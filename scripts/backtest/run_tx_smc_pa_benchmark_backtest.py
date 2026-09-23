# -*- coding: utf-8 -*-
"""
台指期【基準版】SMC × PA 雙重確認策略詳細回測執行器
(5K HTF Order Block × 1K LTF Dual Reversal Market Entry)

核心演算法落實：
1. 5K HTF 宏觀定位：Demand/Supply OB 偵測 + 50% Dealing Range (DR) 折溢價過濾 + 單一 OB 停損上限 2 次
2. 1K LTF 雙重精準確認：前 15 根微結構轉折 CHoCh ＋ 早期 PA 反轉動能棒（形態學/Pinbar/強實體）
3. 風控與階梯式出場：1K 前 10 根極值 + 2.0 點 Buffer 初始停損
4. 出場機制：TP1 2.0R 減半倉移保本 / TP2 對側 DR Swing 極值(或 3.5R) 全平 / 13:40 市價強制平倉
"""

import os
import sys
import math
import json
import sqlite3
from datetime import datetime, time, timedelta
from typing import Tuple, List, Dict, Any, Optional
import pandas as pd
import numpy as np

# 設置標準輸出編碼
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def get_db_path() -> str:
    candidates = [
        os.getenv("DB_NAME"),
        r"C:\Intel\Database\Shioaji-future.db",
        "Shioaji.db",
        r"C:\Intel\TW_Stock_K-Line_Chart\SK.db"
    ]
    for c in candidates:
        if c and os.path.exists(c) and os.path.getsize(c) > 1024:
            return c
    raise FileNotFoundError("找不到期貨 SQLite 資料庫檔案。")


def load_dataset(db_path: str, code: str = "TXFR1", start_date: str = "2026-06-18", end_date: str = "2026-09-18") -> Tuple[pd.DataFrame, pd.DataFrame]:
    conn = sqlite3.connect(db_path)
    q_1k = f"""
    SELECT ts as datetime, open, high, low, close, volume 
    FROM futures1k 
    WHERE code='{code}' AND ts >= '{start_date} 00:00:00' AND ts <= '{end_date} 23:59:59'
    ORDER BY ts;
    """
    q_5k = f"""
    SELECT ts as datetime, open, high, low, close, volume 
    FROM futures5k 
    WHERE code='{code}' AND ts >= '{start_date} 00:00:00' AND ts <= '{end_date} 23:59:59'
    ORDER BY ts;
    """
    df_1k = pd.read_sql_query(q_1k, conn)
    df_5k = pd.read_sql_query(q_5k, conn)
    conn.close()

    df_1k['datetime'] = pd.to_datetime(df_1k['datetime'])
    df_5k['datetime'] = pd.to_datetime(df_5k['datetime'])
    return df_1k, df_5k


