# -*- coding: utf-8 -*-
"""
台指期 SMC 銀色子彈 (Silver Bullet) 專業量化策略引擎
作者: Antigravity (Advanced Agentic Pair Programmer)

包含核心功能：
1. SilverBulletConfig: 策略與風控參數配置 dataclass
2. USDaylightSavingDetector: 美股夏冬令時間自適應檢測器
3. SMCFilterPipeline: 多維度過濾管道 (VWAP 籌碼趨勢過濾 + Volume Spike 成交量突波過濾)
4. PendingOrder: 50% CE 限價回測掛單狀態機
5. SilverBulletEngine: SMC 特徵計算、掛單回測撮合、1R 移動保本與回測績效分析
"""

import os
import sys
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import List, Dict, Any, Tuple, Optional

import numpy as np
import pandas as pd


# ==============================================================================
# 1. 美國日光節約時間 (Daylight Saving Time, DST) 檢測器
# ==============================================================================
class USDaylightSavingDetector:
    """
    美國日光節約時間檢測器。
    規則：
    - 夏令時間 (Daylight Saving Time, EDT, UTC-4)：每年 3 月第二個週日 02:00 ~ 11 月第一個週日 02:00。
      美股現貨開盤為台灣時間 (UTC+8) 21:30。
    - 冬令時間 (Standard Time, EST, UTC-5)：每年 11 月第一個週日 02:00 ~ 隔年 3 月第二個週日 02:00。
      美股現貨開盤為台灣時間 (UTC+8) 22:30。
    """
    @staticmethod
    def is_daylight_saving(dt: datetime) -> bool:
        year = dt.year
        # 3 月第二個週日
        march_1 = datetime(year, 3, 1)
        first_sun_march = 1 + (6 - march_1.weekday()) % 7
        second_sun_march = first_sun_march + 7
        dst_start_date = datetime(year, 3, second_sun_march).date()

        # 11 月第一個週日
        nov_1 = datetime(year, 11, 1)
        first_sun_nov = 1 + (6 - nov_1.weekday()) % 7
        dst_end_date = datetime(year, 11, first_sun_nov).date()

        # 判斷日期區間
        target_date = dt.date()
        return dst_start_date <= target_date < dst_end_date


# ==============================================================================
# 2. 策略參數與配置 (SilverBulletConfig)
# ==============================================================================
@dataclass
class SilverBulletConfig:
    """SMC 銀色子彈策略設定"""
    # 標的與資金
    contract_type: str = "MTX"       # "MTX" (小台指) 或 "TX" (大台指)
    start_capital: float = 1_000_000.0
    risk_pct: float = 0.01           # 單筆承受風險 1%
    point_value: float = 50.0        # 小台 50, 大台 200
    fee_per_side: float = 20.0       # 小台單邊 20 (雙邊 40), 大台單邊 50 (雙邊 100)
    tax_rate: float = 0.00002        # 期交稅 0.002%

    # 時段與 Killzone
    enable_dst_detection: bool = True
    day_killzone_start: time = field(default_factory=lambda: time(9, 0))
    day_killzone_end: time = field(default_factory=lambda: time(10, 0))
    night_dst_start: time = field(default_factory=lambda: time(21, 30))
    night_dst_end: time = field(default_factory=lambda: time(22, 30))
    night_std_start: time = field(default_factory=lambda: time(22, 30))
    night_std_end: time = field(default_factory=lambda: time(23, 30))

    # SMC 訊號閥值
    pivot_window: int = 5
    htf_sweep_lookback: int = 30     # 5K Sweep 回溯棒數
    ltf_sweep_lookback: int = 5      # 1K Sweep 回溯棒數

    # 過濾器管道配置
    enable_vwap: bool = True         # VWAP 籌碼方向過濾 (多 >= VWAP, 空 <= VWAP)
    enable_volume_spike: bool = True # 成交量突波過濾
    vol_spike_ratio: float = 1.3     # 位移棒量能 >= 均量 1.3 倍
    vol_ma_period: int = 20          # 量能均線週期

    # 進場與訂單撮合 (Order Execution)
    entry_mode: str = "limit_retest" # "limit_retest" (50% CE 掛單回測) 或 "market_close" (突破收盤市價)
    fvg_entry_level: str = "ce_50"   # "ce_50" (50% 中軸) 或 "edge" (缺口外沿)
    max_wait_bars: int = 5           # 限價掛單最大等待回測根數 (超過撤單)

    # 停損與風控 (Stop Loss)
    sl_mode: str = "swing_extreme"   # "swing_extreme" (結構波段極值) 或 "bar_extreme" (K棒極值)
    sl_buffer_points: float = 3.0    # 停損緩衝點數
    min_sl_points: float = 15.0      # 最低保底停損點數

    # 停利與動態保本 (Take Profit & Breakeven)
    tp_mode: str = "opposite_swing"  # "opposite_swing" (對側波段高低點) 或 "fixed_rr" (固定盈虧比)
    rr_ratio: float = 2.0            # 固定或保底盈虧比 (預設 2.0R)
    min_rr_filter: float = 1.0       # 進場前預期 RR 必須 >= 1.0
    enable_breakeven: bool = True    # 是否啟用 1R 自動移保本
    be_trigger_r: float = 1.0        # 浮盈達 1.0R 時觸發移保本

    def __post_init__(self):
        if self.contract_type == "TX":
            self.point_value = 200.0
            self.fee_per_side = 50.0
        else:
            self.point_value = 50.0
            self.fee_per_side = 20.0


