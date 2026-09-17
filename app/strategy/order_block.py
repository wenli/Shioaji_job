# -*- coding: utf-8 -*-
"""
台指期 SMC 機構訂單塊 (Order Block / Mitigation Entry) 量化策略核心
作者: Antigravity (Advanced Agentic Pair Programmer)

核心架構：
1. OrderBlockConfig: 策略參數配置 dataclass
2. OrderBlockTracker: 5K 週期 BOS (結構破壞) + FVG (公允價值缺口) 機構訂單塊動態偵測與生命週期維護
3. OrderBlockEngine: 1K 微觀回踩確認、二階段階梯停利 (TP1 破位前高/低移保本，TP2 瞄準對側流動性池) 與風控引擎
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
# 1. 策略參數配置 (OrderBlockConfig)
# ==============================================================================
@dataclass
class OrderBlockConfig:
    """SMC 訂單塊策略配置"""
    # 標的與資金
    contract_type: str = "MTX"       # "MTX" (小台指) 或 "TX" (大台指)
    start_capital: float = 1_000_000.0
    risk_pct: float = 0.01           # 單筆最大風險比例 1%
    point_value: float = 50.0        # 小台 50, 大台 200
    fee_per_side: float = 20.0       # 小台單邊 20 (雙邊 40), 大台單邊 50 (雙邊 100)
    tax_rate: float = 0.00002        # 期交稅 0.002%
    min_lots: int = 2                # 預設最低交易 2 口 (便於 TP1/TP2 50% 分批結清)
    max_lots: int = 2                # 最大進場口數上限 (預設 2 口，須為偶數)

    # 5K Order Block 偵測參數
    swing_window: int = 5            # 5K 波段高低點檢驗視窗 (前後 5 根)
    require_fvg: bool = True         # 是否要求 BOS 突破時必須伴隨 FVG (公允價值缺口)
    min_fvg_points: float = 6.0      # 成立 FVG 的最小缺口點數 (實證最佳值 6.0 點，有效過濾雜訊)
    max_ob_age_bars: int = 40        # OB 最長有效生命週期 (5K 根數，40 根約 3.3 小時)
    ob_zone_mode: str = "candle"     # "candle" (整根 K 棒 High-Low) 或 "body" (實體 Open-Close)

    # 1K 微觀確認進場過濾 (Micro Confirmation)
    require_confirmation: bool = True # 是否要求 1K 形成拒絕影線才進場
    min_wick_ratio: float = 0.35     # 1K 拒絕影線佔比 (如做多下影線 >= 35%)
    require_wick_gte_body: bool = False # 影線是否必須大於實體

    # 趨勢濾網 (HTF Trend Filter)
    enable_trend_filter: bool = True # 啟用 5K EMA 趨勢結構保護
    ema_fast: int = 9
    ema_slow: int = 21

    # 停損與風控 (Stop Loss)
    sl_buffer_points: float = 3.0    # 停損設於 OB 極值外側緩衝點數
    min_sl_points: float = 25.0      # 保底停損點數 (台指 1K 平均波動約 25 點)
    max_attempts_per_ob: int = 1     # 每個 OB 允許回踩進場次數 (預設 1 次，避免多次磨損)
    max_attempts_per_session: int = 2 # 每個時段同方向最多嘗試次數

    # 時段過濾 (黃金機構回踩窗口：避開 09、15、21 點毒性開盤噪聲，鎖定 10、11 盤中趨勢與 20 點歐盤順勢回踩)
    allowed_hours: Optional[List[int]] = field(default_factory=lambda: [10, 11, 20])

    # 停利機制 (Two-Stage Take Profit - 2口限制黃金推薦: 2.0R 平 1 口移保本 / 3.0R 全平鎖利)
    tp1_mode: str = "fixed_rr"       # "fixed_rr" (2.0R), "swing_target" 或 "vwap"
    tp1_fixed_rr: float = 2.0
    tp2_mode: str = "fixed_rr"       # "fixed_rr" (3.0R) 或 "opposite_pool"
    tp2_fixed_rr: float = 3.0

    def __post_init__(self):
        if self.contract_type == "TX":
            self.point_value = 200.0
            self.fee_per_side = 50.0
        else:
            self.point_value = 50.0
            self.fee_per_side = 20.0


# ==============================================================================
# 2. 訂單塊追蹤器 (OrderBlockTracker)
# ==============================================================================
@dataclass
class OrderBlock:
    ob_id: int
    ob_type: str                     # "BULLISH" (看多回踩做多) 或 "BEARISH" (看空回踩做空)
    formed_time: pd.Timestamp
    formed_idx_5k: int
    top: float                       # OB 頂部價
    bottom: float                    # OB 底部價
    mean_threshold: float            # OB 50% 均分點
    origin_target: float             # 突破發動後形成的新波段高/低點 (TP1 目標)
    fvg_size: float                  # 伴隨之 FVG 點數
    status: str = "active"           # "active", "mitigated", "invalidated", "expired"
    trade_count: int = 0


class OrderBlockTracker:
    """
    動態計算 5K 波段高低點 (Pivots)、結構突破 (BOS)、FVG 缺口與 Order Block 清單。
    """
    @staticmethod
    def calculate_pivots(df: pd.DataFrame, window: int = 5) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
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

        confirmed_h = np.full(n, np.nan)
        confirmed_l = np.full(n, np.nan)

        for i in range(n):
            if pivot_h[i] and i + window < n:
                confirmed_h[i + window] = highs[i]
            if pivot_l[i] and i + window < n:
                confirmed_l[i + window] = lows[i]

        last_h = pd.Series(confirmed_h, index=df.index).ffill().values
        last_l = pd.Series(confirmed_l, index=df.index).ffill().values
        return pivot_h, pivot_l, last_h, last_l

    @classmethod
    def detect_5k_order_blocks(cls, df_5k: pd.DataFrame, config: OrderBlockConfig) -> Tuple[pd.DataFrame, List[OrderBlock]]:
        df = df_5k.copy().sort_values('datetime').reset_index(drop=True)
        n = len(df)

        _, _, last_h, last_l = cls.calculate_pivots(df, window=config.swing_window)
        df['last_pivot_h'] = last_h
        df['last_pivot_l'] = last_l

        # 計算 5K EMA 趨勢結構
        df['htf_ema_fast'] = df['close'].ewm(span=config.ema_fast, adjust=False).mean()
        df['htf_ema_slow'] = df['close'].ewm(span=config.ema_slow, adjust=False).mean()

        # 5K ATR 14
        tr1 = df['high'] - df['low']
        tr2 = (df['high'] - df['close'].shift(1)).abs()
        tr3 = (df['low'] - df['close'].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['htf_atr'] = tr.rolling(window=14, min_periods=1).mean()

        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        times = df['datetime'].values

        order_blocks: List[OrderBlock] = []
        ob_id_seq = 0

        # 掃描 5K K 棒尋找 BOS + FVG 生成 Order Block
        for i in range(2, n):
            c_prev_h = last_h[i - 1]
            c_prev_l = last_l[i - 1]

            # 1. Bullish BOS (向上突破前波高點)
            if not np.isnan(c_prev_h) and closes[i] > c_prev_h and closes[i - 1] <= c_prev_h:
                # 檢查 FVG (當根 Low 是否高於 i-2 根 High)
                fvg_gap = lows[i] - highs[i - 2]
                if (not config.require_fvg) or (fvg_gap >= config.min_fvg_points):
                    # 尋找發動上漲前最後一根陰線 (或最低 K 棒)
                    lookback = min(i, 8)
                    ob_idx = i - 1
                    min_val = lows[i - 1]
                    for k in range(i - 1, max(0, i - lookback), -1):
                        if closes[k] < opens[k]: # 找到陰線
                            ob_idx = k
                            break
                        if lows[k] < min_val:
                            min_val = lows[k]
                            ob_idx = k

                    if config.ob_zone_mode == "candle":
                        top = highs[ob_idx]
                        bottom = lows[ob_idx]
                    else:
                        top = max(opens[ob_idx], closes[ob_idx])
                        bottom = min(opens[ob_idx], closes[ob_idx])

                    mean_th = (top + bottom) / 2.0
                    ob_id_seq += 1
                    order_blocks.append(OrderBlock(
                        ob_id=ob_id_seq,
                        ob_type="BULLISH",
                        formed_time=pd.Timestamp(times[i]),
                        formed_idx_5k=i,
                        top=float(top),
                        bottom=float(bottom),
                        mean_threshold=float(mean_th),
                        origin_target=float(highs[i]),
                        fvg_size=float(max(0.0, fvg_gap))
                    ))

            # 2. Bearish BOS (向下擊穿前波低點)
            if not np.isnan(c_prev_l) and closes[i] < c_prev_l and closes[i - 1] >= c_prev_l:
                fvg_gap = lows[i - 2] - highs[i]
                if (not config.require_fvg) or (fvg_gap >= config.min_fvg_points):
                    lookback = min(i, 8)
                    ob_idx = i - 1
                    max_val = highs[i - 1]
                    for k in range(i - 1, max(0, i - lookback), -1):
                        if closes[k] > opens[k]: # 找到陽線
                            ob_idx = k
                            break
                        if highs[k] > max_val:
                            max_val = highs[k]
                            ob_idx = k

                    if config.ob_zone_mode == "candle":
                        top = highs[ob_idx]
                        bottom = lows[ob_idx]
                    else:
                        top = max(opens[ob_idx], closes[ob_idx])
                        bottom = min(opens[ob_idx], closes[ob_idx])

                    mean_th = (top + bottom) / 2.0
                    ob_id_seq += 1
                    order_blocks.append(OrderBlock(
                        ob_id=ob_id_seq,
                        ob_type="BEARISH",
                        formed_time=pd.Timestamp(times[i]),
                        formed_idx_5k=i,
                        top=float(top),
                        bottom=float(bottom),
                        mean_threshold=float(mean_th),
                        origin_target=float(lows[i]),
                        fvg_size=float(max(0.0, fvg_gap))
                    ))

        return df, order_blocks

    @classmethod
    def prepare_dataset(cls, df_1k: pd.DataFrame, df_5k: pd.DataFrame, config: OrderBlockConfig) -> Tuple[pd.DataFrame, List[OrderBlock]]:
        df_1k = df_1k.copy().sort_values('datetime').reset_index(drop=True)
        df_5k_prepared, order_blocks = cls.detect_5k_order_blocks(df_5k, config)

        df_5k_sub = df_5k_prepared[['datetime', 'htf_ema_fast', 'htf_ema_slow', 'htf_atr']].copy()

        # 向後對齊 5K 指標至 1K
        merged = pd.merge_asof(
            df_1k,
            df_5k_sub,
            on='datetime',
            direction='backward'
        )

        # 標記盤別與交易日
        dt_series = pd.to_datetime(merged['datetime'])
        sessions = []
        trade_dates = []
        curr_session_group = 0
        prev_sess = None
        session_groups = []

        for dt in dt_series:
            t = dt.time()
            d = dt.date()
            if time(8, 45) <= t <= time(13, 45):
                sess = 'day'
                t_date = d
            else:
                sess = 'night'
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

        # 前日高低點 (PDH / PDL)
        daily_stats = merged.groupby('trade_date').agg({'high': 'max', 'low': 'min'}).reset_index()
        daily_stats['pdh'] = daily_stats['high'].shift(1)
        daily_stats['pdl'] = daily_stats['low'].shift(1)
        daily_stats = daily_stats.drop(columns=['high', 'low'])
        merged = pd.merge(merged, daily_stats, on='trade_date', how='left')

        # 當盤累積 VWAP
        tp = (merged['high'] + merged['low'] + merged['close']) / 3.0
        cum_vol = merged.groupby('session_group')['volume'].cumsum()
        cum_pv = (tp * merged['volume']).groupby(merged['session_group']).cumsum()
        merged['vwap'] = np.where(cum_vol > 0, cum_pv / cum_vol, merged['close'])

        return merged, order_blocks


# ==============================================================================
# 3. 訂單塊回測引擎 (OrderBlockEngine)
# ==============================================================================
class OrderBlockEngine:
    """
    SMC 機構訂單塊回測核心引擎。
    """
    def __init__(self, df_1k: pd.DataFrame, df_5k: pd.DataFrame, config: Optional[OrderBlockConfig] = None):
        self.config = config or OrderBlockConfig()
        self.df_1k = df_1k
        self.df_5k = df_5k
        self.df: pd.DataFrame = pd.DataFrame()
        self.order_blocks: List[OrderBlock] = []

    def run_backtest(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        if self.df.empty:
            self.df, self.order_blocks = OrderBlockTracker.prepare_dataset(self.df_1k, self.df_5k, self.config)

        df = self.df
        n = len(df)
        if n == 0:
            return {}, [], []

        times = df['datetime'].values
        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        vwaps = df['vwap'].values
        pdhs = df['pdh'].values
        pdls = df['pdl'].values
        htf_ema_f = df['htf_ema_fast'].values if 'htf_ema_fast' in df else np.zeros(n)
        htf_ema_s = df['htf_ema_slow'].values if 'htf_ema_slow' in df else np.zeros(n)
        htf_atrs = df['htf_atr'].values if 'htf_atr' in df else np.full(n, 25.0)
        sessions = df['session'].values
        session_groups = df['session_group'].values

        capital = self.config.start_capital
        equity_curve = [{'time': str(times[0]), 'equity': capital}]
        trades = []

        position = 0
        lots_total = 0
        lots_remaining = 0
        entry_price = 0.0
        entry_time = None
        stop_loss = 0.0
        risk_points = 0.0
        tp1_price = 0.0
        tp2_price = 0.0
        is_tp1_filled = False
        trade_indicators = {}

        # 活躍 OB 池管理
        active_obs = list(self.order_blocks)
        # 依形成時間排序
        active_obs.sort(key=lambda x: x.formed_time)
        ob_cursor = 0

        current_active_obs: List[OrderBlock] = []
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

            # 加入時間已達到的新 OB
            while ob_cursor < len(active_obs) and active_obs[ob_cursor].formed_time <= t_cur:
                current_active_obs.append(active_obs[ob_cursor])
                ob_cursor += 1

            # 清理過期或已被擊穿作廢的 OB
            valid_obs = []
            for ob in current_active_obs:
                # 檢查是否過期 (以 1K 算約 600 根 = 10 小時)
                if (t_cur - ob.formed_time).total_seconds() > 3600 * 12:
                    ob.status = "expired"
                    continue
                # 檢查 Bullish OB 是否被實體下破 (作廢)
                if ob.ob_type == "BULLISH" and c_cur < ob.bottom - 10.0:
                    ob.status = "invalidated"
                    continue
                # 檢查 Bearish OB 是否被實體上破 (作廢)
                if ob.ob_type == "BEARISH" and c_cur > ob.top + 10.0:
                    ob.status = "invalidated"
                    continue
                if ob.status == "active":
                    valid_obs.append(ob)
            current_active_obs = valid_obs

            # ------------------------------------------------------------------
            # A. 持倉處理 (TP1 平半倉移保本 / TP2 對側池結算)
            # ------------------------------------------------------------------
            if position != 0:
                triggered_exit = False
                exit_price = 0.0
                exit_reason = ""
                exit_lots = 0

                # 盤別結束強制平倉
                if curr_sess != prev_sess:
                    triggered_exit = True
                    exit_price = closes[i - 1]
                    exit_reason = "EOD (收盤強平)"
                    exit_lots = lots_remaining

                if not triggered_exit:
                    if position == 1: # 多單持倉
                        if not is_tp1_filled:
                            if l_cur <= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "SL (止損)"
                                exit_lots = lots_remaining
                            elif h_cur >= tp1_price:
                                half_lots = math.ceil(lots_total / 2)
                                pnl_pts = tp1_price - entry_price
                                gross_pnl = pnl_pts * half_lots * self.config.point_value
                                costs = self.calculate_costs(entry_price, tp1_price, half_lots)
                                net_pnl = gross_pnl - costs
                                capital += net_pnl

                                trades.append({
                                    'trade_no': len(trades) + 1,
                                    'strategy': 'order_block',
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
                                stop_loss = max(stop_loss, entry_price)

                        else:
                            if h_cur >= tp2_price:
                                triggered_exit = True
                                exit_price = tp2_price
                                exit_reason = "TP2 (目標全平)"
                                exit_lots = lots_remaining
                            elif l_cur <= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "BE (保本出場)" if abs(stop_loss - entry_price) < 0.1 else "SL (止損)"
                                exit_lots = lots_remaining

                    elif position == -1: # 空單持倉
                        if not is_tp1_filled:
                            if h_cur >= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "SL (止損)"
                                exit_lots = lots_remaining
                            elif l_cur <= tp1_price:
                                half_lots = math.ceil(lots_total / 2)
                                pnl_pts = entry_price - tp1_price
                                gross_pnl = pnl_pts * half_lots * self.config.point_value
                                costs = self.calculate_costs(entry_price, tp1_price, half_lots)
                                net_pnl = gross_pnl - costs
                                capital += net_pnl

                                trades.append({
                                    'trade_no': len(trades) + 1,
                                    'strategy': 'order_block',
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
                                stop_loss = min(stop_loss, entry_price)

                        else:
                            if l_cur <= tp2_price:
                                triggered_exit = True
                                exit_price = tp2_price
                                exit_reason = "TP2 (目標全平)"
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
                        'strategy': 'order_block',
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
            # B. 尋找 Order Block 回踩訊號 (Mitigation Trigger)
            # ------------------------------------------------------------------
            if position == 0:
                if self.config.allowed_hours is not None and t_cur.hour not in self.config.allowed_hours:
                    continue

                bar_range = max(1.0, h_cur - l_cur)
                body = abs(c_cur - o_cur)
                upper_wick = h_cur - max(o_cur, c_cur)
                lower_wick = min(o_cur, c_cur) - l_cur

                is_htf_bullish = bool(not np.isnan(htf_ema_f[i]) and not np.isnan(htf_ema_s[i]) and htf_ema_f[i] >= htf_ema_s[i])
                is_htf_bearish = bool(not np.isnan(htf_ema_f[i]) and not np.isnan(htf_ema_s[i]) and htf_ema_f[i] <= htf_ema_s[i])

                # 1. 檢驗看多訂單塊回踩 (Bullish OB Mitigation -> 做多)
                allow_long = (not self.config.enable_trend_filter) or is_htf_bullish
                if allow_long and session_attempts[s_grp]['long'] < self.config.max_attempts_per_session:
                    for ob in current_active_obs:
                        if ob.ob_type != "BULLISH" or ob.trade_count >= self.config.max_attempts_per_ob:
                            continue

                        # 價格最低點回踩進入 OB 區間 [bottom, top]
                        if l_cur <= ob.top and h_cur >= ob.bottom:
                            # 微觀確認檢查
                            confirmed = True
                            if self.config.require_confirmation:
                                wick_ratio = lower_wick / bar_range
                                confirmed = (wick_ratio >= self.config.min_wick_ratio) or (c_cur >= ob.top)
                                if self.config.require_wick_gte_body:
                                    confirmed = confirmed and (lower_wick >= body)

                            if confirmed:
                                position = 1
                                entry_price = c_cur
                                entry_time = t_cur

                                # 停損：設於 OB 底部 - 緩衝點數
                                sl_raw = ob.bottom - self.config.sl_buffer_points
                                risk_pts = max(self.config.min_sl_points, entry_price - sl_raw)
                                stop_loss = entry_price - risk_pts
                                risk_points = risk_pts

                                # TP1: 發動突破的前波高點 (Origin Target) 或 1.5R
                                if self.config.tp1_mode == "swing_target" and ob.origin_target > entry_price + 10.0:
                                    tp1_price = ob.origin_target
                                elif self.config.tp1_mode == "vwap" and not np.isnan(vwaps[i]) and vwaps[i] > entry_price + 5.0:
                                    tp1_price = vwaps[i]
                                else:
                                    tp1_price = entry_price + risk_pts * self.config.tp1_fixed_rr

                                # TP2: 前日高點 (PDH) 或 3.0R
                                if self.config.tp2_mode == "opposite_pool" and not np.isnan(pdhs[i]) and pdhs[i] > entry_price + 15.0:
                                    tp2_price = float(pdhs[i])
                                else:
                                    tp2_price = entry_price + risk_pts * self.config.tp2_fixed_rr

                                risk_cash = capital * self.config.risk_pct
                                calc_lots = max(self.config.min_lots, int(risk_cash / (risk_pts * self.config.point_value)))
                                if self.config.max_lots is not None and self.config.max_lots >= self.config.min_lots:
                                    calc_lots = min(calc_lots, self.config.max_lots)
                                if calc_lots % 2 != 0:
                                    calc_lots = max(self.config.min_lots, (calc_lots // 2) * 2)

                                lots_total = calc_lots
                                lots_remaining = calc_lots
                                is_tp1_filled = False
                                session_attempts[s_grp]['long'] += 1
                                ob.trade_count += 1
                                ob.status = "mitigated"

                                trade_indicators = {
                                    'ob_id': ob.ob_id,
                                    'ob_type': ob.ob_type,
                                    'ob_top': ob.top,
                                    'ob_bottom': ob.bottom,
                                    'fvg_size': ob.fvg_size,
                                    'vwap': round(vwaps[i], 1) if not np.isnan(vwaps[i]) else 0.0,
                                    'htf_trend': 'BULLISH' if is_htf_bullish else 'BEARISH'
                                }
                                break

                # 2. 檢驗看空訂單塊回踩 (Bearish OB Mitigation -> 做空)
                allow_short = (not self.config.enable_trend_filter) or is_htf_bearish
                if position == 0 and allow_short and session_attempts[s_grp]['short'] < self.config.max_attempts_per_session:
                    for ob in current_active_obs:
                        if ob.ob_type != "BEARISH" or ob.trade_count >= self.config.max_attempts_per_ob:
                            continue

                        # 價格最高點回踩進入 OB 區間 [bottom, top]
                        if h_cur >= ob.bottom and l_cur <= ob.top:
                            confirmed = True
                            if self.config.require_confirmation:
                                wick_ratio = upper_wick / bar_range
                                confirmed = (wick_ratio >= self.config.min_wick_ratio) or (c_cur <= ob.bottom)
                                if self.config.require_wick_gte_body:
                                    confirmed = confirmed and (upper_wick >= body)

                            if confirmed:
                                position = -1
                                entry_price = c_cur
                                entry_time = t_cur

                                # 停損：設於 OB 頂部 + 緩衝點數
                                sl_raw = ob.top + self.config.sl_buffer_points
                                risk_pts = max(self.config.min_sl_points, sl_raw - entry_price)
                                stop_loss = entry_price + risk_pts
                                risk_points = risk_pts

                                # TP1: 發動突破的前波低點 (Origin Target) 或 1.5R
                                if self.config.tp1_mode == "swing_target" and ob.origin_target < entry_price - 10.0:
                                    tp1_price = ob.origin_target
                                elif self.config.tp1_mode == "vwap" and not np.isnan(vwaps[i]) and vwaps[i] < entry_price - 5.0:
                                    tp1_price = vwaps[i]
                                else:
                                    tp1_price = entry_price - risk_pts * self.config.tp1_fixed_rr

                                # TP2: 前日低點 (PDL) 或 3.0R
                                if self.config.tp2_mode == "opposite_pool" and not np.isnan(pdls[i]) and pdls[i] < entry_price - 15.0:
                                    tp2_price = float(pdls[i])
                                else:
                                    tp2_price = entry_price - risk_pts * self.config.tp2_fixed_rr

                                risk_cash = capital * self.config.risk_pct
                                calc_lots = max(self.config.min_lots, int(risk_cash / (risk_pts * self.config.point_value)))
                                if self.config.max_lots is not None and self.config.max_lots >= self.config.min_lots:
                                    calc_lots = min(calc_lots, self.config.max_lots)
                                if calc_lots % 2 != 0:
                                    calc_lots = max(self.config.min_lots, (calc_lots // 2) * 2)

                                lots_total = calc_lots
                                lots_remaining = calc_lots
                                is_tp1_filled = False
                                session_attempts[s_grp]['short'] += 1
                                ob.trade_count += 1
                                ob.status = "mitigated"

                                trade_indicators = {
                                    'ob_id': ob.ob_id,
                                    'ob_type': ob.ob_type,
                                    'ob_top': ob.top,
                                    'ob_bottom': ob.bottom,
                                    'fvg_size': ob.fvg_size,
                                    'vwap': round(vwaps[i], 1) if not np.isnan(vwaps[i]) else 0.0,
                                    'htf_trend': 'BEARISH' if is_htf_bearish else 'BULLISH'
                                }
                                break

        # ----------------------------------------------------------------------
        # C. 績效統計計算
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
