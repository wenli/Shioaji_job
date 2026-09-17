# -*- coding: utf-8 -*-
"""
台指期 SMC 機構訂單塊 (Order Block / Mitigation) 回測執行器
功能：
1. 自 SQLite 讀取 1K/5K 歷史數據
2. 執行 5K BOS + FVG 訂單塊偵測與 1K 微觀回踩確認回測
3. 終端格式化印出績效指標
4. 生成現代化 HTML 資金曲線與交易明細報告
"""

import os
import sys
import json
import sqlite3
import argparse
from datetime import datetime
from typing import Tuple, List, Dict, Any, Optional
import pandas as pd
import numpy as np
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
load_dotenv()

from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data


def run_order_block_analysis(
    df_1k: pd.DataFrame,
    df_5k: pd.DataFrame,
    contract_type: str = "MTX",
    swing_window: int = 5,
    min_fvg_pts: float = 4.0,
    min_sl: float = 25.0,
    max_lots: int = 2,
    allowed_hours: Optional[List[int]] = None,
    enable_trend_filter: bool = True
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    print("\n" + "=" * 70)
    print(f"啟動 SMC 機構訂單塊 (Order Block) 策略回測 (標的: {contract_type}, 最大口數: {max_lots}口, 停損保底: {min_sl}點)")
    print("=" * 70)

    cfg = OrderBlockConfig(
        contract_type=contract_type,
        max_lots=max_lots,
        swing_window=swing_window,
        min_fvg_points=min_fvg_pts,
        require_fvg=True,
        min_sl_points=min_sl,
        enable_trend_filter=enable_trend_filter,
        allowed_hours=allowed_hours
    )

    engine = OrderBlockEngine(df_1k, df_5k, cfg)
    print("正在準備數據特徵 (計算 5K 波段高低點、BOS 結構突破、FVG 缺口與 Order Block)...")
    summary, trades, eq_curve = engine.run_backtest()

    print_metrics_table(summary, cfg)
    report_file = generate_html_report(df_1k, summary, trades, eq_curve, cfg)
    return summary, trades, report_file


def print_metrics_table(s: Dict[str, Any], cfg: OrderBlockConfig):
    print("\n" + "=" * 65)
    print(f"{'SMC 機構訂單塊策略 (Order Block) 回測績效指標':^55}")
    print("-" * 65)

    if s['total_trades'] == 0:
        print("  無任何交易產生。")
        print("=" * 65)
        return

    items = [
        ("交易合約標的 (Contract)", f"{cfg.contract_type} ({'大台指' if cfg.contract_type=='TX' else '小台指'})"),
        ("最大進場口數 (Max Lots)", f"{cfg.max_lots} 口"),
        ("起始本金 (Start Capital)", f"NT$ {cfg.start_capital:,.0f}"),
        ("期末總權益 (Ending Capital)", f"NT$ {s['ending_capital']:,.0f}"),
        ("總平倉次數 (Total Exits)", f"{s['total_trades']:,} 次"),
        ("獲利次數 (Wins)", f"{s['winning_trades']:,} 次"),
        ("虧損次數 (Losses)", f"{s['losing_trades']:,} 次"),
        ("保本出場 (BE Exits)", f"{s['be_trades']:,} 次"),
        ("TP1 達成數 (前高/1.5R 鎖利)", f"{s['tp1_count']:,} 筆"),
        ("TP2 達成數 (對側池目標全平)", f"{s['tp2_count']:,} 筆"),
        ("交易勝率 (Win Rate)", f"{s['win_rate']:.2f}%"),
        ("獲利因子 (Profit Factor)", f"{s['profit_factor']:.2f}"),
        ("最大回撤 (Max Drawdown)", f"{s['max_drawdown_pct']:.2f}%"),
        ("淨損益 (Net Profit)", f"NT$ {s['net_profit']:+,.0f}"),
        ("總報酬率 (Total Return)", f"{s['total_return_pct']:+.2f}%"),
        ("平均獲利 (Avg Win)", f"NT$ {s['avg_win']:,.0f}"),
        ("平均虧損 (Avg Loss)", f"NT$ {s['avg_loss']:,.0f}"),
    ]

    for label, val in items:
        print(f"  {label:<30} | {val:<25}")
    print("=" * 65)


def generate_html_report(df_1k: pd.DataFrame, s: Dict[str, Any], trades: List[Dict[str, Any]], eq: List[Dict[str, Any]], cfg: OrderBlockConfig) -> str:
    output_dir = os.path.join(os.path.dirname(__file__), "../../reports")
    os.makedirs(output_dir, exist_ok=True)
    report_file = os.path.join(output_dir, f"order_block_backtest_{cfg.contract_type.lower()}_2026.html")

    def sample_curve(curve):
        if len(curve) <= 1200:
            return curve
        step = max(1, len(curve) // 1200)
        sampled = curve[::step]
        if curve[-1] not in sampled:
            sampled.append(curve[-1])
        return sampled

    s_eq = sample_curve(eq)
    labels = [p['time'][:16] for p in s_eq]
    data = [round(p['equity'], 1) for p in s_eq]

    start_str = str(df_1k['datetime'].iloc[0])[:10]
    end_str = str(df_1k['datetime'].iloc[-1])[:10]

    trades_rows = ""
    for t in trades[-40:]:
        pnl_color = "#10b981" if t['net_pnl'] > 0 else ("#ef4444" if t['net_pnl'] < 0 else "#94a3b8")
        side_badge = f"<span class='badge {'badge-buy' if t['side']=='BUY' else 'badge-sell'}'>{t['side']}</span>"
        stage_badge = f"<span class='stage-tag'>{t['stage']}</span>"
        ind = t.get('indicators', {})
        ob_str = f"OB#{ind.get('ob_id', '-')}: [{ind.get('ob_bottom', 0):.0f}~{ind.get('ob_top', 0):.0f}]"
        fvg_str = f"FVG: {ind.get('fvg_size', 0):.1f}點"

        trades_rows += f"""
        <tr>
            <td>#{t['trade_no']}</td>
            <td>{stage_badge}</td>
            <td>{side_badge}</td>
            <td>{t['entry_time'][:16]}</td>
            <td>{t['entry_price']:.1f}</td>
            <td>{t['exit_time'][:16]}</td>
            <td>{t['exit_price']:.1f}</td>
            <td><span class='reason-tag'>{t['exit_reason']}</span></td>
            <td>{t['lots']}口</td>
            <td>{t['pnl_points']:+.1f}</td>
            <td style="color:{pnl_color}; font-weight:bold; font-family:'JetBrains Mono';">{t['net_pnl']:+,.0f}</td>
            <td>{ob_str} ({fvg_str})</td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SMC 機構訂單塊 (Order Block) 回測報告</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #090d16;
            --card-bg: #111827;
            --border: #1f293d;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --accent: #8b5cf6;
            --green: #10b981;
            --red: #ef4444;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg);
            color: var(--text-main);
            font-family: 'Outfit', sans-serif;
            padding: 2.5rem;
            line-height: 1.5;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            margin-bottom: 2rem;
            border-bottom: 1px solid var(--border);
            padding-bottom: 1.5rem;
        }}
        .header h1 {{ font-size: 2.2rem; font-weight: 700; background: linear-gradient(135deg, #a78bfa, #8b5cf6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .header .meta {{ color: var(--text-muted); font-size: 0.95rem; }}
        .grid-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1.25rem;
            margin-bottom: 2rem;
        }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.5rem;
            position: relative;
            overflow: hidden;
        }}
        .card::before {{
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0; height: 3px;
            background: linear-gradient(90deg, #8b5cf6, transparent);
        }}
        .card .title {{ font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 0.5rem; }}
        .card .value {{ font-size: 1.8rem; font-weight: 700; font-family: 'JetBrains Mono'; }}
        .card .sub {{ font-size: 0.85rem; margin-top: 0.25rem; }}
        .val-profit {{ color: var(--green); }}
        .val-loss {{ color: var(--red); }}
        .chart-box {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 2rem;
            height: 480px;
        }}
        .table-box {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.5rem;
            overflow-x: auto;
        }}
        .table-box h2 {{ font-size: 1.25rem; margin-bottom: 1rem; color: #a78bfa; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; text-align: left; }}
        th {{ color: var(--text-muted); padding: 0.75rem 1rem; border-bottom: 1px solid var(--border); font-weight: 600; }}
        td {{ padding: 0.75rem 1rem; border-bottom: 1px solid rgba(255,255,255,0.04); }}
        .badge {{ padding: 3px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; }}
        .badge-buy {{ background: rgba(16,185,129,0.15); color: var(--green); border: 1px solid rgba(16,185,129,0.3); }}
        .badge-sell {{ background: rgba(239,68,68,0.15); color: var(--red); border: 1px solid rgba(239,68,68,0.3); }}
        .stage-tag {{ background: rgba(139,92,246,0.15); color: #c4b5fd; border: 1px solid rgba(139,92,246,0.3); padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; }}
        .reason-tag {{ color: var(--text-muted); font-size: 0.8rem; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>台指期 SMC 機構訂單塊 (Order Block) 回測報告</h1>
                <div class="meta">標的: {cfg.contract_type} | 區間: {start_str} ~ {end_str} | 回測引擎: SMC OrderBlockEngine</div>
            </div>
            <div style="text-align: right;">
                <span style="background: rgba(139,92,246,0.2); color: #c4b5fd; padding: 6px 14px; border-radius: 20px; font-size: 0.85rem; border: 1px solid rgba(139,92,246,0.4);">
                    5K BOS + FVG Mitigation
                </span>
            </div>
        </div>

        <div class="grid-stats">
            <div class="card">
                <div class="title">淨損益 (Net Profit)</div>
                <div class="value {'val-profit' if s['net_profit']>=0 else 'val-loss'}">NT$ {s['net_profit']:+,.0f}</div>
                <div class="sub" style="color: {'var(--green)' if s['total_return_pct']>=0 else 'var(--red)'};">報酬率: {s['total_return_pct']:+.2f}%</div>
            </div>
            <div class="card">
                <div class="title">獲利因子 (Profit Factor)</div>
                <div class="value" style="color: #c4b5fd;">{s['profit_factor']:.2f}</div>
                <div class="sub" style="color: var(--text-muted);">勝率: {s['win_rate']:.1f}% ({s['winning_trades']}勝/{s['losing_trades']}負)</div>
            </div>
            <div class="card">
                <div class="title">最大回撤 (Max Drawdown)</div>
                <div class="value val-loss">{s['max_drawdown_pct']:.2f}%</div>
                <div class="sub" style="color: var(--text-muted);">保本出場: {s['be_trades']} 筆</div>
            </div>
            <div class="card">
                <div class="title">分批目標達成</div>
                <div class="value" style="color: #60a5fa;">{s['tp1_count']} / {s['tp2_count']}</div>
                <div class="sub" style="color: var(--text-muted);">TP1(前高破位) / TP2(對側池)</div>
            </div>
            <div class="card">
                <div class="title">平均盈虧 (Avg Win / Loss)</div>
                <div class="value" style="font-size: 1.4rem;">{s['avg_win']:,.0f} / {abs(s['avg_loss']):,.0f}</div>
                <div class="sub" style="color: var(--text-muted);">盈虧比: {s['avg_win']/abs(s['avg_loss']) if s['avg_loss']!=0 else 0:.2f}</div>
            </div>
        </div>

        <div class="chart-box">
            <canvas id="equityChart"></canvas>
        </div>

        <div class="table-box">
            <h2>最近交易明細 (最新 40 筆)</h2>
            <table>
                <thead>
                    <tr>
                        <th>序號</th>
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
                        <th>回踩訂單塊 (Mitigated OB)</th>
                    </tr>
                </thead>
                <tbody>
                    {trades_rows}
                </tbody>
            </table>
        </div>
    </div>

    <script>
        const ctx = document.getElementById('equityChart').getContext('2d');
        const labels = {json.dumps(labels)};
        const data = {json.dumps(data)};

        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: labels,
                datasets: [{{
                    label: '權益總額 (NTD)',
                    data: data,
                    borderColor: '#8b5cf6',
                    backgroundColor: 'rgba(139, 92, 246, 0.08)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.1,
                    pointRadius: 0
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        mode: 'index',
                        intersect: false,
                        backgroundColor: '#111827',
                        borderColor: '#374151',
                        borderWidth: 1,
                        titleColor: '#f3f4f6',
                        bodyColor: '#a78bfa',
                        bodyFont: {{ family: 'JetBrains Mono' }}
                    }}
                }},
                scales: {{
                    x: {{
                        grid: {{ color: 'rgba(255,255,255,0.04)' }},
                        ticks: {{ color: '#9ca3af', maxTicksLimit: 12 }}
                    }},
                    y: {{
                        grid: {{ color: 'rgba(255,255,255,0.04)' }},
                        ticks: {{
                            color: '#9ca3af',
                            callback: v => 'NT$ ' + v.toLocaleString()
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n[HTML 報告已生成]: {os.path.abspath(report_file)}")
    return report_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="台指期 SMC 機構訂單塊 (Order Block) 策略回測執行器")
    parser.add_argument("--code", type=str, default="TXFR1")
    parser.add_argument("--contract", type=str, default="MTX", choices=["MTX", "TX"])
    parser.add_argument("--start", type=str, default="2024-01-01")
    parser.add_argument("--end", type=str, default="2026-09-16")
    parser.add_argument("--swing_window", type=int, default=5)
    parser.add_argument("--min_fvg", type=float, default=6.0, help="成立 FVG 的最小缺口點數")
    parser.add_argument("--min_sl", type=float, default=25.0)
    parser.add_argument("--max_lots", type=int, default=2, help="最大持倉口數上限 (預設 2 口，須為偶數)")
    parser.add_argument("--disable_trend_filter", action="store_true", default=False)
    parser.add_argument("--hours", type=str, default="10,11,20", help="允許交易小時 (黃金窗口: 10,11,20)")

    args = parser.parse_args()
    db_path = get_db_path()

    if args.hours.lower() == "all":
        allowed_hours = None
    else:
        allowed_hours = [int(h.strip()) for h in args.hours.split(",") if h.strip().isdigit()]

    df_1k, df_5k = load_historical_data(
        db_path=db_path,
        code=args.code,
        start_date=args.start,
        end_date=args.end
    )

    run_order_block_analysis(
        df_1k,
        df_5k,
        contract_type=args.contract,
        swing_window=args.swing_window,
        min_fvg_pts=args.min_fvg,
        min_sl=args.min_sl,
        max_lots=args.max_lots,
        allowed_hours=allowed_hours,
        enable_trend_filter=not args.disable_trend_filter
    )
