# -*- coding: utf-8 -*-
"""
台指期【純日盤 10:00-11:59】5K OB + 1K FVG 超窄停損狙擊 vs 傳統對照 回測報告
- 交易時段: 純日盤黃金窗口 (10:00 - 11:59)
- 合約標的: 小台指 (MTX - NT$ 50/pt)
- 口數限制: 嚴格上限 2 口 (Max Lots = 2)
- 階梯停利: TP1 2.0R (平1口移保本) / TP2 3.0R (剩餘1口全平)
- 評估區間: 2024-01-01 至 2026-09-16 全歷史 (742k 1K Bars)
"""

import os
import sys
import json
import sqlite3
import argparse
from datetime import datetime, time
from typing import Tuple, List, Dict, Any, Optional
import pandas as pd
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine, OrderBlockTracker
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data


class SniperOrderBlockEngine(OrderBlockEngine):
    """
    純日盤 10:00-11:59 狙擊回測引擎
    """
    def __init__(self, df: pd.DataFrame, order_blocks: List[Any], config: OrderBlockConfig, mode: str = "sniper_narrow_sl"):
        self.df = df
        self.order_blocks = [ob for ob in order_blocks]
        self.config = config
        self.mode = mode

    def run_backtest(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        if self.df.empty:
            self.df, self.order_blocks = OrderBlockTracker.prepare_dataset(self.df_1k, self.df_5k, self.config)

        df = self.df
        n = len(df)
        if n == 0:
            return {}, [], []

        # 計算 1K FVG
        fvg_bull = (df['low'] > df['high'].shift(2)) & (df['close'] > df['open'])
        fvg_bear = (df['high'] < df['low'].shift(2)) & (df['close'] < df['open'])
        fvg_bull_vals = fvg_bull.values
        fvg_bear_vals = fvg_bear.values

        # 計算 1K 近端 5 根擺動高低點
        swing_l_vals = df['low'].rolling(window=5, min_periods=1).min().values
        swing_h_vals = df['high'].rolling(window=5, min_periods=1).max().values

        times = df['datetime'].values
        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        sessions = df['session'].values
        session_groups = df['session_group'].values
        htf_ema_fast = df['htf_ema_fast'].values
        htf_ema_slow = df['htf_ema_slow'].values

        capital = float(self.config.start_capital)
        peak_capital = capital
        max_drawdown = 0.0
        max_drawdown_pct = 0.0

        position = 0
        lots_total = 0
        lots_remaining = 0
        entry_price = 0.0
        entry_time = None
        stop_loss = 0.0
        tp1_price = 0.0
        tp2_price = 0.0
        risk_points = 0.0
        is_tp1_filled = False
        trade_indicators = {}

        trades = []
        equity_curve = []

        active_obs = sorted(self.order_blocks, key=lambda x: x.formed_time)
        ob_cursor = 0
        current_active_obs = []
        session_attempts = {}

        for i in range(n):
            t_cur = pd.to_datetime(times[i])
            o_cur = opens[i]
            h_cur = highs[i]
            l_cur = lows[i]
            c_cur = closes[i]

            s_grp = session_groups[i]
            if s_grp not in session_attempts:
                session_attempts[s_grp] = {'long': 0, 'short': 0}

            curr_sess = sessions[i]
            prev_sess = sessions[i - 1] if i > 0 else curr_sess

            # 推進有效 OB
            while ob_cursor < len(active_obs) and active_obs[ob_cursor].formed_time <= t_cur:
                current_active_obs.append(active_obs[ob_cursor])
                ob_cursor += 1

            valid_obs = []
            for ob in current_active_obs:
                if (t_cur - ob.formed_time).total_seconds() > 3600 * 12:
                    ob.status = "expired"
                    continue
                if ob.ob_type == "BULLISH" and c_cur < ob.bottom - 10.0:
                    ob.status = "invalidated"
                    continue
                if ob.ob_type == "BEARISH" and c_cur > ob.top + 10.0:
                    ob.status = "invalidated"
                    continue
                if ob.status == "active":
                    valid_obs.append(ob)
            current_active_obs = valid_obs

            # A. 持倉處理
            if position != 0:
                triggered_exit = False
                exit_price = 0.0
                exit_reason = ""
                exit_lots = 0

                # 日盤結束強制平倉 (13:45)
                if curr_sess != 'day' or (prev_sess == 'day' and curr_sess != 'day'):
                    triggered_exit = True
                    exit_price = closes[i - 1]
                    exit_reason = "EOD (日盤收盤強平)"
                    exit_lots = lots_remaining

                if not triggered_exit:
                    if position == 1:
                        if not is_tp1_filled:
                            if l_cur <= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "SL (止損)"
                                exit_lots = lots_remaining
                            elif h_cur >= tp1_price:
                                half_lots = int(lots_total // 2)
                                pnl_pts = tp1_price - entry_price
                                gross_pnl = pnl_pts * half_lots * self.config.point_value
                                costs = self.calculate_costs(entry_price, tp1_price, half_lots)
                                net_pnl = gross_pnl - costs
                                capital += net_pnl

                                trades.append({
                                    'trade_no': len(trades) + 1,
                                    'stage': 'TP1_HALF',
                                    'side': 'BUY',
                                    'entry_time': str(entry_time),
                                    'entry_price': float(entry_price),
                                    'exit_time': str(t_cur),
                                    'exit_price': float(tp1_price),
                                    'exit_reason': "TP1 (2.0R 鎖利移保本)",
                                    'lots': int(half_lots),
                                    'risk_points': float(risk_points),
                                    'pnl_points': float(pnl_pts),
                                    'net_pnl': float(net_pnl),
                                    'capital_after': float(capital)
                                })

                                lots_remaining -= half_lots
                                is_tp1_filled = True
                                stop_loss = max(stop_loss, entry_price)
                        else:
                            if h_cur >= tp2_price:
                                triggered_exit = True
                                exit_price = tp2_price
                                exit_reason = "TP2 (3.0R 目標全平)"
                                exit_lots = lots_remaining
                            elif l_cur <= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "BE (保本出場)" if abs(stop_loss - entry_price) < 0.1 else "SL (止損)"
                                exit_lots = lots_remaining

                    elif position == -1:
                        if not is_tp1_filled:
                            if h_cur >= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "SL (止損)"
                                exit_lots = lots_remaining
                            elif l_cur <= tp1_price:
                                half_lots = int(lots_total // 2)
                                pnl_pts = entry_price - tp1_price
                                gross_pnl = pnl_pts * half_lots * self.config.point_value
                                costs = self.calculate_costs(entry_price, tp1_price, half_lots)
                                net_pnl = gross_pnl - costs
                                capital += net_pnl

                                trades.append({
                                    'trade_no': len(trades) + 1,
                                    'stage': 'TP1_HALF',
                                    'side': 'SELL',
                                    'entry_time': str(entry_time),
                                    'entry_price': float(entry_price),
                                    'exit_time': str(t_cur),
                                    'exit_price': float(tp1_price),
                                    'exit_reason': "TP1 (2.0R 鎖利移保本)",
                                    'lots': int(half_lots),
                                    'risk_points': float(risk_points),
                                    'pnl_points': float(pnl_pts),
                                    'net_pnl': float(net_pnl),
                                    'capital_after': float(capital)
                                })

                                lots_remaining -= half_lots
                                is_tp1_filled = True
                                stop_loss = min(stop_loss, entry_price)
                        else:
                            if l_cur <= tp2_price:
                                triggered_exit = True
                                exit_price = tp2_price
                                exit_reason = "TP2 (3.0R 目標全平)"
                                exit_lots = lots_remaining
                            elif h_cur >= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "BE (保本出場)" if abs(stop_loss - entry_price) < 0.1 else "SL (止損)"
                                exit_lots = lots_remaining

                if triggered_exit:
                    pnl_pts = (exit_price - entry_price) if position == 1 else (entry_price - exit_price)
                    gross_pnl = pnl_pts * exit_lots * self.config.point_value
                    costs = self.calculate_costs(entry_price, exit_price, exit_lots)
                    net_pnl = gross_pnl - costs
                    capital += net_pnl

                    trades.append({
                        'trade_no': len(trades) + 1,
                        'stage': 'TP2_RUNNER' if is_tp1_filled else 'FULL',
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
                        'capital_after': float(capital)
                    })

                    position = 0
                    lots_total = 0
                    lots_remaining = 0
                    entry_price = 0.0
                    entry_time = None
                    stop_loss = 0.0
                    is_tp1_filled = False

            # 記錄權益
            if capital > peak_capital:
                peak_capital = capital
            dd = peak_capital - capital
            dd_pct = (dd / peak_capital) * 100.0 if peak_capital > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd
                max_drawdown_pct = dd_pct

            equity_curve.append({
                'time': str(t_cur),
                'capital': float(capital),
                'drawdown_ntd': float(dd),
                'drawdown_pct': float(dd_pct)
            })

            # B. 新進場檢查 (純日盤 10:00 - 11:59)
            if position == 0 and curr_sess == 'day':
                t = t_cur.time()
                if time(10, 0) <= t < time(12, 0):
                    trend_bull = (htf_ema_fast[i] > htf_ema_slow[i]) if not pd.isna(htf_ema_fast[i]) else True
                    trend_bear = (htf_ema_fast[i] < htf_ema_slow[i]) if not pd.isna(htf_ema_fast[i]) else True

                    # 1. 狙擊模式 (5K OB + 1K FVG)
                    if self.mode in ["sniper_narrow_sl", "sniper_5k_sl"]:
                        for ob in current_active_obs:
                            if ob.trade_count >= self.config.max_attempts_per_ob:
                                continue

                            # 看多回踩 + 1K 看多 FVG
                            if ob.ob_type == "BULLISH" and l_cur <= ob.top and h_cur >= ob.bottom:
                                if trend_bull and (fvg_bull_vals[i] or (i > 0 and fvg_bull_vals[i-1])):
                                    if session_attempts[s_grp]['long'] < self.config.max_attempts_per_session:
                                        position = 1
                                        lots_total = 2
                                        lots_remaining = 2
                                        entry_price = c_cur
                                        entry_time = t_cur

                                        if self.mode == "sniper_narrow_sl":
                                            # 1K 超窄停損 (1K Swing Low，保底 15 點)
                                            raw_sl = swing_l_vals[i] - 2.0
                                            risk_pts = max(15.0, entry_price - raw_sl)
                                            stop_loss = entry_price - risk_pts
                                        else:
                                            # 5K OB 穩健停損
                                            sl_base = ob.bottom - self.config.sl_buffer_points
                                            risk_pts = max(25.0, entry_price - sl_base)
                                            stop_loss = entry_price - risk_pts

                                        risk_points = risk_pts
                                        tp1_price = entry_price + (risk_points * 2.0)
                                        tp2_price = entry_price + (risk_points * 3.0)

                                        ob.trade_count += 1
                                        session_attempts[s_grp]['long'] += 1
                                        break

                            # 看空回踩 + 1K 看空 FVG
                            elif ob.ob_type == "BEARISH" and h_cur >= ob.bottom and l_cur <= ob.top:
                                if trend_bear and (fvg_bear_vals[i] or (i > 0 and fvg_bear_vals[i-1])):
                                    if session_attempts[s_grp]['short'] < self.config.max_attempts_per_session:
                                        position = -1
                                        lots_total = 2
                                        lots_remaining = 2
                                        entry_price = c_cur
                                        entry_time = t_cur

                                        if self.mode == "sniper_narrow_sl":
                                            # 1K 超窄停損 (1K Swing High，保底 15 點)
                                            raw_sl = swing_h_vals[i] + 2.0
                                            risk_pts = max(15.0, raw_sl - entry_price)
                                            stop_loss = entry_price + risk_pts
                                        else:
                                            sl_base = ob.top + self.config.sl_buffer_points
                                            risk_pts = max(25.0, sl_base - entry_price)
                                            stop_loss = entry_price + risk_pts

                                        risk_points = risk_pts
                                        tp1_price = entry_price - (risk_points * 2.0)
                                        tp2_price = entry_price - (risk_points * 3.0)

                                        ob.trade_count += 1
                                        session_attempts[s_grp]['short'] += 1
                                        break

                    # 2. 純 5K 訂單塊 (標準機構 OB)
                    elif self.mode == "pure_5k_ob":
                        bar_range = max(1.0, h_cur - l_cur)
                        lower_wick = min(o_cur, c_cur) - l_cur
                        upper_wick = h_cur - max(o_cur, c_cur)

                        for ob in current_active_obs:
                            if ob.trade_count >= self.config.max_attempts_per_ob:
                                continue

                            if ob.ob_type == "BULLISH" and l_cur <= ob.top and h_cur >= ob.bottom:
                                confirmed = (lower_wick / bar_range >= self.config.min_wick_ratio) or (c_cur >= ob.top)
                                if confirmed and trend_bull:
                                    if session_attempts[s_grp]['long'] < self.config.max_attempts_per_session:
                                        position = 1
                                        lots_total = 2
                                        lots_remaining = 2
                                        entry_price = c_cur
                                        entry_time = t_cur
                                        sl_base = ob.bottom - self.config.sl_buffer_points
                                        risk_pts = max(25.0, entry_price - sl_base)
                                        stop_loss = entry_price - risk_pts
                                        risk_points = risk_pts
                                        tp1_price = entry_price + (risk_points * 2.0)
                                        tp2_price = entry_price + (risk_points * 3.0)
                                        ob.trade_count += 1
                                        session_attempts[s_grp]['long'] += 1
                                        break

                            elif ob.ob_type == "BEARISH" and h_cur >= ob.bottom and l_cur <= ob.top:
                                confirmed = (upper_wick / bar_range >= self.config.min_wick_ratio) or (c_cur <= ob.bottom)
                                if confirmed and trend_bear:
                                    if session_attempts[s_grp]['short'] < self.config.max_attempts_per_session:
                                        position = -1
                                        lots_total = 2
                                        lots_remaining = 2
                                        entry_price = c_cur
                                        entry_time = t_cur
                                        sl_base = ob.top + self.config.sl_buffer_points
                                        risk_pts = max(25.0, sl_base - entry_price)
                                        stop_loss = entry_price + risk_pts
                                        risk_points = risk_pts
                                        tp1_price = entry_price - (risk_points * 2.0)
                                        tp2_price = entry_price - (risk_points * 3.0)
                                        ob.trade_count += 1
                                        session_attempts[s_grp]['short'] += 1
                                        break

                    # 3. 純 1K 銀色子彈 (10:00-11:59)
                    elif self.mode == "pure_1k_sb":
                        if fvg_bull_vals[i] and trend_bull:
                            if session_attempts[s_grp]['long'] < self.config.max_attempts_per_session:
                                position = 1
                                lots_total = 2
                                lots_remaining = 2
                                entry_price = c_cur
                                entry_time = t_cur
                                raw_sl = swing_l_vals[i] - 2.0
                                risk_pts = max(15.0, entry_price - raw_sl)
                                stop_loss = entry_price - risk_pts
                                risk_points = risk_pts
                                tp1_price = entry_price + (risk_points * 2.0)
                                tp2_price = entry_price + (risk_points * 3.0)
                                session_attempts[s_grp]['long'] += 1

                        elif fvg_bear_vals[i] and trend_bear:
                            if session_attempts[s_grp]['short'] < self.config.max_attempts_per_session:
                                position = -1
                                lots_total = 2
                                lots_remaining = 2
                                entry_price = c_cur
                                entry_time = t_cur
                                raw_sl = swing_h_vals[i] + 2.0
                                risk_pts = max(15.0, raw_sl - entry_price)
                                stop_loss = entry_price + risk_pts
                                risk_points = risk_pts
                                tp1_price = entry_price - (risk_points * 2.0)
                                tp2_price = entry_price - (risk_points * 3.0)
                                session_attempts[s_grp]['short'] += 1

        total_pnl = capital - float(self.config.start_capital)
        total_trades = len(trades)
        wins = [t for t in trades if t['net_pnl'] > 0]
        losses = [t for t in trades if t['net_pnl'] <= 0]
        win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0

        gross_profit = sum(t['net_pnl'] for t in wins)
        gross_loss = abs(sum(t['net_pnl'] for t in losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        yearly_pnl = {'2024': 0.0, '2025': 0.0, '2026': 0.0}
        for t in trades:
            yr = t['entry_time'][:4]
            if yr in yearly_pnl:
                yearly_pnl[yr] += t['net_pnl']

        stats = {
            'mode': self.mode,
            'total_trades': total_trades,
            'win_rate': round(win_rate, 1),
            'profit_factor': round(profit_factor, 2),
            'total_pnl': round(total_pnl, 1),
            'max_drawdown_ntd': round(max_drawdown, 1),
            'max_drawdown_pct': round(max_drawdown_pct, 2),
            'final_capital': round(capital, 1),
            'yearly_pnl': {k: round(v, 1) for k, v in yearly_pnl.items()}
        }

        return stats, trades, equity_curve


def generate_html_report(results: List[Dict[str, Any]], output_path: str):
    labels = []
    sample_size = 1000
    ref_curve = results[0]['equity_curve']
    step = max(1, len(ref_curve) // sample_size)
    chart_times = [ref_curve[i]['time'] for i in range(0, len(ref_curve), step)]

    datasets = []
    colors = ['#10b981', '#3b82f6', '#f59e0b', '#ec4899']

    for idx, r in enumerate(results):
        curve = r['equity_curve']
        pts = [curve[i]['capital'] for i in range(0, len(curve), step)]
        pts = pts[:len(chart_times)]
        datasets.append({
            'label': r['name'],
            'borderColor': colors[idx % len(colors)],
            'backgroundColor': 'transparent',
            'data': pts,
            'borderWidth': 3 if idx == 0 else 1.8,
            'pointRadius': 0,
            'tension': 0.1
        })

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>【純日盤 10:00-11:59】5K OB + 1K FVG 超窄停損狙擊回測報告</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-base: #080c14;
            --bg-card: rgba(14, 20, 32, 0.9);
            --border: rgba(255, 255, 255, 0.08);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #3b82f6;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Outfit', sans-serif; }}
        body {{ background: var(--bg-base); color: var(--text-main); padding: 32px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        header {{ margin-bottom: 28px; border-bottom: 1px solid var(--border); padding-bottom: 20px; }}
        h1 {{ font-size: 26px; font-weight: 700; color: #fff; }}
        h1 span {{ color: #10b981; }}
        p.sub {{ color: var(--text-muted); font-size: 14px; margin-top: 6px; }}
        .card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 14px; padding: 24px; margin-bottom: 24px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; margin-top: 12px; }}
        th {{ color: var(--text-muted); padding: 12px 14px; border-bottom: 1px solid var(--border); font-weight: 600; background: rgba(255,255,255,0.02); }}
        td {{ padding: 12px 14px; border-bottom: 1px solid rgba(255,255,255,0.04); font-family: 'JetBrains Mono', monospace; }}
        .val-profit {{ color: var(--success); }}
        .val-loss {{ color: var(--danger); }}
        .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
        .highlight-row {{ background: rgba(16, 185, 129, 0.08); border-left: 3px solid #10b981; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>☀️ 5K OB + 1K FVG <span>【純日盤黃金窗口超窄狙擊】量化回測報告</span></h1>
            <p class="sub">合約標的: 小台指 (MTX) | 歷史區間: 2024-01-01 ~ 2026-09-16 (全歷史 742k 根 1K) | 嚴格 2 口限制、2.0R / 3.0R 階梯停利</p>
        </header>

        <div class="card">
            <h2 style="font-size: 18px; margin-bottom: 16px;">📊 策略對比矩陣 (純日盤 10:00 - 11:59)</h2>
            <table>
                <thead>
                    <tr>
                        <th>策略架構方案</th>
                        <th>核心機制與停損特性</th>
                        <th>總交易次數</th>
                        <th>勝率 (%)</th>
                        <th>獲利因子 (PF)</th>
                        <th>最大回撤 (MDD)</th>
                        <th>總淨損益 (NTD)</th>
                        <th>2024 年</th>
                        <th>2025 年</th>
                        <th>2026 年</th>
                    </tr>
                </thead>
                <tbody>
"""

    for idx, r in enumerate(results):
        st = r['stats']
        is_best = (idx == 0)
        row_cls = 'class="highlight-row"' if is_best else ''
        pnl_cls = "val-profit" if st['total_pnl'] > 0 else "val-loss"
        pnl_sign = "+" if st['total_pnl'] > 0 else ""
        y24_cls = "val-profit" if st['yearly_pnl']['2024'] > 0 else "val-loss"
        y25_cls = "val-profit" if st['yearly_pnl']['2025'] > 0 else "val-loss"
        y26_cls = "val-profit" if st['yearly_pnl']['2026'] > 0 else "val-loss"

        html += f"""
                    <tr {row_cls}>
                        <td style="font-weight: 700; color: #fff;">{r['name']}</td>
                        <td style="color: var(--text-muted); font-size: 12px;">{r['desc']}</td>
                        <td>{st['total_trades']} 筆</td>
                        <td>{st['win_rate']}%</td>
                        <td style="color: #60a5fa; font-weight: bold;">{st['profit_factor']}</td>
                        <td class="{pnl_cls}">{st['max_drawdown_pct']}%</td>
                        <td class="{pnl_cls}" style="font-weight: bold;">NT$ {pnl_sign}{st['total_pnl']:,}</td>
                        <td class="{y24_cls}">NT$ {st['yearly_pnl']['2024']:,}</td>
                        <td class="{y25_cls}">NT$ {st['yearly_pnl']['2025']:,}</td>
                        <td class="{y26_cls}">NT$ {st['yearly_pnl']['2026']:,}</td>
                    </tr>
        """

    html += f"""
                </tbody>
            </table>
        </div>

        <div class="card">
            <h2 style="font-size: 18px; margin-bottom: 16px;">📈 全歷史資金權益曲線 (Equity Curves)</h2>
            <div style="height: 440px;">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <div class="card">
            <h2 style="font-size: 18px; margin-bottom: 16px;">📋 冠軍策略最新交易流水明細 (Top 100 Exits)</h2>
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>階段</th>
                        <th>方向</th>
                        <th>進場時間</th>
                        <th>進場價</th>
                        <th>出場時間</th>
                        <th>出場價</th>
                        <th>平倉口數</th>
                        <th>點數損益</th>
                        <th>淨利 (NTD)</th>
                        <th>出場原因</th>
                    </tr>
                </thead>
                <tbody>
    """

    best_trades = results[0]['trades']
    for t in reversed(best_trades[-100:]):
        d_cls = "badge-buy" if t['side'] == "BUY" else "badge-sell"
        p_cls = "val-profit" if t['net_pnl'] > 0 else "val-loss"
        p_sign = "+" if t['net_pnl'] > 0 else ""
        html += f"""
                    <tr>
                        <td>{t['trade_no']}</td>
                        <td><span style="font-size: 11px; color: #94a3b8;">{t['stage']}</span></td>
                        <td><span class="badge" style="background: {'rgba(16,185,129,0.15)' if t['side']=='BUY' else 'rgba(239,68,68,0.15)'}; color: {'#10b981' if t['side']=='BUY' else '#ef4444'};">{t['side']}</span></td>
                        <td>{t['entry_time'][5:16]}</td>
                        <td>{t['entry_price']}</td>
                        <td>{t['exit_time'][5:16]}</td>
                        <td>{t['exit_price']}</td>
                        <td>{t['lots']} 口</td>
                        <td class="{p_cls}">{p_sign}{t['pnl_points']} 點</td>
                        <td class="{p_cls}" style="font-weight: bold;">NT$ {p_sign}{t['net_pnl']:,}</td>
                        <td style="color: #cbd5e1; font-size: 12px;">{t['exit_reason']}</td>
                    </tr>
        """

    html += f"""
                </tbody>
            </table>
        </div>
    </div>

    <script>
        const ctx = document.getElementById('equityChart').getContext('2d');
        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: {json.dumps([t[5:16] for t in chart_times])},
                datasets: {json.dumps(datasets)}
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                interaction: {{ mode: 'index', intersect: false }},
                scales: {{
                    x: {{ grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#94a3b8', maxTicksLimit: 12 }} }},
                    y: {{ grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#94a3b8' }} }}
                }},
                plugins: {{
                    legend: {{ position: 'top', labels: {{ color: '#fff', font: {{ family: 'Outfit', size: 12 }} }} }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[HTML Export] Generated report at: {output_path}")


def main():
    db_path = get_db_path()
    print(f"[Data] Loading historical bars from {db_path}...")
    df_1k, df_5k = load_historical_data(db_path, "TXFR1", start_date="2024-01-01", end_date="2026-09-16")
    print(f"[Data] Loaded {len(df_1k)} 1K bars and {len(df_5k)} 5K bars.")

    base_config = OrderBlockConfig(
        contract_type="MTX",
        point_value=50.0,
        fee_per_side=20.0,
        tax_rate=0.00002,
        max_lots=2,
        min_sl_points=25.0,
        start_capital=1000000.0,
        tp1_fixed_rr=2.0,
        tp2_fixed_rr=3.0
    )

    print("[*] Preparing dataset and 5K Order Blocks (computing once)...")
    prepared_df, raw_obs = OrderBlockTracker.prepare_dataset(df_1k, df_5k, base_config)
    print(f"[*] Prepared {len(prepared_df)} rows and {len(raw_obs)} order blocks.")

    configs = [
        {
            'name': '🏆 【5K OB + 1K FVG 超窄停損狙擊】',
            'desc': '5K OB 區域回踩 + 1K FVG 觸發 + 1K Swing 超窄停損 (15-20pts)',
            'mode': 'sniper_narrow_sl'
        },
        {
            'name': '🛡️ 【5K OB + 1K FVG 穩健防守】',
            'desc': '5K OB 區域回踩 + 1K FVG 觸發 + 5K OB 外圍停損 (25-35pts)',
            'mode': 'sniper_5k_sl'
        },
        {
            'name': '🥇 【純 5K 訂單塊基準】',
            'desc': '純 5K OB 回踩進場 (標準機構訂單塊)',
            'mode': 'pure_5k_ob'
        },
        {
            'name': '⚡ 【純 1K 銀色子彈】',
            'desc': '純 1K FVG + 趨勢突破 (10:00-11:59)',
            'mode': 'pure_1k_sb'
        }
    ]

    all_results = []
    print("\n========================= 純日盤 10:00-11:59 策略回測評比 =========================")
    for c in configs:
        print(f"\n[*] Running: {c['name']} ({c['mode']})...")
        config = OrderBlockConfig(
            contract_type="MTX",
            point_value=50.0,
            fee_per_side=20.0,
            tax_rate=0.00002,
            max_lots=2,
            min_sl_points=15.0 if "narrow" in c['mode'] else 25.0,
            start_capital=1000000.0,
            tp1_fixed_rr=2.0,
            tp2_fixed_rr=3.0
        )
        import copy
        obs_copy = copy.deepcopy(raw_obs)
        engine = SniperOrderBlockEngine(prepared_df.copy(), obs_copy, config=config, mode=c['mode'])
        stats, trades, equity_curve = engine.run_backtest()
        print(f"    Total PnL: NT$ {stats['total_pnl']:+,} | PF: {stats['profit_factor']} | WinRate: {stats['win_rate']}% | MDD: {stats['max_drawdown_pct']}% | Trades: {stats['total_trades']}")
        print(f"    Yearly: 2024: NT$ {stats['yearly_pnl']['2024']:+,} | 2025: NT$ {stats['yearly_pnl']['2025']:+,} | 2026: NT$ {stats['yearly_pnl']['2026']:+,}")

        all_results.append({
            'name': c['name'],
            'desc': c['desc'],
            'mode': c['mode'],
            'stats': stats,
            'trades': trades,
            'equity_curve': equity_curve
        })

    all_results.sort(key=lambda x: x['stats']['total_pnl'], reverse=True)

    report_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../reports/sniper_confluence_day_session.html"))
    generate_html_report(all_results, report_path)


if __name__ == "__main__":
    main()