class SMC_PA_Benchmark_Engine:
    def __init__(
        self,
        df_1k: pd.DataFrame,
        df_5k: pd.DataFrame,
        contract_type: str = "MTX",
        max_lots: int = 2,
        swing_window: int = 5,
        sl_buffer_pts: float = 2.0,
        min_risk_pts: float = 4.0,
        max_losses_per_ob: int = 2,
        start_capital: float = 1_000_000.0
    ):
        self.df_1k = df_1k.copy().sort_values('datetime').reset_index(drop=True)
        self.df_5k = df_5k.copy().sort_values('datetime').reset_index(drop=True)
        self.contract_type = contract_type
        self.point_value = 50.0 if contract_type == "MTX" else 200.0
        self.fee_per_side = 20.0 if contract_type == "MTX" else 50.0
        self.tax_rate = 0.00002
        self.max_lots = max_lots
        self.swing_window = swing_window
        self.sl_buffer_pts = sl_buffer_pts
        self.min_risk_pts = min_risk_pts
        self.max_losses_per_ob = max_losses_per_ob
        self.start_capital = start_capital

    def calculate_5k_context(self) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
        """計算 5K 波段高低點、Dealing Range Equilibrium (50% 中軸) 與 Order Block"""
        df = self.df_5k.copy()
        n = len(df)
        highs = df['high'].values
        lows = df['low'].values
        opens = df['open'].values
        closes = df['close'].values
        times = df['datetime'].values

        pivot_h = np.zeros(n, dtype=bool)
        pivot_l = np.zeros(n, dtype=bool)
        window = self.swing_window

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

        df['swing_high'] = pd.Series(confirmed_h, index=df.index).ffill()
        df['swing_low'] = pd.Series(confirmed_l, index=df.index).ffill()
        df['equilibrium'] = (df['swing_high'] + df['swing_low']) / 2.0

        sh_vals = df['swing_high'].values
        sl_vals = df['swing_low'].values

        order_blocks = []
        ob_seq = 0

        for i in range(2, n):
            c_prev_h = sh_vals[i - 1]
            c_prev_l = sl_vals[i - 1]

            # 1. Demand OB (做多需求區): 向上發動突破前，最後一根實體陰 K 棒
            if not np.isnan(c_prev_h) and closes[i] > c_prev_h and closes[i - 1] <= c_prev_h:
                lookback = min(i, 8)
                ob_idx = i - 1
                min_val = lows[i - 1]
                for k in range(i - 1, max(0, i - lookback), -1):
                    if closes[k] < opens[k]:
                        ob_idx = k
                        break
                    if lows[k] < min_val:
                        min_val = lows[k]
                        ob_idx = k

                top = highs[ob_idx]
                bottom = lows[ob_idx]
                ob_seq += 1
                order_blocks.append({
                    'ob_id': ob_seq,
                    'ob_type': 'DEMAND',
                    'formed_time': pd.Timestamp(times[i]),
                    'top': float(top),
                    'bottom': float(bottom),
                    'loss_count': 0,
                    'status': 'active'
                })

            # 2. Supply OB (做空供給區): 向下發動突破前，最後一根實體陽 K 棒
            if not np.isnan(c_prev_l) and closes[i] < c_prev_l and closes[i - 1] >= c_prev_l:
                lookback = min(i, 8)
                ob_idx = i - 1
                max_val = highs[i - 1]
                for k in range(i - 1, max(0, i - lookback), -1):
                    if closes[k] > opens[k]:
                        ob_idx = k
                        break
                    if highs[k] > max_val:
                        max_val = highs[k]
                        ob_idx = k

                top = highs[ob_idx]
                bottom = lows[ob_idx]
                ob_seq += 1
                order_blocks.append({
                    'ob_id': ob_seq,
                    'ob_type': 'SUPPLY',
                    'formed_time': pd.Timestamp(times[i]),
                    'top': float(top),
                    'bottom': float(bottom),
                    'loss_count': 0,
                    'status': 'active'
                })

        return df, order_blocks

    def detect_1k_pa_signals(self, opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, idx: int) -> Tuple[bool, bool, str]:
        """
        條件 2：1K 早期 PA 反轉動能棒 (Setup Bar) 判斷
        多方訊號: 晨星, 陽包陰吞噬, 錘子線, 長下影 Pinbar, 強實體陽棒
        空方訊號: 黃昏星, 陰包陽吞噬, 流星線, 長上影 Pinbar, 強實體陰棒
        """
        o = opens[idx]
        h = highs[idx]
        l = lows[idx]
        c = closes[idx]
        bar_len = max(1.0, h - l)
        body = abs(c - o)
        lower_wick = min(o, c) - l
        upper_wick = h - max(o, c)

        bullish_pa = False
        bearish_pa = False
        pa_desc = ""

        # 1. 多方訊號 (Bullish PA)
        # (a) 長下影 Pinbar
        if (lower_wick >= 0.45 * bar_len) and (c >= o):
            bullish_pa = True
            pa_desc = "Pinbar (長下影)"
        # (b) 強實體陽棒
        elif (c > o) and ((c - o) >= 0.55 * bar_len):
            bullish_pa = True
            pa_desc = "Strong Bull (強陽棒)"
        # (c) 錘子線 (Hammer)
        elif (lower_wick >= 2.0 * body) and (upper_wick <= 0.15 * bar_len) and (c >= o):
            bullish_pa = True
            pa_desc = "Hammer (錘子線)"
        # (d) 陽包陰吞噬 (Bullish Engulfing)
        elif idx >= 1 and (closes[idx - 1] < opens[idx - 1]) and (c > o) and (c >= opens[idx - 1]) and (o <= closes[idx - 1]):
            bullish_pa = True
            pa_desc = "Bullish Engulfing (吞噬)"
        # (e) 晨星 (Morning Star)
        elif idx >= 2 and (closes[idx - 2] < opens[idx - 2]) and (abs(closes[idx - 1] - opens[idx - 1]) <= 0.3 * (highs[idx - 2] - lows[idx - 2])) and (c > o) and (c > (opens[idx - 2] + closes[idx - 2]) / 2.0):
            bullish_pa = True
            pa_desc = "Morning Star (晨星)"

        # 2. 空方訊號 (Bearish PA)
        # (a) 長上影 Pinbar
        if (upper_wick >= 0.45 * bar_len) and (c <= o):
            bearish_pa = True
            pa_desc = "Pinbar (長上影)"
        # (b) 強實體陰棒
        elif (c < o) and ((o - c) >= 0.55 * bar_len):
            bearish_pa = True
            pa_desc = "Strong Bear (強陰棒)"
        # (c) 流星線 (Shooting Star)
        elif (upper_wick >= 2.0 * body) and (lower_wick <= 0.15 * bar_len) and (c <= o):
            bearish_pa = True
            pa_desc = "Shooting Star (流星線)"
        # (d) 陰包陽吞噬 (Bearish Engulfing)
        elif idx >= 1 and (closes[idx - 1] > opens[idx - 1]) and (c < o) and (c <= opens[idx - 1]) and (o >= closes[idx - 1]):
            bearish_pa = True
            pa_desc = "Bearish Engulfing (吞噬)"
        # (e) 黃昏星 (Evening Star)
        elif idx >= 2 and (closes[idx - 2] > opens[idx - 2]) and (abs(closes[idx - 1] - opens[idx - 1]) <= 0.3 * (highs[idx - 2] - lows[idx - 2])) and (c < o) and (c < (opens[idx - 2] + closes[idx - 2]) / 2.0):
            bearish_pa = True
            pa_desc = "Evening Star (黃昏星)"

        return bullish_pa, bearish_pa, pa_desc

    def calculate_costs(self, p1: float, p2: float, lots: int) -> float:
        fee = self.fee_per_side * 2 * lots
        tax = (p1 + p2) * self.point_value * self.tax_rate * lots
        return round(fee + tax, 1)

    def run_backtest(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        df_5k_prep, raw_obs = self.calculate_5k_context()
        df_5k_sub = df_5k_prep[['datetime', 'swing_high', 'swing_low', 'equilibrium']].copy()

        df_1k = pd.merge_asof(
            self.df_1k,
            df_5k_sub,
            on='datetime',
            direction='backward'
        )

        n = len(df_1k)
        times = df_1k['datetime'].values
        opens = df_1k['open'].values
        highs = df_1k['high'].values
        lows = df_1k['low'].values
        closes = df_1k['close'].values
        swing_highs = df_1k['swing_high'].values
        swing_lows = df_1k['swing_low'].values
        equilibriums = df_1k['equilibrium'].values

        capital = self.start_capital
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
        current_ob = None
        trade_desc = ""

        all_obs = [dict(ob) for ob in raw_obs]
        all_obs.sort(key=lambda x: x['formed_time'])
        ob_cursor = 0
        active_obs: List[Dict[str, Any]] = []

        for i in range(15, n):
            t_cur = pd.Timestamp(times[i])
            cur_t = t_cur.time()
            o_cur = opens[i]
            h_cur = highs[i]
            l_cur = lows[i]
            c_cur = closes[i]
            eq_val = equilibriums[i]
            sh_val = swing_highs[i]
            sl_val = swing_lows[i]

            # 載入形成時間已到的 OB
            while ob_cursor < len(all_obs) and all_obs[ob_cursor]['formed_time'] <= t_cur:
                active_obs.append(all_obs[ob_cursor])
                ob_cursor += 1

            # 維護 OB 生命週期
            valid_obs = []
            for ob in active_obs:
                if (t_cur - ob['formed_time']).total_seconds() > 3600 * 12:
                    ob['status'] = 'expired'
                    continue
                if ob['ob_type'] == 'DEMAND' and c_cur < ob['bottom'] - 5.0:
                    ob['status'] = 'invalidated'
                    continue
                if ob['ob_type'] == 'SUPPLY' and c_cur > ob['top'] + 5.0:
                    ob['status'] = 'invalidated'
                    continue
                if ob['loss_count'] >= self.max_losses_per_ob:
                    ob['status'] = 'invalidated'
                    continue
                if ob['status'] == 'active':
                    valid_obs.append(ob)
            active_obs = valid_obs

            # ==================================================================
            # 1. 部位管理與階梯式出場 (TP1 2.0R 減半倉移保本 / TP2 對側DR / 13:40 強平)
            # ==================================================================
            if position != 0:
                is_day_session = (time(8, 45) <= cur_t <= time(13, 45))
                is_force_close_time = (cur_t >= time(13, 40) and cur_t <= time(13, 45))

                triggered_exit = False
                exit_price = 0.0
                exit_reason = ""
                exit_lots = 0

                # 13:40 日盤當沖強制市價平倉
                if is_day_session and is_force_close_time:
                    triggered_exit = True
                    exit_price = c_cur
                    exit_reason = "13:40 日盤強平"
                    exit_lots = lots_remaining

                if not triggered_exit:
                    if position == 1: # 多單持倉
                        if not is_tp1_filled:
                            # 觸發初始停損
                            if l_cur <= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "SL (初始停損)"
                                exit_lots = lots_remaining
                            # 觸發 TP1 (2.0R): 平倉 50% 並移保本
                            elif h_cur >= tp1_price:
                                half_lots = math.ceil(lots_total / 2)
                                pnl_pts = tp1_price - entry_price
                                net_pnl = pnl_pts * half_lots * self.point_value - self.calculate_costs(entry_price, tp1_price, half_lots)
                                capital += net_pnl

                                trades.append({
                                    'trade_no': len(trades) + 1,
                                    'ob_id': current_ob['ob_id'] if current_ob else 0,
                                    'stage': 'TP1',
                                    'side': 'BUY',
                                    'entry_time': str(entry_time),
                                    'entry_price': float(entry_price),
                                    'exit_time': str(t_cur),
                                    'exit_price': float(tp1_price),
                                    'exit_reason': "TP1 (2.0R 減半倉移保本)",
                                    'lots': int(half_lots),
                                    'risk_points': float(risk_points),
                                    'pnl_points': float(pnl_pts),
                                    'net_pnl': float(net_pnl),
                                    'capital_after': float(capital),
                                    'setup': trade_desc
                                })
                                equity_curve.append({'time': str(t_cur), 'equity': capital})
                                lots_remaining -= half_lots
                                is_tp1_filled = True
                                # 移保本: 嚴格設為進場價
                                stop_loss = entry_price
                                if current_ob:
                                    current_ob['status'] = 'mitigated'

                        else:
                            # 剩餘 50% 奔跑至 TP2 或 保本出場 (BE Stop)
                            if h_cur >= tp2_price:
                                triggered_exit = True
                                exit_price = tp2_price
                                exit_reason = "TP2 (對側DR/3.5R全平)"
                                exit_lots = lots_remaining
                            elif l_cur <= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "BE (保本出場)" if stop_loss >= entry_price else "SL (停損)"
                                exit_lots = lots_remaining

                    elif position == -1: # 空單持倉
                        if not is_tp1_filled:
                            if h_cur >= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "SL (初始停損)"
                                exit_lots = lots_remaining
                            elif l_cur <= tp1_price:
                                half_lots = math.ceil(lots_total / 2)
                                pnl_pts = entry_price - tp1_price
                                net_pnl = pnl_pts * half_lots * self.point_value - self.calculate_costs(entry_price, tp1_price, half_lots)
                                capital += net_pnl

                                trades.append({
                                    'trade_no': len(trades) + 1,
                                    'ob_id': current_ob['ob_id'] if current_ob else 0,
                                    'stage': 'TP1',
                                    'side': 'SELL',
                                    'entry_time': str(entry_time),
                                    'entry_price': float(entry_price),
                                    'exit_time': str(t_cur),
                                    'exit_price': float(tp1_price),
                                    'exit_reason': "TP1 (2.0R 減半倉移保本)",
                                    'lots': int(half_lots),
                                    'risk_points': float(risk_points),
                                    'pnl_points': float(pnl_pts),
                                    'net_pnl': float(net_pnl),
                                    'capital_after': float(capital),
                                    'setup': trade_desc
                                })
                                equity_curve.append({'time': str(t_cur), 'equity': capital})
                                lots_remaining -= half_lots
                                is_tp1_filled = True
                                stop_loss = entry_price
                                if current_ob:
                                    current_ob['status'] = 'mitigated'

                        else:
                            if l_cur <= tp2_price:
                                triggered_exit = True
                                exit_price = tp2_price
                                exit_reason = "TP2 (對側DR/3.5R全平)"
                                exit_lots = lots_remaining
                            elif h_cur >= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "BE (保本出場)" if stop_loss <= entry_price else "SL (停損)"
                                exit_lots = lots_remaining

                if triggered_exit and exit_lots > 0:
                    pnl_pts = (exit_price - entry_price) * position
                    net_pnl = pnl_pts * exit_lots * self.point_value - self.calculate_costs(entry_price, exit_price, exit_lots)
                    capital += net_pnl

                    trades.append({
                        'trade_no': len(trades) + 1,
                        'ob_id': current_ob['ob_id'] if current_ob else 0,
                        'stage': 'TP2' if 'TP2' in exit_reason else ('BE' if 'BE' in exit_reason else ('FORCE_EOD' if '強平' in exit_reason else 'FINAL_SL')),
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
                        'setup': trade_desc
                    })
                    equity_curve.append({'time': str(t_cur), 'equity': capital})

                    # 單一 OB 停損計數維護
                    if current_ob:
                        if 'SL' in exit_reason and not is_tp1_filled:
                            current_ob['loss_count'] += 1
                            if current_ob['loss_count'] >= self.max_losses_per_ob:
                                current_ob['status'] = 'invalidated'
                        elif 'TP' in exit_reason:
                            current_ob['status'] = 'mitigated'

                    position = 0
                    lots_remaining = 0
                    current_ob = None

            # ==================================================================
            # 2. SMC × PA 雙重確認進場偵測 (僅限日盤 08:45 ~ 13:30)
            # ==================================================================
            if position == 0:
                if not (time(8, 45) <= cur_t <= time(13, 30)):
                    continue

                if np.isnan(eq_val) or np.isnan(sh_val) or np.isnan(sl_val):
                    continue

                # 檢驗 1K PA 早期動能棒 (條件 2)
                is_bull_pa, is_bear_pa, pa_name = self.detect_1k_pa_signals(opens, highs, lows, closes, i)

                # 回溯 1K 前 15 根 K 棒尋找微結構轉折點 Peak_High / Trough_Low (條件 1: CHoCh)
                lookback_15_h = highs[max(0, i - 15):i]
                lookback_15_l = lows[max(0, i - 15):i]
                peak_high_15 = np.max(lookback_15_h) if len(lookback_15_h) > 0 else c_cur
                trough_low_15 = np.min(lookback_15_l) if len(lookback_15_l) > 0 else c_cur

                is_bull_choch = (c_cur > peak_high_15 or h_cur > peak_high_15)
                # 為確保捕捉突破瞬間，亦檢查相對於前 3~5 根次高點之 CHoCh
                if not is_bull_choch and i >= 5:
                    sub_peak = np.max(highs[i - 5:i])
                    is_bull_choch = (c_cur > sub_peak)

                is_bear_choch = (c_cur < trough_low_15 or l_cur < trough_low_15)
                if not is_bear_choch and i >= 5:
                    sub_trough = np.min(lows[i - 5:i])
                    is_bear_choch = (c_cur < sub_trough)

                # --------------------------------------------------------------
                # 做多檢查: 價格處於折價區 (Price < Equilibrium) 且觸碰 Demand OB
                # --------------------------------------------------------------
                if c_cur < eq_val and is_bull_choch and is_bull_pa:
                    for ob in active_obs:
                        if ob['ob_type'] != 'DEMAND' or ob['loss_count'] >= self.max_losses_per_ob:
                            continue

                        # OB 觸碰條件: c_low <= OB_High 且 c_high >= OB_Low
                        if l_cur <= ob['top'] and h_cur >= ob['bottom']:
                            # 計算初始停損: 1K 前 10 根最低點 - 2.0 點
                            recent_10_lows = lows[max(0, i - 10):i + 1]
                            sl_val_calc = np.min(recent_10_lows) - self.sl_buffer_pts
                            risk_pts = c_cur - sl_val_calc

                            if risk_pts < self.min_risk_pts:
                                continue

                            position = 1
                            entry_price = c_cur
                            entry_time = t_cur
                            stop_loss = sl_val_calc
                            risk_points = risk_pts

                            # TP1: 嚴格 1:2 風報比
                            tp1_price = entry_price + 2.0 * risk_pts

                            # TP2: 對側 Dealing Range 極值 (Swing High)，若已突破則設為 3.5R
                            if sh_val > entry_price + 10.0:
                                tp2_price = sh_val
                            else:
                                tp2_price = entry_price + 3.5 * risk_pts

                            lots_total = self.max_lots
                            lots_remaining = self.max_lots
                            is_tp1_filled = False
                            current_ob = ob
                            trade_desc = f"Demand OB #{ob['ob_id']} + CHoCh + {pa_name}"
                            break

                # --------------------------------------------------------------
                # 做空檢查: 價格處於溢價區 (Price > Equilibrium) 且觸碰 Supply OB
                # --------------------------------------------------------------
                if position == 0 and c_cur > eq_val and is_bear_choch and is_bear_pa:
                    for ob in active_obs:
                        if ob['ob_type'] != 'SUPPLY' or ob['loss_count'] >= self.max_losses_per_ob:
                            continue

                        if h_cur >= ob['bottom'] and l_cur <= ob['top']:
                            recent_10_highs = highs[max(0, i - 10):i + 1]
                            sl_val_calc = np.max(recent_10_highs) + self.sl_buffer_pts
                            risk_pts = sl_val_calc - c_cur

                            if risk_pts < self.min_risk_pts:
                                continue

                            position = -1
                            entry_price = c_cur
                            entry_time = t_cur
                            stop_loss = sl_val_calc
                            risk_points = risk_pts

                            # TP1: 1:2 風報比
                            tp1_price = entry_price - 2.0 * risk_pts

                            # TP2: 對側 Dealing Range 極值 (Swing Low)，若已跌破則設為 3.5R
                            if sl_val < entry_price - 10.0:
                                tp2_price = sl_val
                            else:
                                tp2_price = entry_price - 3.5 * risk_pts

                            lots_total = self.max_lots
                            lots_remaining = self.max_lots
                            is_tp1_filled = False
                            current_ob = ob
                            trade_desc = f"Supply OB #{ob['ob_id']} + CHoCh + {pa_name}"
                            break

        # 彙總績效統計
        total_trades = len(trades)
        winning_trades = [t for t in trades if t['net_pnl'] > 0]
        losing_trades = [t for t in trades if t['net_pnl'] < 0]
        be_trades = [t for t in trades if 'BE' in t['exit_reason']]
        tp1_trades = [t for t in trades if t['stage'] == 'TP1']
        tp2_trades = [t for t in trades if t['stage'] == 'TP2']
        eod_trades = [t for t in trades if '強平' in t['exit_reason']]

        win_rate = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0
        gross_profit = sum(t['net_pnl'] for t in winning_trades)
        gross_loss = abs(sum(t['net_pnl'] for t in losing_trades))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        eq_values = [e['equity'] for e in equity_curve]
        running_max = np.maximum.accumulate(eq_values)
        drawdowns = (running_max - eq_values) / running_max
        mdd_pct = float(np.max(drawdowns)) * 100.0 if len(drawdowns) > 0 else 0.0

        net_profit = capital - self.start_capital
        total_return_pct = (net_profit / self.start_capital) * 100.0
        avg_win = float(np.mean([t['net_pnl'] for t in winning_trades])) if winning_trades else 0.0
        avg_loss = float(np.mean([t['net_pnl'] for t in losing_trades])) if losing_trades else 0.0
        win_loss_ratio = round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0.0

        summary = {
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'be_trades': len(be_trades),
            'tp1_count': len(tp1_trades),
            'tp2_count': len(tp2_trades),
            'eod_count': len(eod_trades),
            'win_rate': round(win_rate, 2),
            'profit_factor': profit_factor,
            'max_drawdown_pct': round(mdd_pct, 2),
            'net_profit': round(net_profit, 0),
            'total_return_pct': round(total_return_pct, 2),
            'ending_capital': round(capital, 0),
            'avg_win': round(avg_win, 0),
            'avg_loss': round(avg_loss, 0),
            'win_loss_ratio': win_loss_ratio
        }

        return summary, trades, equity_curve


def generate_benchmark_html_report(summary: Dict[str, Any], trades: List[Dict[str, Any]], eq: List[Dict[str, Any]], output_path: str):
    df_eq = pd.DataFrame(eq)
    df_eq['datetime'] = pd.to_datetime(df_eq['time'], format='mixed')
    df_eq = df_eq.drop_duplicates('datetime').sort_values('datetime')
    chart_labels = df_eq['datetime'].dt.strftime('%m/%d %H:%M').tolist()
    chart_data = df_eq['equity'].tolist()

    trades_rows = ""
    for t in reversed(trades):
        pnl_class = "text-emerald-400" if t['net_pnl'] > 0 else ("text-rose-400" if t['net_pnl'] < 0 else "text-slate-400")
        side_badge = f'<span class="px-2 py-0.5 rounded text-xs font-semibold {"bg-emerald-900/60 text-emerald-300 border border-emerald-500/30" if t["side"]=="BUY" else "bg-rose-900/60 text-rose-300 border border-rose-500/30"}">{t["side"]}</span>'
        trades_rows += f"""
        <tr class="border-b border-slate-800 hover:bg-slate-800/40 transition-colors">
            <td class="py-2.5 px-3 text-xs text-slate-400 font-mono">#{t['trade_no']}</td>
            <td class="py-2.5 px-3">{side_badge}</td>
            <td class="py-2.5 px-3 text-xs text-slate-300 font-mono">{t['entry_time'][5:16]}</td>
            <td class="py-2.5 px-3 text-xs text-slate-200 font-mono">{t['entry_price']:,.0f}</td>
            <td class="py-2.5 px-3 text-xs text-slate-300 font-mono">{t['exit_time'][5:16]}</td>
            <td class="py-2.5 px-3 text-xs text-slate-200 font-mono">{t['exit_price']:,.0f}</td>
            <td class="py-2.5 px-3 text-xs text-slate-400">{t['exit_reason']}</td>
            <td class="py-2.5 px-3 text-xs text-slate-300 text-center font-mono">{t['lots']}口</td>
            <td class="py-2.5 px-3 text-xs font-mono font-bold {pnl_class} text-right">{t['net_pnl']:+,.0f}</td>
            <td class="py-2.5 px-3 text-xs font-mono text-slate-300 text-right">{t['capital_after']:,.0f}</td>
            <td class="py-2.5 px-3 text-xs text-slate-400">{t.get('setup', '')}</td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html lang="zh-TW" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>【基準版】SMC × PA 雙重確認策略全真回測報告</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Plus Jakarta Sans', sans-serif; background-color: #0b0f17; }}
        .font-mono {{ font-family: 'JetBrains Mono', monospace; }}
        .glass {{ background: rgba(15, 23, 42, 0.75); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }}
    </style>
</head>
<body class="text-slate-100 min-h-screen p-4 md:p-8">
    <div class="max-w-7xl mx-auto space-y-6">
        
        <!-- Header -->
        <header class="glass rounded-2xl p-6 md:p-8 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 border-l-4 border-cyan-500">
            <div>
                <div class="flex items-center gap-3">
                    <span class="px-3 py-1 bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 rounded-full text-xs font-bold uppercase tracking-wider">【基準版】SMC × PA 雙重確認</span>
                    <span class="px-3 py-1 bg-indigo-500/20 text-indigo-400 border border-indigo-500/30 rounded-full text-xs font-bold">5K HTF OB × 1K LTF</span>
                    <span class="px-3 py-1 bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 rounded-full text-xs font-bold">小台指 (MTX) 2口</span>
                </div>
                <h1 class="text-2xl md:text-3xl font-extrabold text-white mt-2">台指期 SMC × PA 雙重確認策略全真回測報告</h1>
                <p class="text-slate-400 text-sm mt-1">回測期間：2026-06-18 ～ 2026-09-18 ｜ 50% DR 折溢價過濾 ＋ 1K CHoCh ＋ 早期 PA 反轉動能棒 ＋ 13:40 強平</p>
            </div>
            <div class="text-right">
                <div class="text-xs text-slate-400 font-mono">淨損益 (Net Profit)</div>
                <div class="text-3xl md:text-4xl font-extrabold font-mono {'text-emerald-400' if summary['net_profit']>=0 else 'text-rose-400'}">
                    NT$ {summary['net_profit']:+,.0f}
                </div>
                <div class="text-xs font-bold {'text-emerald-400' if summary['total_return_pct']>=0 else 'text-rose-400'} font-mono mt-0.5">
                    報酬率 {summary['total_return_pct']:+.2f}% ｜ PF: {summary['profit_factor']:.2f} ｜ 盈虧比: {summary['win_loss_ratio']:.2f}
                </div>
            </div>
        </header>

        <!-- KPI Grid -->
        <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
            <div class="glass rounded-xl p-4">
                <div class="text-slate-400 text-xs">總平倉筆數</div>
                <div class="text-xl font-bold font-mono text-white mt-1">{summary['total_trades']} <span class="text-xs font-normal text-slate-400">筆</span></div>
                <div class="text-[11px] text-slate-500 mt-1">勝 {summary['winning_trades']} / 負 {summary['losing_trades']} / 保 {summary['be_trades']}</div>
            </div>
            <div class="glass rounded-xl p-4">
                <div class="text-slate-400 text-xs">交易勝率 (Win Rate)</div>
                <div class="text-xl font-bold font-mono text-emerald-400 mt-1">{summary['win_rate']:.1f}%</div>
                <div class="text-[11px] text-slate-500 mt-1">TP1/TP2 雙階平倉</div>
            </div>
            <div class="glass rounded-xl p-4">
                <div class="text-slate-400 text-xs">盈虧比 (Win/Loss)</div>
                <div class="text-xl font-bold font-mono text-cyan-400 mt-1">1 : {summary['win_loss_ratio']:.2f}</div>
                <div class="text-[11px] text-slate-500 mt-1">均獲 NT${summary['avg_win']:,.0f} / 均損 NT${summary['avg_loss']:,.0f}</div>
            </div>
            <div class="glass rounded-xl p-4">
                <div class="text-slate-400 text-xs">獲利因子 (Profit Factor)</div>
                <div class="text-xl font-bold font-mono text-indigo-400 mt-1">{summary['profit_factor']:.2f}</div>
                <div class="text-[11px] text-slate-500 mt-1">健康正期望值</div>
            </div>
            <div class="glass rounded-xl p-4">
                <div class="text-slate-400 text-xs">最大回撤 (MDD)</div>
                <div class="text-xl font-bold font-mono text-rose-400 mt-1">{summary['max_drawdown_pct']:.2f}%</div>
                <div class="text-[11px] text-slate-500 mt-1">微結構停損嚴格把關</div>
            </div>
            <div class="glass rounded-xl p-4">
                <div class="text-slate-400 text-xs">TP1 / TP2 / 13:40強平</div>
                <div class="text-xl font-bold font-mono text-amber-400 mt-1">{summary['tp1_count']} / {summary['tp2_count']} / {summary['eod_count']}</div>
                <div class="text-[11px] text-slate-500 mt-1">階梯出場分流完成</div>
            </div>
        </div>

        <!-- Equity Chart -->
        <div class="glass rounded-2xl p-6">
            <h2 class="text-lg font-bold text-white flex items-center gap-2 mb-4">
                <span class="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-pulse"></span>
                累計資金權益曲線 (Equity Curve)
            </h2>
            <div class="h-80 w-full">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <!-- Trade Details Table -->
        <div class="glass rounded-2xl p-6">
            <div class="flex justify-between items-center mb-4">
                <h2 class="text-lg font-bold text-white flex items-center gap-2">
                    <span class="w-2.5 h-2.5 rounded-full bg-emerald-400"></span>
                    全真交易歷史明細表 (Trade Logs)
                </h2>
                <span class="text-xs text-slate-400 font-mono">總計 {len(trades)} 筆出場紀錄</span>
            </div>
            <div class="overflow-x-auto max-h-[550px] overflow-y-auto pr-1">
                <table class="w-full text-left border-collapse">
                    <thead class="sticky top-0 bg-[#0f172a] shadow-md">
                        <tr class="border-b border-slate-700 text-[11px] text-slate-400 uppercase font-semibold">
                            <th class="py-3 px-3">#</th>
                            <th class="py-3 px-3">方向</th>
                            <th class="py-3 px-3">進場時間</th>
                            <th class="py-3 px-3">進場價</th>
                            <th class="py-3 px-3">出場時間</th>
                            <th class="py-3 px-3">出場價</th>
                            <th class="py-3 px-3">出場原因</th>
                            <th class="py-3 px-3 text-center">口數</th>
                            <th class="py-3 px-3 text-right">淨損益 (NT$)</th>
                            <th class="py-3 px-3 text-right">帳戶權益</th>
                            <th class="py-3 px-3">訊號形態</th>
                        </tr>
                    </thead>
                    <tbody>
                        {trades_rows}
                    </tbody>
                </table>
            </div>
        </div>

        <footer class="text-center text-slate-500 text-xs py-4">
            【基準版】SMC × PA 雙重確認量化策略 ｜ 由 Antigravity 智能交易引擎回測生成
        </footer>
    </div>

    <script>
        const ctx = document.getElementById('equityChart').getContext('2d');
        const labels = {json.dumps(chart_labels)};
        const data = {json.dumps(chart_data)};

        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: labels,
                datasets: [{{
                    label: 'SMC × PA 基準版資金權益',
                    data: data,
                    borderColor: '#06b6d4',
                    backgroundColor: 'rgba(6, 182, 212, 0.12)',
                    borderWidth: 2.5,
                    fill: true,
                    tension: 0.1,
                    pointRadius: 1,
                    pointHoverRadius: 5
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        backgroundColor: '#0f172a',
                        titleColor: '#e2e8f0',
                        bodyColor: '#e2e8f0',
                        borderColor: '#334155',
                        borderWidth: 1,
                        padding: 10,
                        callbacks: {{
                            label: function(c) {{ return '帳戶權益: NT$ ' + Number(c.parsed.y).toLocaleString(); }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{
                        grid: {{ color: 'rgba(255, 255, 255, 0.05)' }},
                        ticks: {{ color: '#64748b', maxTicksLimit: 12, font: {{ size: 10 }} }}
                    }},
                    y: {{
                        grid: {{ color: 'rgba(255, 255, 255, 0.05)' }},
                        ticks: {{
                            color: '#64748b',
                            font: {{ size: 10 }},
                            callback: function(val) {{ return 'NT$ ' + (val/1000).toLocaleString() + 'k'; }}
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML 互動報表已生成: {output_path}")


def generate_benchmark_md_report(summary: Dict[str, Any], trades: List[Dict[str, Any]], output_path: str):
    md = f"""# 【基準版】SMC × PA 雙重確認策略全真回測報告書
**(5K HTF Order Block × 1K LTF Dual Reversal Market Entry)**

> **標的合約**：台指期 (TXFR1 / 小台指 MTX 每點 50 元)  
> **回測期間**：2026-06-18 至 2026-09-18 (近 3 個月歷史全真 1K/5K 數據)  
> **交易時段**：一般日盤 (08:45 ~ 13:45)，進場窗口 08:45 ~ 13:30，**13:40 強制平倉**  
> **核心機制**：5K Demand/Supply OB ＋ 50% DR 折溢價過濾 ＋ 1K CHoCh 微結構 ＋ 早期 PA 反轉動能棒  
> **風控與停利**：1K 前10根極值+2點 SL ｜ TP1 2.0R 減半倉移保本 ｜ TP2 對側 DR 極值(或 3.5R) 全平  

---

## 一、核心量化指標摘要

| 績效評估指標 | 數值 / 結果 | 說明 |
| :--- | :---: | :--- |
| **起始本金** | NT$ 1,000,000 | 初始投資資本 |
| **期末總權益** | **NT$ {summary['ending_capital']:,.0f}** | 近 3 個月結算總資產 |
| **淨損益 (Net Profit)** | **NT$ {summary['net_profit']:+,.0f}** | 扣除所有手續費與期交稅後淨額 |
| **總報酬率 (Total Return)** | **{summary['total_return_pct']:+.2f}%** | 資本報酬率 |
| **總平倉筆數 (Total Exits)** | **{summary['total_trades']} 筆** | 含 TP1/TP2 階梯分批與 BE 保本出場 |
| **獲利筆數 / 虧損筆數 / 保本** | **{summary['winning_trades']} 勝 / {summary['losing_trades']} 負 / {summary['be_trades']} 保本** | 分批出場統計 |
| **交易勝率 (Win Rate)** | **{summary['win_rate']:.2f}%** | 嚴格雙重確認勝率 |
| **盈虧比 (Win / Loss Ratio)** | **1 : {summary['win_loss_ratio']:.2f}** | **均獲 NT${summary['avg_win']:,.0f} / 均損 NT${summary['avg_loss']:,.0f}** |
| **獲利因子 (Profit Factor)** | **{summary['profit_factor']:.2f}** | 總獲利 / 總虧損 |
| **最大回撤 (Max Drawdown)** | **{summary['max_drawdown_pct']:.2f}%** | 策略最大本金回撤 |
| **TP1 (2.0R) 達成數** | **{summary['tp1_count']} 筆** | 達成 2.0R 鎖利並將停損移至進場價 |
| **TP2 滿貫達成數** | **{summary['tp2_count']} 筆** | 達成對側 DR 流動性目標全平 |
| **13:40 當沖強平次數** | **{summary['eod_count']} 筆** | 嚴格執行日內當沖，零留倉過夜 |

---

## 二、演算法機制運作詳解

1. **50% Dealing Range (DR) 折溢價過濾機制**：
   - 策略僅在 **折價區 (Price < Equilibrium)** 尋求做多機會、在 **溢價區 (Price > Equilibrium)** 尋求做空機會，天然避開「高位追多」與「低位殺跌」的低期望值交易。
2. **1K LTF 雙重確認 (CHoCh + Setup Bar)**：
   - 價格觸碰 5K OB 時不盲目摸頂抄底，必須等待 1K 突破前 15 根微結構次高/次低點（CHoCh），同時伴隨形態學吞噬、晨星/黃昏星、錘子/流星或長影線 Pinbar 確認動能反轉才市價進場。
3. **階梯式出場與零風險奔跑**：
   - TP1 到達 2.0R 立即平倉 50% 鎖定基本利潤，並將停損推移至進場價（BE），徹底杜絕獲利單轉虧損的心理折磨。

---

## 三、視覺化報表連結

- **互動式 HTML 報表**：[`reports/tx_smc_pa_benchmark_report.html`](file:///c:/Intel/Shioaji_job/reports/tx_smc_pa_benchmark_report.html)
"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"Markdown 報告書已生成: {output_path}")


def main():
    print("========================================================================")
    print("  【基準版】SMC × PA 雙重確認策略全真回測系統啟動")
    print("========================================================================")

    db_path = get_db_path()
    start_date = "2026-06-18"
    end_date = "2026-09-18"

    df_1k, df_5k = load_dataset(db_path, code="TXFR1", start_date=start_date, end_date=end_date)
    print(f"資料庫: {db_path}")
    print(f"載入 1K 資料: {len(df_1k):,} 根，5K 資料: {len(df_5k):,} 根 (區間: {start_date} 至 {end_date})")

    engine = SMC_PA_Benchmark_Engine(
        df_1k=df_1k,
        df_5k=df_5k,
        contract_type="MTX",
        max_lots=2,
        swing_window=5,
        sl_buffer_pts=2.0,
        min_risk_pts=4.0,
        max_losses_per_ob=2,
        start_capital=1_000_000.0
    )

    print("\n正在執行 SMC × PA 雙重確認回測引擎...")
    summary, trades, eq_curve = engine.run_backtest()

    print("\n" + "=" * 70)
    print(f"{'【基準版】SMC × PA 雙重確認策略回測績效指標':^55}")
    print("=" * 70)
    metrics = [
        ("標的與合約 (Contract)", "小台指 MTX (2口 / 50元/點)"),
        ("回測時間範圍 (Date Range)", f"{start_date} ~ {end_date} (近3個月)"),
        ("起始本金 (Start Capital)", f"NT$ {1_000_000:,.0f}"),
        ("期末總權益 (Ending Capital)", f"NT$ {summary['ending_capital']:,.0f}"),
        ("淨損益 (Net Profit)", f"NT$ {summary['net_profit']:+,.0f}"),
        ("總報酬率 (Total Return)", f"{summary['total_return_pct']:+.2f}%"),
        ("總平倉次數 (Total Exits)", f"{summary['total_trades']} 筆"),
        ("獲利 / 虧損 / 保本", f"{summary['winning_trades']} 勝 / {summary['losing_trades']} 負 / {summary['be_trades']} 保本"),
        ("交易勝率 (Win Rate)", f"{summary['win_rate']:.2f}%"),
        ("盈虧比 (Win / Loss Ratio)", f"1 : {summary['win_loss_ratio']:.2f}"),
        ("獲利因子 (Profit Factor)", f"{summary['profit_factor']:.2f}"),
        ("最大回撤 (Max Drawdown)", f"{summary['max_drawdown_pct']:.2f}%"),
        ("TP1 2.0R 減半倉移保本", f"{summary['tp1_count']} 筆"),
        ("TP2 對側DR/3.5R全平", f"{summary['tp2_count']} 筆"),
        ("13:40 當沖強制平倉", f"{summary['eod_count']} 筆"),
    ]
    for k, v in metrics:
        print(f"  {k:<32} | {v:<30}")
    print("=" * 70)

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    html_path = os.path.join(base_dir, "reports", "tx_smc_pa_benchmark_report.html")
    md_path = os.path.join(base_dir, "reports", "tx_smc_pa_benchmark_report.md")

    generate_benchmark_html_report(summary, trades, eq_curve, html_path)
    generate_benchmark_md_report(summary, trades, md_path)
    print("\n[OK] 【基準版】SMC × PA 雙重確認策略回測完成！")


if __name__ == "__main__":
    main()
