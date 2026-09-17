# -*- coding: utf-8 -*-
"""
台指期 SMC 流動性獵取假突破反轉策略 (Liquidity Sweep Fade / Turtle Soup)
作者: Antigravity (Advanced Agentic Pair Programmer)

核心架構：
1. SweepFadeConfig: 策略參數配置 dataclass
2. LiquidityPoolTracker: 動態追蹤 PDH/PDL、ORB 15m 高低點、5K 波段高低點與當盤 VWAP
3. SweepFadeEngine: 事件驅動回測引擎，支援二階段階梯停利 (TP1 平半倉移保本，TP2 瞄準對側池) 與強單邊趨勢保護
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
# 1. 策略參數配置 (SweepFadeConfig)
# ==============================================================================
@dataclass
class SweepFadeConfig:
    """SMC 假突破反轉策略設定"""
    # 標的與資金
    contract_type: str = "MTX"       # "MTX" (小台指) 或 "TX" (大台指)
    start_capital: float = 1_000_000.0
    risk_pct: float = 0.01           # 單筆最大風險比例 1%
    point_value: float = 50.0        # 小台 50, 大台 200
    fee_per_side: float = 20.0       # 小台單邊 20 (雙邊 40), 大台單邊 50 (雙邊 100)
    tax_rate: float = 0.00002        # 期交稅 0.002%
    min_lots: int = 2                # 預設最低交易 2 口 (便於 TP1/TP2 50% 分批結清)

    # 監控的流動性池開關
    enable_pdh_pdl: bool = True      # 監控前日高低點 (PDH/PDL)
    enable_orb_pools: bool = True    # 監控開盤 15m ORB 高低點 (08:45-09:00)
    enable_htf_swings: bool = False  # 監控 5K 大週期波段極值 (5K Pivots，高雜訊盤建議關閉)
    orb_probe_minutes: int = 15      # 開盤區間計算時間 (15 分鐘)

    # 假突破與影線認定條件
    min_penetration: float = 3.0     # 最少刺穿點數 (排除微幅平水跳動)
    max_penetration: float = 35.0    # 最大刺穿點數 (超過 35 點視為強單邊真突破，嚴禁逆勢接刀)
    min_wick_ratio: float = 0.40     # 拒絕影線佔整根 K 棒的最低比例 (如上影線 >= 40%)
    require_wick_gte_body: bool = True # 拒絕影線長度是否必須大於或等於實體長度

    # 趨勢與動態 ATR 濾網 (HTF 5K Trend & ATR Protection)
    enable_trend_filter: bool = True # 啟用 5K EMA 趨勢結構濾網 (多頭只做多 Sweep，空頭只做空 Sweep)
    ema_fast: int = 9                # 5K 快速 EMA (9 週期，靈敏捕捉日內波段方向)
    ema_slow: int = 21               # 5K 慢速 EMA (21 週期)
    enable_dynamic_atr: bool = True  # 啟用 5K ATR 動態刺穿防護
    atr_period: int = 14
    min_pen_atr_mult: float = 0.08   # 動態最小刺穿比例 (0.08 * ATR_5K)
    max_pen_atr_mult: float = 0.60   # 動態最大刺穿比例 (0.60 * ATR_5K)

    # 停損與保護 (Stop Loss)
    sl_buffer_points: float = 3.0    # 停損設於 Sweep 影線極值外側加緩衝點數
    min_sl_points: float = 25.0      # 最低保底停損點數 (考量台指 1K 平均波動 25 點)
    max_attempts_per_session: int = 2 # 每個時段同一個方向最多嘗試次數 (防止單邊盤連續逆勢)

    # 時段過濾 (黃金交易時段：避開 09:00 開盤震盪、16-18 低流動性；鎖定 10-11 盤中衰竭與夜盤歐美關鍵窗口)
    allowed_hours: Optional[List[int]] = field(default_factory=lambda: [10, 11, 15, 19, 20, 21, 4])

    # 停利機制 (Two-Stage Take Profit)
    tp1_mode: str = "vwap"           # "vwap" (當盤 VWAP 均價線) 或 "fixed_rr" (1.5R)
    tp1_fixed_rr: float = 1.5        # 若無法獲取有效 VWAP 時的保底 TP1 盈虧比
    tp2_mode: str = "opposite_pool"  # "opposite_pool" (對側流動性池) 或 "fixed_rr" (3.0R)
    tp2_fixed_rr: float = 3.0        # 對側目標保底盈虧比 (3.0R)

    def __post_init__(self):
        if self.contract_type == "TX":
            self.point_value = 200.0
            self.fee_per_side = 50.0
        else:
            self.point_value = 50.0
            self.fee_per_side = 20.0


# ==============================================================================
# 2. 流動性池追蹤器 (LiquidityPoolTracker)
# ==============================================================================
class LiquidityPoolTracker:
    """
    動態計算與維護三大關鍵流動性目標：
    1. 前日高低點 (PDH, PDL)
    2. 開盤前 15 分鐘極值 (ORB High, Low)
    3. 5K 大週期波段極值 (5K Swing High / Low)
    以及當日累計成交量加權平均價 (VWAP)
    """
    @staticmethod
    def calculate_pivots(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
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

        confirmed_h = np.full(n, np.nan)
        confirmed_l = np.full(n, np.nan)

        for i in range(n):
            if pivot_h[i] and i + window < n:
                confirmed_h[i + window] = highs[i]
            if pivot_l[i] and i + window < n:
                confirmed_l[i + window] = lows[i]

        df['last_pivot_h'] = pd.Series(confirmed_h, index=df.index).ffill()
        df['last_pivot_l'] = pd.Series(confirmed_l, index=df.index).ffill()
        return df

    @classmethod
    def prepare_dataset(cls, df_1k: pd.DataFrame, df_5k: pd.DataFrame, config: SweepFadeConfig) -> pd.DataFrame:
        df_1k = df_1k.copy().sort_values('datetime').reset_index(drop=True)
        df_5k = df_5k.copy().sort_values('datetime').reset_index(drop=True)

        # 1. 5K 波段點與指標計算
        df_5k = cls.calculate_pivots(df_5k, window=5)

        # 計算 5K EMA 20, 50 趨勢結構
        df_5k['htf_ema_fast'] = df_5k['close'].ewm(span=config.ema_fast, adjust=False).mean()
        df_5k['htf_ema_slow'] = df_5k['close'].ewm(span=config.ema_slow, adjust=False).mean()

        # 計算 5K ATR 14 動態波動度
        tr1 = df_5k['high'] - df_5k['low']
        tr2 = (df_5k['high'] - df_5k['close'].shift(1)).abs()
        tr3 = (df_5k['low'] - df_5k['close'].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df_5k['htf_atr'] = tr.rolling(window=config.atr_period, min_periods=1).mean()

        df_5k_sub = df_5k[['datetime', 'last_pivot_h', 'last_pivot_l', 'htf_ema_fast', 'htf_ema_slow', 'htf_atr']].copy()

        # 2. 向後對齊 5K 至 1K
        merged = pd.merge_asof(
            df_1k,
            df_5k_sub,
            on='datetime',
            direction='backward'
        )

        # 3. 標記盤別與交易日 (Trade Date)
        dt_series = pd.to_datetime(merged['datetime'])
        sessions = []
        trade_dates = []
        curr_session_group = 0
        prev_sess = None
        session_groups = []

        for dt in dt_series:
            t = dt.time()
            d = dt.date()

            # 日盤: 08:45 - 13:45
            if time(8, 45) <= t <= time(13, 45):
                sess = 'day'
                t_date = d
            else:
                sess = 'night'
                # 凌晨 00:00 - 05:00 歸屬於前一個交易日的夜盤
                if t < time(8, 45):
                    t_date = d - timedelta(days=1)
                else:
                    t_date = d

            if sess != prev_sess:
                curr_session_group += 1
                prev_sess = sess

            sessions.append(sess)
            trade_dates.append(t_date)
            session_groups.append(curr_session_group)

        merged['session'] = sessions
        merged['trade_date'] = trade_dates
        merged['session_group'] = session_groups

        # 4. 計算前日高低點 (PDH / PDL)
        # 以每個 trade_date 的最高點與最低點滾動計算前一交易日高低
        daily_stats = merged.groupby('trade_date').agg({'high': 'max', 'low': 'min'}).reset_index()
        daily_stats['pdh'] = daily_stats['high'].shift(1)
        daily_stats['pdl'] = daily_stats['low'].shift(1)
        daily_stats = daily_stats.drop(columns=['high', 'low'])

        merged = pd.merge(merged, daily_stats, on='trade_date', how='left')

        # 5. 計算當盤累積 VWAP
        tp = (merged['high'] + merged['low'] + merged['close']) / 3.0
        cum_vol = merged.groupby('session_group')['volume'].cumsum()
        cum_pv = (tp * merged['volume']).groupby(merged['session_group']).cumsum()
        merged['vwap'] = np.where(cum_vol > 0, cum_pv / cum_vol, merged['close'])

        # 6. 計算開盤 15m ORB 高低點 (日盤 08:45-09:00)
        orb_highs = np.full(len(merged), np.nan)
        orb_lows = np.full(len(merged), np.nan)

        for s_grp, group in merged.groupby('session_group'):
            # 若為日盤，取前 15 根 (08:45 ~ 09:00)
            if group['session'].iloc[0] == 'day':
                probe_window = group.iloc[:config.orb_probe_minutes]
                if not probe_window.empty:
                    oh = probe_window['high'].max()
                    ol = probe_window['low'].min()
                    idxs = group.index[config.orb_probe_minutes:]
                    orb_highs[idxs] = oh
                    orb_lows[idxs] = ol

        merged['orb_high'] = orb_highs
        merged['orb_low'] = orb_lows

        return merged


# ==============================================================================
# 3. SMC 假突破反轉策略回測核心 (SweepFadeEngine)
# ==============================================================================
class SweepFadeEngine:
    """
    SMC 流動性獵取假突破反轉量化回測引擎。
    """
    def __init__(self, df_1k: pd.DataFrame, df_5k: pd.DataFrame, config: Optional[SweepFadeConfig] = None):
        self.config = config or SweepFadeConfig()
        self.df_1k = df_1k
        self.df_5k = df_5k
        self.df: pd.DataFrame = pd.DataFrame()

    def run_backtest(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        if self.df.empty:
            self.df = LiquidityPoolTracker.prepare_dataset(self.df_1k, self.df_5k, self.config)

        df = self.df
        n = len(df)
        if n == 0:
            return {}, [], []

        # 預加載 numpy 陣列
        times = df['datetime'].values
        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        volumes = df['volume'].values
        vwaps = df['vwap'].values
        pdhs = df['pdh'].values
        pdls = df['pdl'].values
        orb_highs = df['orb_high'].values
        orb_lows = df['orb_low'].values
        htf_piv_h = df['last_pivot_h'].values if 'last_pivot_h' in df else np.zeros(n)
        htf_piv_l = df['last_pivot_l'].values if 'last_pivot_l' in df else np.zeros(n)
        htf_ema_f = df['htf_ema_fast'].values if 'htf_ema_fast' in df else np.zeros(n)
        htf_ema_s = df['htf_ema_slow'].values if 'htf_ema_slow' in df else np.zeros(n)
        htf_atrs = df['htf_atr'].values if 'htf_atr' in df else np.full(n, 25.0)
        sessions = df['session'].values
        session_groups = df['session_group'].values

        capital = self.config.start_capital
        equity_curve = [{'time': str(times[0]), 'equity': capital}]
        trades = []

        # 持倉狀態
        position = 0               # 1 (多), -1 (空), 0 (空手)
        lots_total = 0             # 初始總口數
        lots_remaining = 0         # 當前剩餘口數
        entry_price = 0.0
        entry_time = None
        stop_loss = 0.0
        risk_points = 0.0
        tp1_price = 0.0
        tp2_price = 0.0
        is_tp1_filled = False
        trade_indicators = {}

        # 每個 session_group 的進場次數限制追蹤: {session_group: {'long': count, 'short': count}}
        session_attempts: Dict[int, Dict[str, int]] = {}

        for i in range(1, n):
            t_cur = pd.Timestamp(times[i])
            o_cur = opens[i]
            h_cur = highs[i]
            l_cur = lows[i]
            c_cur = closes[i]

            s_grp = session_groups[i]
            if s_grp not in session_attempts:
                session_attempts[s_grp] = {'long': 0, 'short': 0}

            curr_sess = sessions[i]
            prev_sess = sessions[i - 1] if i > 0 else curr_sess

            # ------------------------------------------------------------------
            # A. 持倉處理 (二階段階梯停利與動態保本)
            # ------------------------------------------------------------------
            if position != 0:
                triggered_exit = False
                exit_price = 0.0
                exit_reason = ""
                exit_lots = 0

                # 1. 盤別結束強制平倉 (收盤離場)
                if curr_sess != prev_sess:
                    triggered_exit = True
                    exit_price = closes[i - 1]
                    exit_reason = "EOD (收盤強平)"
                    exit_lots = lots_remaining

                if not triggered_exit:
                    if position == 1:  # 多單持倉
                        if not is_tp1_filled:
                            # 檢查原始停損
                            if l_cur <= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "SL (止損)"
                                exit_lots = lots_remaining
                            # 檢查 TP1 觸發 (平 50% 倉位並移保本)
                            elif h_cur >= tp1_price:
                                half_lots = math.ceil(lots_total / 2)
                                pnl_pts = tp1_price - entry_price
                                gross_pnl = pnl_pts * half_lots * self.config.point_value
                                costs = self.calculate_costs(entry_price, tp1_price, half_lots)
                                net_pnl = gross_pnl - costs
                                capital += net_pnl

                                trades.append({
                                    'trade_no': len(trades) + 1,
                                    'strategy': 'sweep_fade',
                                    'stage': 'TP1',
                                    'side': 'BUY',
                                    'entry_time': str(entry_time),
                                    'entry_price': float(entry_price),
                                    'exit_time': str(t_cur),
                                    'exit_price': float(tp1_price),
                                    'exit_reason': "TP1 (平半倉移保本)",
                                    'lots': int(half_lots),
                                    'risk_points': float(risk_points),
                                    'pnl_points': float(pnl_pts),
                                    'net_pnl': float(net_pnl),
                                    'capital_after': float(capital),
                                    'indicators': trade_indicators
                                })

                                equity_curve.append({'time': str(t_cur), 'equity': capital})
                                lots_remaining -= half_lots
                                is_tp1_filled = True
                                # 止損立即提升至進場成本價
                                stop_loss = max(stop_loss, entry_price)

                        else:
                            # 已完成 TP1，檢查 TP2 終極停利與保本止損
                            if h_cur >= tp2_price:
                                triggered_exit = True
                                exit_price = tp2_price
                                exit_reason = "TP2 (對側池全平)"
                                exit_lots = lots_remaining
                            elif l_cur <= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "BE (保本出場)" if abs(stop_loss - entry_price) < 0.1 else "SL (止損)"
                                exit_lots = lots_remaining

                    elif position == -1:  # 空單持倉
                        if not is_tp1_filled:
                            # 檢查原始停損
                            if h_cur >= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "SL (止損)"
                                exit_lots = lots_remaining
                            # 檢查 TP1 觸發
                            elif l_cur <= tp1_price:
                                half_lots = math.ceil(lots_total / 2)
                                pnl_pts = entry_price - tp1_price
                                gross_pnl = pnl_pts * half_lots * self.config.point_value
                                costs = self.calculate_costs(entry_price, tp1_price, half_lots)
                                net_pnl = gross_pnl - costs
                                capital += net_pnl

                                trades.append({
                                    'trade_no': len(trades) + 1,
                                    'strategy': 'sweep_fade',
                                    'stage': 'TP1',
                                    'side': 'SELL',
                                    'entry_time': str(entry_time),
                                    'entry_price': float(entry_price),
                                    'exit_time': str(t_cur),
                                    'exit_price': float(tp1_price),
                                    'exit_reason': "TP1 (平半倉移保本)",
                                    'lots': int(half_lots),
                                    'risk_points': float(risk_points),
                                    'pnl_points': float(pnl_pts),
                                    'net_pnl': float(net_pnl),
                                    'capital_after': float(capital),
                                    'indicators': trade_indicators
                                })

                                equity_curve.append({'time': str(t_cur), 'equity': capital})
                                lots_remaining -= half_lots
                                is_tp1_filled = True
                                # 止損立即降低至進場成本價
                                stop_loss = min(stop_loss, entry_price)

                        else:
                            # 已完成 TP1，檢查 TP2 終極停利與保本止損
                            if l_cur <= tp2_price:
                                triggered_exit = True
                                exit_price = tp2_price
                                exit_reason = "TP2 (對側池全平)"
                                exit_lots = lots_remaining
                            elif h_cur >= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "BE (保本出場)" if abs(stop_loss - entry_price) < 0.1 else "SL (止損)"
                                exit_lots = lots_remaining

                if triggered_exit and exit_lots > 0:
                    pnl_pts = (exit_price - entry_price) * position
                    gross_pnl = pnl_pts * exit_lots * self.config.point_value
                    costs = self.calculate_costs(entry_price, exit_price, exit_lots)
                    net_pnl = gross_pnl - costs
                    capital += net_pnl

                    trades.append({
                        'trade_no': len(trades) + 1,
                        'strategy': 'sweep_fade',
                        'stage': 'TP2' if "TP2" in exit_reason else ('FINAL_BE' if 'BE' in exit_reason else 'FINAL_SL'),
                        'side': 'BUY' if position == 1 else 'SELL',
                        'entry_time': str(entry_time),
                        'entry_price': float(entry_price),
                        'exit_time': str(t_cur),
                        'exit_price': float(exit_price),
                        'exit_reason': exit_reason,
                        'lots': int(exit_lots),
                        'risk_points': float(risk_points),
                        'pnl_points': float(pnl_pts),
                        'net_pnl': float(net_pnl),
                        'capital_after': float(capital),
                        'indicators': trade_indicators
                    })

                    equity_curve.append({'time': str(t_cur), 'equity': capital})
                    position = 0
                    lots_remaining = 0

            # ------------------------------------------------------------------
            # B. 尋找 SMC 假突破反轉信號 (Signal Generation)
            # ------------------------------------------------------------------
            if position == 0:
                # 檢查是否在允許的黃金交易時段
                if self.config.allowed_hours is not None and t_cur.hour not in self.config.allowed_hours:
                    continue

                bar_range = max(1.0, h_cur - l_cur)
                body = abs(c_cur - o_cur)
                upper_wick = h_cur - max(o_cur, c_cur)
                lower_wick = min(o_cur, c_cur) - l_cur

                # 收集當前可獵取的流動性阻力位 (Resistance Pools)
                res_pools = []
                if self.config.enable_pdh_pdl and not np.isnan(pdhs[i]):
                    res_pools.append(('PDH', float(pdhs[i])))
                if self.config.enable_orb_pools and not np.isnan(orb_highs[i]):
                    res_pools.append(('ORB_High', float(orb_highs[i])))
                if self.config.enable_htf_swings and not np.isnan(htf_piv_h[i]):
                    res_pools.append(('5K_Swing_High', float(htf_piv_h[i])))

                # 收集當前可獵取的流動性支撐位 (Support Pools)
                sup_pools = []
                if self.config.enable_pdh_pdl and not np.isnan(pdls[i]):
                    sup_pools.append(('PDL', float(pdls[i])))
                if self.config.enable_orb_pools and not np.isnan(orb_lows[i]):
                    sup_pools.append(('ORB_Low', float(orb_lows[i])))
                if self.config.enable_htf_swings and not np.isnan(htf_piv_l[i]):
                    sup_pools.append(('5K_Swing_Low', float(htf_piv_l[i])))

                # 計算當根動態刺穿上下限
                cur_atr = htf_atrs[i] if (not np.isnan(htf_atrs[i]) and htf_atrs[i] > 0) else 25.0
                if self.config.enable_dynamic_atr:
                    cur_min_pen = max(self.config.min_penetration, cur_atr * self.config.min_pen_atr_mult)
                    cur_max_pen = max(self.config.max_penetration, cur_atr * self.config.max_pen_atr_mult)
                else:
                    cur_min_pen = self.config.min_penetration
                    cur_max_pen = self.config.max_penetration

                # 趨勢過濾狀態判定 (5K EMA Fast vs Slow)
                is_htf_bullish = bool(not np.isnan(htf_ema_f[i]) and not np.isnan(htf_ema_s[i]) and htf_ema_f[i] >= htf_ema_s[i])
                is_htf_bearish = bool(not np.isnan(htf_ema_f[i]) and not np.isnan(htf_ema_s[i]) and htf_ema_f[i] <= htf_ema_s[i])

                # --- 1. 空頭假突破反轉 (Bearish Sweep Fade) ---
                # 趨勢濾網：若啟用 trend filter，多頭結構下禁止摸頂做空
                allow_short = (not self.config.enable_trend_filter) or is_htf_bearish
                if allow_short and session_attempts[s_grp]['short'] < self.config.max_attempts_per_session:
                    for pool_name, pool_price in res_pools:
                        penetration = h_cur - pool_price
                        # 刺穿幅度在動態範圍內，且收盤跌回池內
                        if cur_min_pen <= penetration <= cur_max_pen and c_cur < pool_price:
                            # 檢查上影線拒絕強度
                            wick_ratio = upper_wick / bar_range
                            wick_ok = (wick_ratio >= self.config.min_wick_ratio)
                            if self.config.require_wick_gte_body:
                                wick_ok = wick_ok and (upper_wick >= body)

                            if wick_ok:
                                # 訊號確立！當根收盤立即做空
                                position = -1
                                entry_price = c_cur
                                entry_time = t_cur

                                # 停損：設於 Sweep 上影線最高點 + 3 點緩衝
                                sl_raw = h_cur + self.config.sl_buffer_points
                                risk_pts = max(self.config.min_sl_points, sl_raw - entry_price)
                                stop_loss = entry_price + risk_pts
                                risk_points = risk_pts

                                # 目標 TP1
                                if self.config.tp1_mode == "vwap":
                                    vwap_val = vwaps[i]
                                    if not np.isnan(vwap_val) and vwap_val < entry_price - 5.0:
                                        tp1_price = vwap_val
                                    else:
                                        tp1_price = entry_price - risk_pts * self.config.tp1_fixed_rr
                                else:
                                    tp1_price = entry_price - risk_pts * self.config.tp1_fixed_rr

                                # 目標 TP2
                                if self.config.tp2_mode == "opposite_pool":
                                    opp_targets = [p[1] for p in sup_pools if p[1] < entry_price - 10.0]
                                    if opp_targets:
                                        tp2_price = max(opp_targets)
                                    else:
                                        tp2_price = entry_price - risk_pts * self.config.tp2_fixed_rr
                                else:
                                    tp2_price = entry_price - risk_pts * self.config.tp2_fixed_rr

                                # 口數計算
                                risk_cash = capital * self.config.risk_pct
                                calc_lots = max(self.config.min_lots, int(risk_cash / (risk_pts * self.config.point_value)))
                                # 保證能平分兩批
                                if calc_lots % 2 != 0:
                                    calc_lots += 1

                                lots_total = calc_lots
                                lots_remaining = calc_lots
                                is_tp1_filled = False
                                session_attempts[s_grp]['short'] += 1

                                trade_indicators = {
                                    'swept_pool': pool_name,
                                    'pool_price': pool_price,
                                    'penetration': round(penetration, 1),
                                    'upper_wick_ratio': round(wick_ratio, 2),
                                    'vwap': round(vwaps[i], 1) if not np.isnan(vwaps[i]) else 0.0,
                                    'htf_atr': round(cur_atr, 1),
                                    'htf_trend': 'BEARISH' if is_htf_bearish else 'BULLISH'
                                }
                                break

                # --- 2. 多頭假突破反轉 (Bullish Sweep Fade) ---
                # 趨勢濾網：若啟用 trend filter，空頭結構下禁止接刀做多
                allow_long = (not self.config.enable_trend_filter) or is_htf_bullish
                if position == 0 and allow_long and session_attempts[s_grp]['long'] < self.config.max_attempts_per_session:
                    for pool_name, pool_price in sup_pools:
                        penetration = pool_price - l_cur
                        # 刺穿幅度在動態範圍內，且收盤漲回池內
                        if cur_min_pen <= penetration <= cur_max_pen and c_cur > pool_price:
                            # 檢查下影線拒絕強度
                            wick_ratio = lower_wick / bar_range
                            wick_ok = (wick_ratio >= self.config.min_wick_ratio)
                            if self.config.require_wick_gte_body:
                                wick_ok = wick_ok and (lower_wick >= body)

                            if wick_ok:
                                # 訊號確立！當根收盤立即做多
                                position = 1
                                entry_price = c_cur
                                entry_time = t_cur

                                # 停損：設於 Sweep 下影線最低點 - 3 點緩衝
                                sl_raw = l_cur - self.config.sl_buffer_points
                                risk_pts = max(self.config.min_sl_points, entry_price - sl_raw)
                                stop_loss = entry_price - risk_pts
                                risk_points = risk_pts

                                # 目標 TP1
                                if self.config.tp1_mode == "vwap":
                                    vwap_val = vwaps[i]
                                    if not np.isnan(vwap_val) and vwap_val > entry_price + 5.0:
                                        tp1_price = vwap_val
                                    else:
                                        tp1_price = entry_price + risk_pts * self.config.tp1_fixed_rr
                                else:
                                    tp1_price = entry_price + risk_pts * self.config.tp1_fixed_rr

                                # 目標 TP2
                                if self.config.tp2_mode == "opposite_pool":
                                    opp_targets = [p[1] for p in res_pools if p[1] > entry_price + 10.0]
                                    if opp_targets:
                                        tp2_price = min(opp_targets)
                                    else:
                                        tp2_price = entry_price + risk_pts * self.config.tp2_fixed_rr
                                else:
                                    tp2_price = entry_price + risk_pts * self.config.tp2_fixed_rr

                                risk_cash = capital * self.config.risk_pct
                                calc_lots = max(self.config.min_lots, int(risk_cash / (risk_pts * self.config.point_value)))
                                if calc_lots % 2 != 0:
                                    calc_lots += 1

                                lots_total = calc_lots
                                lots_remaining = calc_lots
                                is_tp1_filled = False
                                session_attempts[s_grp]['long'] += 1

                                trade_indicators = {
                                    'swept_pool': pool_name,
                                    'pool_price': pool_price,
                                    'penetration': round(penetration, 1),
                                    'lower_wick_ratio': round(wick_ratio, 2),
                                    'vwap': round(vwap_val, 1),
                                    'htf_atr': round(cur_atr, 1),
                                    'htf_trend': 'BULLISH' if is_htf_bullish else 'BEARISH'
                                }
                                break

        # ----------------------------------------------------------------------
        # C. 績效指標計算
        # ----------------------------------------------------------------------
        total_trades = len(trades)
        if total_trades > 0:
            winning_trades = [t for t in trades if t['net_pnl'] > 0]
            losing_trades = [t for t in trades if t['net_pnl'] < 0]
            be_trades = [t for t in trades if "BE" in t['exit_reason']]
            tp1_trades = [t for t in trades if t['stage'] == 'TP1']
            tp2_trades = [t for t in trades if t['stage'] == 'TP2']

            win_rate = len(winning_trades) / total_trades
            gross_profit = sum(t['net_pnl'] for t in winning_trades)
            gross_loss = abs(sum(t['net_pnl'] for t in losing_trades))
            profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

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
                'tp1_count': len(tp1_trades),
                'tp2_count': len(tp2_trades),
                'win_rate': round(win_rate * 100.0, 2),
                'profit_factor': profit_factor,
                'max_drawdown_pct': round(mdd_pct, 2),
                'total_return_pct': round(total_return_pct, 2),
                'net_profit': round(capital - self.config.start_capital, 1),
                'ending_capital': round(capital, 1),
                'avg_win': round(avg_win, 1),
                'avg_loss': round(avg_loss, 1)
            }
        else:
            summary = {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'be_trades': 0,
                'tp1_count': 0,
                'tp2_count': 0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'max_drawdown_pct': 0.0,
                'total_return_pct': 0.0,
                'net_profit': 0.0,
                'ending_capital': self.config.start_capital,
                'avg_win': 0.0,
                'avg_loss': 0.0
            }

        return summary, trades, equity_curve

    def calculate_costs(self, entry_price: float, exit_price: float, lots: int) -> float:
        total_fee = self.config.fee_per_side * 2 * lots
        tax_entry = round(entry_price * self.config.point_value * self.config.tax_rate * lots)
        tax_exit = round(exit_price * self.config.point_value * self.config.tax_rate * lots)
        return total_fee + tax_entry + tax_exit
