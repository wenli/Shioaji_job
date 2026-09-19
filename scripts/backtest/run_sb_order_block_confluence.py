# -*- coding: utf-8 -*-
"""
台指期銀色子彈 (Silver Bullet Killzone) + SMC 訂單塊 (Order Block) 雙重共振回測執行器
- 口數限制: 嚴格上限 2 口 (Max Lots = 2)
- 階梯停利: TP1 2.0R (平1口移保本) / TP2 3.0R (剩餘1口全平)
- 停損保底: 25.0 點
- 評估週期: 2024-01-01 至 2026-09-16 全歷史 (MTX 小台指)
- 輸出: 終端指標矩陣 + 獨立互動式 HTML 視覺化回測報告
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
from app.strategy.silver_bullet import USDaylightSavingDetector
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data


class ConfluenceOrderBlockEngine(OrderBlockEngine):
    """
    擴展 OrderBlockEngine，支援銀色子彈精確 Killzone 分鐘級時段過濾
    """
    def __init__(self, df_1k: pd.DataFrame, df_5k: pd.DataFrame, config: OrderBlockConfig, kz_mode: str = "standard_sb"):
        super().__init__(df_1k, df_5k, config)
        self.kz_mode = kz_mode

    def is_in_silver_bullet_kz(self, dt: pd.Timestamp) -> Tuple[bool, str]:
        t = dt.time()
        is_dst = USDaylightSavingDetector.is_daylight_saving(dt.to_pydatetime())

        if self.kz_mode == "standard_sb":
            # 日盤: 09:00 - 10:00
            if time(9, 0) <= t < time(10, 0):
                return True, "SB_DAY_0900_1000"
            # 夜盤: 夏令 21:30 - 22:30 / 冬令 22:30 - 23:30
            if is_dst and (time(21, 30) <= t < time(22, 30)):
                return True, "SB_NIGHT_EDT_2130_2230"
            elif (not is_dst) and (time(22, 30) <= t < time(23, 30)):
                return True, "SB_NIGHT_EST_2230_2330"
            return False, ""

        elif self.kz_mode == "extended_sb":
            # 擴展銀色子彈時段: 包含 09:00-11:00 (現貨主力發動+趨勢確立) + 夜盤 21:30-23:00
            if time(9, 0) <= t < time(11, 0):
                return True, "SB_EXT_DAY_0900_1100"
            if is_dst and (time(21, 30) <= t < time(23, 0)):
                return True, "SB_EXT_NIGHT_EDT"
            elif (not is_dst) and (time(22, 0) <= t < time(23, 30)):
                return True, "SB_EXT_NIGHT_EST"
            return False, ""

        elif self.kz_mode == "ob_golden":
            # 訂單塊黃金窗口: 10:00-11:59, 20:00-20:59
            if time(10, 0) <= t < time(12, 0):
                return True, "OB_GOLDEN_DAY_1011"
            if time(20, 0) <= t < time(21, 0):
                return True, "OB_GOLDEN_EVE_20"
            return False, ""

        elif self.kz_mode == "all_day_eve":
            return True, "ALL"

        return False, ""

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
        sessions = df['session'].values
        session_groups = df['session_group'].values
        htf_ema_f = df['htf_ema_fast'].values
        htf_ema_s = df['htf_ema_slow'].values

        capital = float(self.config.start_capital)
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

        trades = []
        equity_curve = [{'time': str(times[0]), 'equity': capital}]

        active_obs = list(self.order_blocks)
        active_obs.sort(key=lambda x: x.formed_time)
        ob_cursor = 0

        current_active_obs = []
        session_attempts = {}

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

            # A. 持倉處理 (TP1 平1口移保本 / TP2 剩餘1口全平)
            if position != 0:
                triggered_exit = False
                exit_price = 0.0
                exit_reason = ""
                exit_lots = 0

                if curr_sess != prev_sess:
                    triggered_exit = True
                    exit_price = closes[i - 1]
                    exit_reason = "EOD (收盤強平)"
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
                                    'strategy': 'sb_order_block',
                                    'stage': 'TP1',
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
                                    'strategy': 'sb_order_block',
                                    'stage': 'TP1',
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
                                exit_reason = "TP2 (3.0R 目標全平)"
                                exit_lots = lots_remaining
                            elif h_cur >= stop_loss:
                                triggered_exit = True
                                exit_price = stop_loss
                                exit_reason = "BE (保本出場)" if abs(stop_loss - entry_price) < 0.1 else "SL (止損)"
                                exit_lots = lots_remaining

                if triggered_exit:
                    mult = 1 if position == 1 else -1
                    pnl_pts = (exit_price - entry_price) * mult
                    gross_pnl = pnl_pts * exit_lots * self.config.point_value
                    costs = self.calculate_costs(entry_price, exit_price, exit_lots)
                    net_pnl = gross_pnl - costs
                    capital += net_pnl

                    trades.append({
                        'trade_no': len(trades) + 1,
                        'strategy': 'sb_order_block',
                        'stage': 'TP2' if is_tp1_filled else 'FULL',
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

            # B. 尋找銀色子彈 Killzone 時段內的 Order Block 回踩訊號
            if position == 0:
                in_kz, kz_tag = self.is_in_silver_bullet_kz(t_cur)
                if not in_kz:
                    continue

                bar_range = max(1.0, h_cur - l_cur)
                body = abs(c_cur - o_cur)
                upper_wick = h_cur - max(o_cur, c_cur)
                lower_wick = min(o_cur, c_cur) - l_cur

                is_htf_bullish = bool(not np.isnan(htf_ema_f[i]) and not np.isnan(htf_ema_s[i]) and htf_ema_f[i] >= htf_ema_s[i])
                is_htf_bearish = bool(not np.isnan(htf_ema_f[i]) and not np.isnan(htf_ema_s[i]) and htf_ema_f[i] <= htf_ema_s[i])

                # 1. 看多訂單塊回踩
                allow_long = (not self.config.enable_trend_filter) or is_htf_bullish
                if allow_long and session_attempts[s_grp]['long'] < self.config.max_attempts_per_session:
                    for ob in current_active_obs:
                        if ob.ob_type != "BULLISH" or ob.trade_count >= self.config.max_attempts_per_ob:
                            continue

                        if l_cur <= ob.top and h_cur >= ob.bottom:
                            confirmed = True
                            if self.config.require_confirmation:
                                wick_ratio = lower_wick / bar_range
                                confirmed = (wick_ratio >= self.config.min_wick_ratio) or (c_cur >= ob.top)

                            if confirmed:
                                position = 1
                                entry_price = c_cur
                                entry_time = t_cur

                                sl_raw = ob.bottom - self.config.sl_buffer_points
                                risk_pts = max(self.config.min_sl_points, entry_price - sl_raw)
                                stop_loss = entry_price - risk_pts
                                risk_points = risk_pts

                                tp1_price = entry_price + risk_pts * self.config.tp1_fixed_rr
                                tp2_price = entry_price + risk_pts * self.config.tp2_fixed_rr

                                # 嚴格 2 口限制
                                calc_lots = 2

                                lots_total = calc_lots
                                lots_remaining = calc_lots
                                is_tp1_filled = False
                                session_attempts[s_grp]['long'] += 1
                                ob.trade_count += 1
                                ob.status = "mitigated"

                                trade_indicators = {
                                    'kz_tag': kz_tag,
                                    'ob_id': ob.ob_id,
                                    'ob_type': ob.ob_type,
                                    'fvg_size': ob.fvg_size,
                                    'vwap': round(vwaps[i], 1) if not np.isnan(vwaps[i]) else 0.0
                                }
                                break

                # 2. 看空訂單塊回踩
                allow_short = (not self.config.enable_trend_filter) or is_htf_bearish
                if position == 0 and allow_short and session_attempts[s_grp]['short'] < self.config.max_attempts_per_session:
                    for ob in current_active_obs:
                        if ob.ob_type != "BEARISH" or ob.trade_count >= self.config.max_attempts_per_ob:
                            continue

                        if h_cur >= ob.bottom and l_cur <= ob.top:
                            confirmed = True
                            if self.config.require_confirmation:
                                wick_ratio = upper_wick / bar_range
                                confirmed = (wick_ratio >= self.config.min_wick_ratio) or (c_cur <= ob.bottom)

                            if confirmed:
                                position = -1
                                entry_price = c_cur
                                entry_time = t_cur

                                sl_raw = ob.top + self.config.sl_buffer_points
                                risk_pts = max(self.config.min_sl_points, sl_raw - entry_price)
                                stop_loss = entry_price + risk_pts
                                risk_points = risk_pts

                                tp1_price = entry_price - risk_pts * self.config.tp1_fixed_rr
                                tp2_price = entry_price - risk_pts * self.config.tp2_fixed_rr

                                # 嚴格 2 口限制
                                calc_lots = 2

                                lots_total = calc_lots
                                lots_remaining = calc_lots
                                is_tp1_filled = False
                                session_attempts[s_grp]['short'] += 1
                                ob.trade_count += 1
                                ob.status = "mitigated"

                                trade_indicators = {
                                    'kz_tag': kz_tag,
                                    'ob_id': ob.ob_id,
                                    'ob_type': ob.ob_type,
                                    'fvg_size': ob.fvg_size,
                                    'vwap': round(vwaps[i], 1) if not np.isnan(vwaps[i]) else 0.0
                                }
                                break

        # 績效統計
        total_trades = len(trades)
        if total_trades > 0:
            winning_trades = [t for t in trades if t['net_pnl'] > 0]
            losing_trades = [t for t in trades if t['net_pnl'] < 0]
            be_trades = [t for t in trades if "BE" in t['exit_reason']]
            tp1_trades = [t for t in trades if t['stage'] == 'TP1']
            tp2_trades = [t for t in trades if t['stage'] == 'TP2' and t['net_pnl'] > 0]

            win_rate = len(winning_trades) / total_trades
            gross_profit = sum(t['net_pnl'] for t in winning_trades)
            gross_loss = abs(sum(t['net_pnl'] for t in losing_trades))
            profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.9

            eq_series = pd.Series([e['equity'] for e in equity_curve])
            cum_max = eq_series.cummax()
            dd = (cum_max - eq_series) / cum_max
            mdd_pct = float(dd.max() * 100.0)

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
                'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0, 'be_trades': 0,
                'tp1_count': 0, 'tp2_count': 0, 'win_rate': 0.0, 'profit_factor': 0.0,
                'max_drawdown_pct': 0.0, 'total_return_pct': 0.0, 'net_profit': 0.0,
                'ending_capital': self.config.start_capital, 'avg_win': 0.0, 'avg_loss': 0.0
            }

        return summary, trades, equity_curve


def generate_html_report(df_1k, experiments_results: List[Dict[str, Any]], output_file: str):
    """生成現代化玻璃擬物風格 HTML 對比回測報告"""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    # 準備資金曲線 JSON
    datasets = []
    colors = ["#10b981", "#3b82f6", "#f59e0b", "#ec4899"]
    for idx, exp in enumerate(experiments_results):
        eq = exp['equity_curve']
        step = max(1, len(eq) // 800)
        sampled = eq[::step]
        if eq[-1] not in sampled:
            sampled.append(eq[-1])
        datasets.append({
            'label': exp['name'],
            'data': [{'x': p['time'][:16], 'y': round(p['equity'], 0)} for p in sampled],
            'borderColor': colors[idx % len(colors)],
            'borderWidth': 2,
            'fill': False,
            'tension': 0.1,
            'pointRadius': 0
        })

    primary_trades = experiments_results[0]['trades'][:100]  # 最新 100 筆交易
    
    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SMC 銀色子彈 + 訂單塊雙重共振回測報告 (2口限制、2.0R/3.0R)</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-base: #080c14;
            --bg-card: rgba(14, 20, 32, 0.85);
            --border: rgba(255, 255, 255, 0.08);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #8b5cf6;
            --success: #10b981;
            --danger: #ef4444;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Outfit', sans-serif; }}
        body {{ background: var(--bg-base); color: var(--text-main); padding: 32px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        header {{ margin-bottom: 28px; border-bottom: 1px solid var(--border); padding-bottom: 20px; }}
        h1 {{ font-size: 26px; font-weight: 700; color: #fff; }}
        h1 span {{ color: #a78bfa; }}
        p.sub {{ color: var(--text-muted); font-size: 14px; margin-top: 6px; }}
        .card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 14px; padding: 24px; margin-bottom: 24px; }}
        .grid-stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .stat-item {{ background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }}
        .stat-label {{ font-size: 12px; color: var(--text-muted); text-transform: uppercase; font-weight: 600; }}
        .stat-val {{ font-size: 24px; font-weight: 700; font-family: 'JetBrains Mono', monospace; margin-top: 6px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; margin-top: 12px; }}
        th {{ color: var(--text-muted); padding: 12px 14px; border-bottom: 1px solid var(--border); }}
        td {{ padding: 12px 14px; border-bottom: 1px solid rgba(255,255,255,0.04); font-family: 'JetBrains Mono', monospace; }}
        .val-profit {{ color: var(--success); }}
        .val-loss {{ color: var(--danger); }}
        .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
        .badge-buy {{ background: rgba(16,185,129,0.15); color: var(--success); }}
        .badge-sell {{ background: rgba(239,68,68,0.15); color: var(--danger); }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🏛️ SMC 銀色子彈 + 訂單塊 <span>雙重共振回測報告</span></h1>
            <p class="sub">合約標的: 小台指 (MTX) | 評估區間: 2024-01-01 ~ 2026-09-16 | 風控: 嚴格上限 2 口、2.0R/3.0R 階梯停利</p>
        </header>

        <div class="card">
            <h2 style="font-size: 18px; margin-bottom: 16px;">📊 策略對比與綜合評比矩陣</h2>
            <table>
                <thead>
                    <tr>
                        <th>策略配置名稱</th>
                        <th>交易時段 (Window)</th>
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
    for exp in experiments_results:
        s = exp['summary']
        pnl_class = "val-profit" if s['net_profit'] > 0 else "val-loss"
        html += f"""
                    <tr>
                        <td style="font-weight: 700; color: #fff;">{exp['name']}</td>
                        <td style="color: var(--text-muted);">{exp['window_desc']}</td>
                        <td>{s['total_trades']} 筆</td>
                        <td>{s['win_rate']:.1f}%</td>
                        <td style="color: #a78bfa; font-weight: bold;">{s['profit_factor']:.2f}</td>
                        <td class="val-loss">{s['max_drawdown_pct']:.1f}%</td>
                        <td class="{pnl_class}" style="font-weight: bold;">NT$ {s['net_profit']:+,.0f}</td>
                        <td class="{'val-profit' if exp['pnl_24']>0 else 'val-loss'}">NT$ {exp['pnl_24']:+,.0f}</td>
                        <td class="{'val-profit' if exp['pnl_25']>0 else 'val-loss'}">NT$ {exp['pnl_25']:+,.0f}</td>
                        <td class="{'val-profit' if exp['pnl_26']>0 else 'val-loss'}">NT$ {exp['pnl_26']:+,.0f}</td>
                    </tr>
        """

    html += f"""
                </tbody>
            </table>
        </div>

        <div class="card">
            <h2 style="font-size: 18px; margin-bottom: 16px;">📈 全歷史資金累積曲線 (Equity Curves)</h2>
            <div style="height: 420px;">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <div class="card">
            <h2 style="font-size: 18px; margin-bottom: 16px;">📋 首選策略最新交易流水明細 (Top 100 Exits)</h2>
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
                        <th>出場原因</th>
                        <th>口數</th>
                        <th>點數</th>
                        <th>淨損益 (NTD)</th>
                    </tr>
                </thead>
                <tbody>