# ==============================================================================
# 3. SMC 多維度過濾管道 (SMCFilterPipeline)
# ==============================================================================
@dataclass
class SMCFilterResult:
    """過濾器檢驗結果"""
    passed: bool
    reasons: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


class BaseSMCFilter:
    """過濾器基類"""
    def __init__(self, config: SilverBulletConfig):
        self.config = config

    def check(self, context: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        raise NotImplementedError


class SMCVWAPFilter(BaseSMCFilter):
    """
    VWAP 籌碼趨勢過濾器：
    - 多頭：突破 K 棒收盤價必須高於當日 VWAP (c_cur >= vwap)
    - 空頭：突破 K 棒收盤價必須低於當日 VWAP (c_cur <= vwap)
    """
    def check(self, context: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        if not self.config.enable_vwap:
            return True, None, {"status": "disabled"}

        price = context.get("current_price", 0.0)
        vwap = context.get("vwap", 0.0)
        side = context.get("side", 1)  # 1 for Long, -1 for Short

        if vwap <= 0 or price <= 0:
            return True, None, {"status": "skipped_no_vwap"}

        detail = {"price": price, "vwap": vwap, "side": side}

        if side == 1:
            if price >= vwap:
                return True, None, detail
            else:
                reason = f"VWAP: 逆勢多單 (價格 {price:.1f} < VWAP {vwap:.1f})"
                return False, reason, detail
        else:
            if price <= vwap:
                return True, None, detail
            else:
                reason = f"VWAP: 逆勢空單 (價格 {price:.1f} > VWAP {vwap:.1f})"
                return False, reason, detail


class SMCVolumeSpikeFilter(BaseSMCFilter):
    """
    成交量突波過濾器：
    位移突破棒的成交量必須大於前 N 根均量的指定倍數 (預設 1.3x)。
    """
    def check(self, context: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        if not self.config.enable_volume_spike:
            return True, None, {"status": "disabled"}

        volume = context.get("current_volume", 0)
        vol_ma = context.get("vol_ma", 0.0)

        if vol_ma <= 0:
            return True, None, {"status": "skipped_insufficient_vol_data"}

        ratio = volume / vol_ma
        required = self.config.vol_spike_ratio
        detail = {"volume": volume, "vol_ma": vol_ma, "ratio": ratio, "required": required}

        if ratio >= required:
            return True, None, detail
        else:
            reason = f"VolumeSpike: 量能不足 ({ratio:.2f}x < 門檻 {required:.2f}x)"
            return False, reason, detail


class SMCFilterPipeline:
    """SMC 過濾器管道組合"""
    def __init__(self, config: SilverBulletConfig):
        self.config = config
        self.filters: List[BaseSMCFilter] = [
            SMCVWAPFilter(config),
            SMCVolumeSpikeFilter(config)
        ]

    def evaluate(self, context: Dict[str, Any]) -> SMCFilterResult:
        reasons = []
        details = {}
        all_passed = True

        for flt in self.filters:
            passed, reason, detail = flt.check(context)
            filter_name = flt.__class__.__name__
            details[filter_name] = detail
            if not passed:
                all_passed = False
                if reason:
                    reasons.append(reason)

        return SMCFilterResult(passed=all_passed, reasons=reasons, details=details)


# ==============================================================================
# 4. 限價掛單 (PendingOrder) 狀態資料結構
# ==============================================================================
@dataclass
class PendingOrder:
    """FVG 限價回測掛單"""
    side: int                   # 1 (多單) 或 -1 (空單)
    limit_price: float          # 掛單成交價 (如 50% CE)
    stop_loss: float            # 預設停損價
    take_profit: float          # 預設停利價
    risk_points: float          # 風險點數
    lots: int                   # 口數
    placed_bar_idx: int         # 掛單時的 K 棒索引
    placed_time: pd.Timestamp   # 掛單時間
    expires_bar_idx: int        # 最晚有效 K 棒索引
    indicators: Dict[str, Any]  # 指標紀錄


# ==============================================================================
# 5. SMC 專業量化策略引擎 (SilverBulletEngine)
# ==============================================================================
class SilverBulletEngine:
    """
    台指期銀色子彈 (Silver Bullet) 專業策略引擎。
    """
    def __init__(self, df_1k: pd.DataFrame, df_5k: pd.DataFrame, config: Optional[SilverBulletConfig] = None):
        self.config = config or SilverBulletConfig()
        self.df_1k_raw = df_1k.copy()
        self.df_5k_raw = df_5k.copy()
        self.df: pd.DataFrame = pd.DataFrame()
        self.pipeline = SMCFilterPipeline(self.config)

    # --------------------------------------------------------------------------
    # 5.1 指標計算與特徵工程
    # --------------------------------------------------------------------------
    @staticmethod
    def calculate_pivots(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
        """計算波段高低點，使用延遲確認徹底消除未來函數 (Look-ahead bias)"""
        highs = df['high'].values
        lows = df['low'].values
        n = len(df)

        pivot_h = np.zeros(n, dtype=bool)
        pivot_l = np.zeros(n, dtype=bool)

        for i in range(window, n - window):
            is_h = True
            is_l = True
            for w in range(1, window + 1):
                if highs[i] < highs[i - w] or highs[i] < highs[i + w]:
                    is_h = False
                if lows[i] > lows[i - w] or lows[i] > lows[i + w]:
                    is_l = False
            if is_h:
                pivot_h[i] = True
            if is_l:
                pivot_l[i] = True

        df['pivot_h'] = pivot_h
        df['pivot_l'] = pivot_l

        confirmed_pivot_h = np.full(n, np.nan)
        confirmed_pivot_l = np.full(n, np.nan)

        for i in range(n):
            if pivot_h[i] and i + window < n:
                confirmed_pivot_h[i + window] = highs[i]
            if pivot_l[i] and i + window < n:
                confirmed_pivot_l[i + window] = lows[i]

        df['last_pivot_h'] = pd.Series(confirmed_pivot_h, index=df.index).ffill()
        df['last_pivot_l'] = pd.Series(confirmed_pivot_l, index=df.index).ffill()
        return df

    @classmethod
    def calculate_htf_5k(cls, df_5k: pd.DataFrame, window: int = 5) -> pd.DataFrame:
        """計算 5K (HTF) 大週期的波段點、Sweep 與 Order Block (OB)"""
        df = df_5k.copy()
        df = cls.calculate_pivots(df, window=window)

        # 5K Sweep
        sweep_l = (df['low'] < df['last_pivot_l'].shift(1)) & (df['close'] > df['last_pivot_l'].shift(1))
        sweep_h = (df['high'] > df['last_pivot_h'].shift(1)) & (df['close'] < df['last_pivot_h'].shift(1))
        df['sweep_low'] = sweep_l
        df['sweep_high'] = sweep_h

        # 5K Order Block (OB)
        n = len(df)
        ob_bull_top = np.zeros(n)
        ob_bull_bot = np.zeros(n)
        ob_bear_top = np.zeros(n)
        ob_bear_bot = np.zeros(n)

        for i in range(1, n):
            if sweep_l.iloc[i]:
                for offset in range(1, 6):
                    idx = i - offset
                    if idx >= 0 and df['close'].iloc[idx] < df['open'].iloc[idx]:
                        ob_bull_top[i] = df['high'].iloc[idx]
                        ob_bull_bot[i] = df['low'].iloc[idx]
                        break
            if sweep_h.iloc[i]:
                for offset in range(1, 6):
                    idx = i - offset
                    if idx >= 0 and df['close'].iloc[idx] > df['open'].iloc[idx]:
                        ob_bear_top[i] = df['high'].iloc[idx]
                        ob_bear_bot[i] = df['low'].iloc[idx]
                        break

        df['ob_bullish_top'] = np.where(ob_bull_top > 0, ob_bull_top, np.nan)
        df['ob_bullish_bottom'] = np.where(ob_bull_bot > 0, ob_bull_bot, np.nan)
        df['ob_bearish_top'] = np.where(ob_bear_top > 0, ob_bear_top, np.nan)
        df['ob_bearish_bottom'] = np.where(ob_bear_bot > 0, ob_bear_bot, np.nan)
        return df

    def prepare_data(self) -> pd.DataFrame:
        """對齊 5K 與 1K，計算 VWAP、Volume MA 與 1K FVG/Sweep 特徵"""
        df_5k_analyzed = self.calculate_htf_5k(self.df_5k_raw, window=self.config.pivot_window)

        # 整理 5K 需合併欄位
        htf_cols = [
            'datetime', 'sweep_low', 'sweep_high', 'last_pivot_h', 'last_pivot_l',
            'ob_bullish_top', 'ob_bullish_bottom', 'ob_bearish_top', 'ob_bearish_bottom'
        ]
        df_5k_sub = df_5k_analyzed[htf_cols].copy()
        df_5k_sub.columns = ['datetime'] + ['htf_' + c for c in htf_cols[1:]]

        # 依收盤時間向後對齊 (backward direction)
        df_1k_sorted = self.df_1k_raw.sort_values('datetime').reset_index(drop=True)
        df_5k_sorted = df_5k_sub.sort_values('datetime').reset_index(drop=True)

        merged = pd.merge_asof(
            df_1k_sorted,
            df_5k_sorted,
            on='datetime',
            direction='backward'
        )

        # 計算 1K 波段高低點
        merged = self.calculate_pivots(merged, window=self.config.pivot_window)

        # 計算 1K FVG
        fvg_bull = (merged['low'] > merged['high'].shift(2)) & (merged['close'] > merged['open'])
        fvg_bear = (merged['high'] < merged['low'].shift(2)) & (merged['close'] < merged['open'])
        merged['ltf_fvg_bullish'] = fvg_bull
        merged['ltf_fvg_bearish'] = fvg_bear
        merged['ltf_fvg_bull_top'] = np.where(fvg_bull, merged['low'], np.nan)
        merged['ltf_fvg_bull_bottom'] = np.where(fvg_bull, merged['high'].shift(2), np.nan)
        merged['ltf_fvg_bear_top'] = np.where(fvg_bear, merged['low'].shift(2), np.nan)
        merged['ltf_fvg_bear_bottom'] = np.where(fvg_bear, merged['high'], np.nan)

        # 計算 1K Sweep
        merged['ltf_sweep_l'] = (merged['low'] < merged['last_pivot_l'].shift(1)) & (merged['close'] > merged['last_pivot_l'].shift(1))
        merged['ltf_sweep_h'] = (merged['high'] > merged['last_pivot_h'].shift(1)) & (merged['close'] < merged['last_pivot_h'].shift(1))

        # 計算成交量均線 (Volume MA)
        merged['vol_ma'] = merged['volume'].rolling(self.config.vol_ma_period, min_periods=1).mean()

        # 計算當盤 VWAP (每盤獨立重置)
        # 台指期盤別劃分：日盤 (08:45-13:45), 夜盤 (15:00-05:00)
        dt_series = pd.to_datetime(merged['datetime'])
        sessions = []
        session_groups = []
        curr_group = 0
        prev_sess = None

        for dt in dt_series:
            t = dt.time()
            if time(8, 45) <= t <= time(13, 45):
                sess = 'day'
            else:
                sess = 'night'
            if sess != prev_sess:
                curr_group += 1
                prev_sess = sess
            sessions.append(sess)
            session_groups.append(curr_group)

        merged['session'] = sessions
        merged['session_group'] = session_groups

        # 典型價格與 VWAP
        typical_price = (merged['high'] + merged['low'] + merged['close']) / 3.0
        cum_vol = merged.groupby('session_group')['volume'].cumsum()
        cum_pv = (typical_price * merged['volume']).groupby(merged['session_group']).cumsum()
        merged['vwap'] = np.where(cum_vol > 0, cum_pv / cum_vol, merged['close'])

        self.df = merged
        return merged

    # --------------------------------------------------------------------------
    # 5.2 時段檢測輔助
    # --------------------------------------------------------------------------
    def is_in_killzone(self, dt: pd.Timestamp) -> Tuple[bool, str]:
        """判斷是否在台指銀色子彈 Killzone 範圍內"""
        py_dt = dt.to_pydatetime()
        t = dt.time()

        # 日盤 09:00 - 10:00 (現貨主力開盤黃金時段)
        if self.config.day_killzone_start <= t <= self.config.day_killzone_end:
            return True, "day_killzone"

        # 夜盤 (依夏冬令時自適應)
        if self.config.enable_dst_detection:
            is_dst = USDaylightSavingDetector.is_daylight_saving(py_dt)
            if is_dst:
                # 美國夏令時: 21:30 - 22:30
                if self.config.night_dst_start <= t <= self.config.night_dst_end:
                    return True, "night_dst_killzone"
            else:
                # 美國冬令時: 22:30 - 23:30
                if self.config.night_std_start <= t <= self.config.night_std_end:
                    return True, "night_std_killzone"
        else:
            # 預設固定窗口
            if self.config.night_dst_start <= t <= self.config.night_dst_end:
                return True, "night_fixed_killzone"

        return False, "outside_killzone"

    # --------------------------------------------------------------------------
    # 5.3 交易成本計算
    # --------------------------------------------------------------------------
    def calculate_costs(self, entry_price: float, exit_price: float, lots: int) -> float:
        total_fee = self.config.fee_per_side * 2 * lots
        tax_entry = round(entry_price * self.config.point_value * self.config.tax_rate * lots)
        tax_exit = round(exit_price * self.config.point_value * self.config.tax_rate * lots)
        return total_fee + tax_entry + tax_exit

    # --------------------------------------------------------------------------
    # 5.4 完整事件驅動回測 (Event-Driven Backtest Loop)
    # --------------------------------------------------------------------------
    def run_backtest(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        if self.df.empty:
            self.prepare_data()

        df = self.df
        df_len = len(df)
        if df_len == 0:
            return {}, [], []

        # 預先取得 numpy 陣列加速迴圈
        times = df['datetime'].values
        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values
        opens = df['open'].values
        volumes = df['volume'].values
        vol_mas = df['vol_ma'].values
        vwaps = df['vwap'].values
        sessions = df['session'].values

        htf_sweep_l = df['htf_sweep_low'].values
        htf_sweep_h = df['htf_sweep_high'].values
        htf_ob_b_bot = df['htf_ob_bullish_bottom'].values
        htf_ob_s_top = df['htf_ob_bearish_top'].values
        htf_last_piv_h = df['htf_last_pivot_h'].values
        htf_last_piv_l = df['htf_last_pivot_l'].values

        ltf_fvg_b = df['ltf_fvg_bullish'].values
        ltf_fvg_s = df['ltf_fvg_bearish'].values
        ltf_fvg_b_top = df['ltf_fvg_bull_top'].values
        ltf_fvg_b_bot = df['ltf_fvg_bull_bottom'].values
        ltf_fvg_s_top = df['ltf_fvg_bear_top'].values
        ltf_fvg_s_bot = df['ltf_fvg_bear_bottom'].values
        ltf_sweep_l = df['ltf_sweep_l'].values
        ltf_sweep_h = df['ltf_sweep_h'].values

        capital = self.config.start_capital
        equity_curve = [{'time': str(times[0]), 'equity': capital}]
        trades = []
        filtered_events = []

        position = 0               # 1 (多), -1 (空), 0 (空手)
        entry_price = 0.0
        entry_time = None
        stop_loss = 0.0
        take_profit = 0.0
        risk_points = 0.0
        lots = 0
        is_be_moved = False
        current_entry_indicators = {}

        pending_order: Optional[PendingOrder] = None

        for i in range(1, df_len):
            t_cur = pd.Timestamp(times[i])
            c_cur = closes[i]
            h_cur = highs[i]
            l_cur = lows[i]
            o_cur = opens[i]

            curr_sess = sessions[i]
            prev_sess = sessions[i - 1] if i > 0 else curr_sess

            # ------------------------------------------------------------------
            # A. 持倉中處理 (止損、保本、止盈、當盤收盤強制離場)
            # ------------------------------------------------------------------
            if position != 0:
                triggered = False
                exit_reason = ""
                exit_price_actual = 0.0

                if position == 1:
                    # 1R 動態移保本檢查
                    if self.config.enable_breakeven and not is_be_moved:
                        if h_cur >= entry_price + risk_points * self.config.be_trigger_r:
                            stop_loss = max(stop_loss, entry_price)
                            is_be_moved = True

                    # 止損與止盈撮合
                    if l_cur <= stop_loss:
                        triggered = True
                        exit_price_actual = stop_loss
                        exit_reason = "BE (保本出場)" if is_be_moved and abs(exit_price_actual - entry_price) < 0.1 else "SL (止損)"
                    elif h_cur >= take_profit:
                        triggered = True
                        exit_price_actual = take_profit
                        exit_reason = "TP (止盈)"

                elif position == -1:
                    # 1R 動態移保本檢查
                    if self.config.enable_breakeven and not is_be_moved:
                        if l_cur <= entry_price - risk_points * self.config.be_trigger_r:
                            stop_loss = min(stop_loss, entry_price)
                            is_be_moved = True

                    # 止損與止盈撮合
                    if h_cur >= stop_loss:
                        triggered = True
                        exit_price_actual = stop_loss
                        exit_reason = "BE (保本出場)" if is_be_moved and abs(exit_price_actual - entry_price) < 0.1 else "SL (止損)"
                    elif l_cur <= take_profit:
                        triggered = True
                        exit_price_actual = take_profit
                        exit_reason = "TP (止盈)"

                # 當盤結束強制平倉 (日盤收盤或夜盤收盤)
                if not triggered:
                    if (prev_sess == 'day' and curr_sess == 'night') or (prev_sess == 'night' and curr_sess == 'day'):
                        triggered = True
                        exit_price_actual = closes[i - 1]
                        exit_reason = "EOD (收盤平倉)"

                if triggered:
                    gross_pnl = (exit_price_actual - entry_price) * position * lots * self.config.point_value
                    costs = self.calculate_costs(entry_price, exit_price_actual, lots)
                    net_pnl = gross_pnl - costs
                    capital += net_pnl

                    trades.append({
                        'trade_no': len(trades) + 1,
                        'strategy': 'silver_bullet',
                        'side': 'BUY' if position == 1 else 'SELL',
                        'entry_time': str(entry_time),
                        'entry_price': float(entry_price),
                        'exit_time': str(t_cur),
                        'exit_price': float(exit_price_actual),
                        'exit_reason': exit_reason,
                        'lots': int(lots),
                        'risk_points': float(risk_points),
                        'pnl_points': float((exit_price_actual - entry_price) * position),
                        'gross_pnl': float(gross_pnl),
                        'costs': float(costs),
                        'net_pnl': float(net_pnl),
                        'capital_after': float(capital),
                        'is_breakeven_triggered': is_be_moved,
                        'indicators': current_entry_indicators
                    })

                    equity_curve.append({'time': str(t_cur), 'equity': capital})
                    position = 0
                    pending_order = None

            # ------------------------------------------------------------------
            # B. 檢查限價掛單回測成交 (Pending Order Check)
            # ------------------------------------------------------------------
            if position == 0 and pending_order is not None:
                in_kz, _ = self.is_in_killzone(t_cur)
                # 逾期撤單、盤別切換或脫離 Killzone
                if i > pending_order.expires_bar_idx or curr_sess != prev_sess or not in_kz:
                    pending_order = None
                else:
                    filled = False
                    if pending_order.side == 1:
                        # 多單限價買進：當根最低價 <= 掛單價即視為回測成交
                        if l_cur <= pending_order.limit_price:
                            filled = True
                    elif pending_order.side == -1:
                        # 空單限價賣出：當根最高價 >= 掛單價即視為回測成交
                        if h_cur >= pending_order.limit_price:
                            filled = True

                    if filled:
                        position = pending_order.side
                        entry_price = pending_order.limit_price
                        entry_time = t_cur
                        stop_loss = pending_order.stop_loss
                        take_profit = pending_order.take_profit
                        risk_points = pending_order.risk_points
                        lots = pending_order.lots
                        is_be_moved = False
                        current_entry_indicators = pending_order.indicators
                        pending_order = None

            # ------------------------------------------------------------------
            # C. 尋找全新銀色子彈進場機會 (Signal Generation)
            # ------------------------------------------------------------------
            if position == 0 and pending_order is None:
                in_killzone, kz_tag = self.is_in_killzone(t_cur)

                if in_killzone:
                    # 1. 大週期 (5K HTF) 共振條件
                    lookback_htf = self.config.htf_sweep_lookback
                    start_htf = max(0, i - lookback_htf)
                    htf_bull_align = (not np.isnan(htf_ob_b_bot[i]) and c_cur >= htf_ob_b_bot[i]) or any(htf_sweep_l[start_htf:i + 1])
                    htf_bear_align = (not np.isnan(htf_ob_s_top[i]) and c_cur <= htf_ob_s_top[i]) or any(htf_sweep_h[start_htf:i + 1])

                    # 2. 小週期 (1K LTF) Sweep 條件
                    lookback_ltf = self.config.ltf_sweep_lookback
                    start_ltf = max(0, i - lookback_ltf)
                    sweep_l_recent = any(ltf_sweep_l[start_ltf:i + 1])
                    sweep_h_recent = any(ltf_sweep_h[start_ltf:i + 1])

                    # --- 多頭信號偵測 ---
                    if htf_bull_align and sweep_l_recent and ltf_fvg_b[i]:
                        context = {
                            "current_price": c_cur,
                            "vwap": vwaps[i],
                            "side": 1,
                            "current_volume": volumes[i],
                            "vol_ma": vol_mas[i]
                        }
                        filter_res = self.pipeline.evaluate(context)

                        if filter_res.passed:
                            fvg_top = ltf_fvg_b_top[i]
                            fvg_bot = ltf_fvg_b_bot[i]
                            entry_pr = (fvg_top + fvg_bot) / 2.0 if self.config.fvg_entry_level == "ce_50" else fvg_top

                            # 結構極值停損：尋找引發 Sweep 的局部最低點
                            if self.config.sl_mode == "swing_extreme":
                                structural_low = min(lows[start_ltf:i + 1])
                                sl_price = structural_low - self.config.sl_buffer_points
                            else:
                                sl_price = l_cur - self.config.sl_buffer_points

                            calc_risk = max(self.config.min_sl_points, entry_pr - sl_price)
                            sl_price = entry_pr - calc_risk

                            # 停利目標
                            if self.config.tp_mode == "opposite_swing":
                                tp_price = htf_last_piv_h[i]
                                if np.isnan(tp_price) or tp_price <= entry_pr or (tp_price - entry_pr) / calc_risk < self.config.min_rr_filter:
                                    tp_price = entry_pr + calc_risk * self.config.rr_ratio
                            else:
                                tp_price = entry_pr + calc_risk * self.config.rr_ratio

                            rr = (tp_price - entry_pr) / calc_risk
                            if rr >= self.config.min_rr_filter:
                                risk_cash = capital * self.config.risk_pct
                                lots_to_trade = max(1, int(risk_cash / (calc_risk * self.config.point_value)))

                                indicators = {
                                    'killzone': kz_tag,
                                    'vwap': float(vwaps[i]),
                                    'vol_ratio': float(volumes[i] / vol_mas[i]) if vol_mas[i] > 0 else 1.0,
                                    'fvg_zone': f"{round(fvg_bot, 1)} - {round(fvg_top, 1)}",
                                    'rr_est': round(rr, 2),
                                    'sl_mode': self.config.sl_mode
                                }

                                if self.config.entry_mode == "limit_retest":
                                    # 限價掛單等待回測
                                    pending_order = PendingOrder(
                                        side=1,
                                        limit_price=entry_pr,
                                        stop_loss=sl_price,
                                        take_profit=tp_price,
                                        risk_points=calc_risk,
                                        lots=lots_to_trade,
                                        placed_bar_idx=i,
                                        placed_time=t_cur,
                                        expires_bar_idx=i + self.config.max_wait_bars,
                                        indicators=indicators
                                    )
                                else:
                                    # 市價立即進場 (收盤價)
                                    position = 1
                                    entry_price = c_cur
                                    entry_time = t_cur
                                    stop_loss = sl_price
                                    take_profit = tp_price
                                    risk_points = calc_risk
                                    lots = lots_to_trade
                                    is_be_moved = False
                                    current_entry_indicators = indicators
                        else:
                            filtered_events.append({
                                'time': str(t_cur),
                                'side': 'BUY',
                                'reasons': filter_res.reasons,
                                'details': filter_res.details
                            })

                    # --- 空頭信號偵測 ---
                    elif htf_bear_align and sweep_h_recent and ltf_fvg_s[i]:
                        context = {
                            "current_price": c_cur,
                            "vwap": vwaps[i],
                            "side": -1,
                            "current_volume": volumes[i],
                            "vol_ma": vol_mas[i]
                        }
                        filter_res = self.pipeline.evaluate(context)

                        if filter_res.passed:
                            fvg_top = ltf_fvg_s_top[i]
                            fvg_bot = ltf_fvg_s_bot[i]
                            entry_pr = (fvg_top + fvg_bot) / 2.0 if self.config.fvg_entry_level == "ce_50" else fvg_bot

                            # 結構極值停損：尋找引發 Sweep 的局部最高點
                            if self.config.sl_mode == "swing_extreme":
                                structural_high = max(highs[start_ltf:i + 1])
                                sl_price = structural_high + self.config.sl_buffer_points
                            else:
                                sl_price = h_cur + self.config.sl_buffer_points

                            calc_risk = max(self.config.min_sl_points, sl_price - entry_pr)
                            sl_price = entry_pr + calc_risk

                            # 停利目標
                            if self.config.tp_mode == "opposite_swing":
                                tp_price = htf_last_piv_l[i]
                                if np.isnan(tp_price) or tp_price >= entry_pr or (entry_pr - tp_price) / calc_risk < self.config.min_rr_filter:
                                    tp_price = entry_pr - calc_risk * self.config.rr_ratio
                            else:
                                tp_price = entry_pr - calc_risk * self.config.rr_ratio

                            rr = (entry_pr - tp_price) / calc_risk
                            if rr >= self.config.min_rr_filter:
                                risk_cash = capital * self.config.risk_pct
                                lots_to_trade = max(1, int(risk_cash / (calc_risk * self.config.point_value)))

                                indicators = {
                                    'killzone': kz_tag,
                                    'vwap': float(vwaps[i]),
                                    'vol_ratio': float(volumes[i] / vol_mas[i]) if vol_mas[i] > 0 else 1.0,
                                    'fvg_zone': f"{round(fvg_bot, 1)} - {round(fvg_top, 1)}",
                                    'rr_est': round(rr, 2),
                                    'sl_mode': self.config.sl_mode
                                }

                                if self.config.entry_mode == "limit_retest":
                                    pending_order = PendingOrder(
                                        side=-1,
                                        limit_price=entry_pr,
                                        stop_loss=sl_price,
                                        take_profit=tp_price,
                                        risk_points=calc_risk,
                                        lots=lots_to_trade,
                                        placed_bar_idx=i,
                                        placed_time=t_cur,
                                        expires_bar_idx=i + self.config.max_wait_bars,
                                        indicators=indicators
                                    )
                                else:
                                    position = -1
                                    entry_price = c_cur
                                    entry_time = t_cur
                                    stop_loss = sl_price
                                    take_profit = tp_price
                                    risk_points = calc_risk
                                    lots = lots_to_trade
                                    is_be_moved = False
                                    current_entry_indicators = indicators
                        else:
                            filtered_events.append({
                                'time': str(t_cur),
                                'side': 'SELL',
                                'reasons': filter_res.reasons,
                                'details': filter_res.details
                            })

        # ----------------------------------------------------------------------
        # D. 統計績效指標 (Performance Metrics)
        # ----------------------------------------------------------------------
        total_trades = len(trades)
        if total_trades > 0:
            winning_trades = [t for t in trades if t['net_pnl'] > 0]
            losing_trades = [t for t in trades if t['net_pnl'] < 0]
            be_trades = [t for t in trades if t['exit_reason'] == "BE (保本出場)"]

            win_rate = len(winning_trades) / total_trades
            gross_profit = sum(t['net_pnl'] for t in winning_trades)
            gross_loss = abs(sum(t['net_pnl'] for t in losing_trades))
            profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

            # 計算最大回撤 (Max Drawdown)
            eq_values = [e['equity'] for e in equity_curve]
            running_max = np.maximum.accumulate(eq_values)
            drawdowns = (running_max - eq_values) / running_max
            mdd_pct = float(np.max(drawdowns)) * 100.0 if len(drawdowns) > 0 else 0.0

            total_return_pct = ((capital - self.config.start_capital) / self.config.start_capital) * 100.0
            avg_win = float(np.mean([t['net_pnl'] for t in winning_trades])) if winning_trades else 0.0
            avg_loss = float(np.mean([t['net_pnl'] for t in losing_trades])) if losing_trades else 0.0

            summary = {
                'total_trades': total_trades,
                'winning_trades': len(winning_trades),
                'losing_trades': len(losing_trades),
                'be_trades': len(be_trades),
                'win_rate': round(win_rate * 100.0, 2),
                'profit_factor': profit_factor,
                'max_drawdown_pct': round(mdd_pct, 2),
                'total_return_pct': round(total_return_pct, 2),
                'net_profit': round(capital - self.config.start_capital, 1),
                'ending_capital': round(capital, 1),
                'avg_win': round(avg_win, 1),
                'avg_loss': round(avg_loss, 1),
                'filtered_count': len(filtered_events)
            }
        else:
            summary = {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'be_trades': 0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'max_drawdown_pct': 0.0,
                'total_return_pct': 0.0,
                'net_profit': 0.0,
                'ending_capital': self.config.start_capital,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'filtered_count': len(filtered_events)
            }

        return summary, trades, equity_curve