"""
    for t in primary_trades:
        badge_cls = "badge-buy" if t['side'] == 'BUY' else "badge-sell"
        pnl_cls = "val-profit" if t['net_pnl'] > 0 else "val-loss"
        html += f"""
                    <tr>
                        <td>{t['trade_no']}</td>
                        <td><span class="badge" style="background: rgba(139,92,246,0.2); color:#c4b5fd;">{t['stage']}</span></td>
                        <td><span class="badge {badge_cls}">{t['side']}</span></td>
                        <td>{t['entry_time']}</td>
                        <td>{t['entry_price']:.0f}</td>
                        <td>{t['exit_time']}</td>
                        <td>{t['exit_price']:.0f}</td>
                        <td>{t['exit_reason']}</td>
                        <td>{t['lots']} 口</td>
                        <td>{t['pnl_points']:+.1f}</td>
                        <td class="{pnl_cls}">NT$ {t['net_pnl']:+,.0f}</td>
                    </tr>
        """

    html += f"""
                </tbody>
            </table>
        </div>
    </div>

    <script>
        const ctx = document.getElementById('equityChart').getContext('2d');
        const chartData = {json.dumps(datasets)};
        new Chart(ctx, {{
            type: 'line',
            data: {{ datasets: chartData }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{
                        type: 'category',
                        grid: {{ color: 'rgba(255,255,255,0.04)' }},
                        ticks: {{ color: '#94a3b8', maxTicksLimit: 12 }}
                    }},
                    y: {{
                        grid: {{ color: 'rgba(255,255,255,0.04)' }},
                        ticks: {{
                            color: '#94a3b8',
                            callback: v => 'NT$ ' + v.toLocaleString()
                        }}
                    }}
                }},
                plugins: {{
                    legend: {{ labels: {{ color: '#f8fafc' }} }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n[HTML 報告已生成]: {os.path.abspath(output_file)}")


def main():
    db_path = get_db_path()
    print("=" * 80)
    print(f"啟動「銀色子彈 Killzone + SMC 訂單塊」雙重共振回測 (2口限制、2.0R/3.0R 階梯停利)")
    print("=" * 80)

    df_1k, df_5k = load_historical_data(
        db_path=db_path,
        code="TXFR1",
        start_date="2024-01-01",
        end_date="2026-09-16"
    )

    # 共同參數：小台指、上限2口、保底停損25點、TP1 2.0R、TP2 3.0R、FVG 6.0點
    base_cfg = OrderBlockConfig(
        contract_type="MTX",
        max_lots=2,
        swing_window=5,
        min_fvg_points=6.0,
        max_ob_age_bars=40,
        min_wick_ratio=0.35,
        min_sl_points=25.0,
        tp1_mode="fixed_rr",
        tp1_fixed_rr=2.0,
        tp2_mode="fixed_rr",
        tp2_fixed_rr=3.0,
        enable_trend_filter=True,
        allowed_hours=None  # 由 Confluence 引擎精準控制分鐘級別 Killzone
    )

    # 預建特徵矩陣
    print("正在建構 1K/5K 特徵特徵矩陣 (VWAP, PDH/PDL, HTF EMA)...")
    prepared_df, obs = OrderBlockTracker.prepare_dataset(df_1k, df_5k, base_cfg)

    # 定義對比實驗
    experiments = [
        ("【標準共振】銀色子彈 Killzone + SMC 訂單塊", "standard_sb", "日盤 09:00-10:00 + 美盤 (夏 21:30 / 冬 22:30)"),
        ("【擴充共振】銀色子彈擴展 + SMC 訂單塊", "extended_sb", "日盤 09:00-11:00 + 美盤 21:30-23:00"),
        ("【基準對照】訂單塊黃金窗口 (純 OB 最佳化)", "ob_golden", "日盤 10:00-11:59 + 歐盤 20:00-20:59"),
        ("【全時段對照】SMC 訂單塊全時段 (無時段過濾)", "all_day_eve", "日盤 + 夜盤 全時段無休"),
    ]

    results = []

    print("\n" + "=" * 80)
    print(f"{'策略配置':<30} | {'總淨利 (NTD)':<14} | {'PF':<6} | {'MDD':<6} | {'勝率':<6} | {'交易數':<6}")
    print("-" * 80)

    for name, kz_mode, window_desc in experiments:
        engine = ConfluenceOrderBlockEngine(df_1k, df_5k, base_cfg, kz_mode=kz_mode)
        engine.df = prepared_df.copy()
        engine.order_blocks = [OrderBlockTracker.detect_5k_order_blocks(df_5k, base_cfg)[1]][0]

        summary, trades, eq_curve = engine.run_backtest()

        df_tr = pd.DataFrame(trades)
        p24 = p25 = p26 = 0
        if not df_tr.empty:
            df_tr['entry_time'] = pd.to_datetime(df_tr['entry_time'])
            df_tr['year'] = df_tr['entry_time'].dt.year
            p24 = df_tr[df_tr['year'] == 2024]['net_pnl'].sum() if 2024 in df_tr['year'].values else 0
            p25 = df_tr[df_tr['year'] == 2025]['net_pnl'].sum() if 2025 in df_tr['year'].values else 0
            p26 = df_tr[df_tr['year'] == 2026]['net_pnl'].sum() if 2026 in df_tr['year'].values else 0

        print(f"{name:<28} | NT$ {summary['net_profit']:>+9,.0f} | {summary['profit_factor']:>4.2f} | {summary['max_drawdown_pct']:>4.1f}% | {summary['win_rate']:>4.1f}% | {summary['total_trades']:>4} 筆")

        results.append({
            'name': name,
            'kz_mode': kz_mode,
            'window_desc': window_desc,
            'summary': summary,
            'trades': trades,
            'equity_curve': eq_curve,
            'pnl_24': p24,
            'pnl_25': p25,
            'pnl_26': p26
        })

    print("=" * 80)

    output_html = os.path.join(os.path.dirname(__file__), "../../reports/sb_order_block_confluence_2lots.html")
    generate_html_report(df_1k, results, output_html)


if __name__ == "__main__":
    main()
