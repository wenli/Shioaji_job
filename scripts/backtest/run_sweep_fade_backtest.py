# -*- coding: utf-8 -*-
"""
台指期 SMC 流動性獵取假突破反轉 (Sweep Fade) 2026 全年回測執行器
功能：
1. 自 SQLite (C:\\Intel\\Database\\Shioaji-future.db) 讀取 2026 全年 1K/5K 歷史數據
2. 執行流動性獵取假突破反轉策略回測
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

from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data


def run_sweep_fade_analysis(
    df_1k: pd.DataFrame,
    df_5k: pd.DataFrame,
    contract_type: str = "MTX",
    min_pen: float = 3.0,
    max_pen: float = 35.0,
    enable_htf_swings: bool = False,
    min_sl: float = 25.0,
    allowed_hours: Optional[List[int]] = None,
    enable_trend_filter: bool = True,
    enable_dynamic_atr: bool = True
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    print("\n" + "=" * 70)
    print(f"啟動 SMC 假突破反轉策略回測 (標的: {contract_type}, 停損保底: {min_sl}點, 5K趨勢濾網: {enable_trend_filter})")
    print("=" * 70)

    cfg = SweepFadeConfig(
        contract_type=contract_type,
        min_penetration=min_pen,
        max_penetration=max_pen,
        enable_pdh_pdl=True,
        enable_orb_pools=True,
        enable_htf_swings=enable_htf_swings,
        enable_trend_filter=enable_trend_filter,
        enable_dynamic_atr=enable_dynamic_atr,
        min_wick_ratio=0.40,
        require_wick_gte_body=True,
        min_sl_points=min_sl,
        allowed_hours=allowed_hours,
        max_attempts_per_session=2
    )

    engine = SweepFadeEngine(df_1k, df_5k, cfg)
    print("正在準備數據特徵 (計算 PDH/PDL、ORB 15m、5K 波段高低點與 VWAP)...")
    summary, trades, eq_curve = engine.run_backtest()

    print_metrics_table(summary, cfg)
    report_file = generate_html_report(df_1k, summary, trades, eq_curve, cfg)
    return summary, trades, report_file


def print_metrics_table(s: Dict[str, Any], cfg: SweepFadeConfig):
    print("\n" + "=" * 65)
    print(f"{'SMC 假突破反轉策略 (Sweep Fade) 回測績效指標':^55}")
    print("-" * 65)

    items = [
        ("總交易記錄 (Total Exits)", f"{s['total_trades']:,} 筆"),
        ("獲利交易 (Wins)", f"{s['winning_trades']:,} 筆"),
        ("虧損交易 (Losses)", f"{s['losing_trades']:,} 筆"),
        ("保本出場 (BE Exits)", f"{s['be_trades']:,} 筆"),
        ("TP1 達成數 (VWAP 鎖半倉)", f"{s['tp1_count']:,} 筆"),
        ("TP2 達成數 (對側池全平)", f"{s['tp2_count']:,} 筆"),
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


def generate_html_report(df_1k: pd.DataFrame, s: Dict[str, Any], trades: List[Dict[str, Any]], eq: List[Dict[str, Any]], cfg: SweepFadeConfig) -> str:
    output_dir = os.path.join(os.path.dirname(__file__), "../../reports")
    os.makedirs(output_dir, exist_ok=True)
    report_file = os.path.join(output_dir, f"sweep_fade_backtest_{cfg.contract_type.lower()}_2026.html")

    # 抽樣資金曲線
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

    # 最近 30 筆交易流水
    trades_rows = ""
    for t in trades[-30:]:
        pnl_color = "#10b981" if t['net_pnl'] > 0 else ("#ef4444" if t['net_pnl'] < 0 else "#94a3b8")
        side_badge = f"<span class='badge {'badge-buy' if t['side']=='BUY' else 'badge-sell'}'>{t['side']}</span>"
        stage_badge = f"<span class='stage-tag'>{t['stage']}</span>"
        ind = t.get('indicators', {})
        pool_str = f"{ind.get('swept_pool', '-')}: {ind.get('pool_price', 0):.0f}"
        pen_str = f"{ind.get('penetration', 0):.1f}點"

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
            <td>{pool_str} ({pen_str})</td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SMC 假突破反轉策略 (Sweep Fade) 2026 回測報告</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #090d16;
            --card-bg: #111827;
            --border: #1f293d;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --accent: #3b82f6;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
        }}
        * {{ margin:0; padding:0; box-sizing:border-box; font-family:'Outfit', -apple-system, sans-serif; }}
        body {{ background: var(--bg); color: var(--text-main); padding: 30px; line-height: 1.6; }}
        .container {{ max-width: 1350px; margin: 0 auto; }}
        header {{ margin-bottom: 30px; border-bottom: 1px solid var(--border); padding-bottom: 20px; display: flex; justify-content: space-between; align-items: flex-end; }}
        h1 {{ font-size: 28px; font-weight: 700; color: #fff; }}
        h1 span {{ color: #10b981; }}
        .sub {{ font-size: 14px; color: var(--text-muted); margin-top: 5px; }}
        
        .grid-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 20px; margin-bottom: 30px; }}
        .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 22px; }}
        .card-title {{ font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
        .card-val {{ font-size: 26px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }}
        .card-sub {{ font-size: 12px; color: var(--text-muted); margin-top: 6px; }}
        
        .chart-box {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 25px; margin-bottom: 30px; }}
        .chart-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }}
        .chart-title {{ font-size: 18px; font-weight: 600; }}
        
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }}
        th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); }}
        th {{ background: #162032; color: var(--text-muted); font-size: 11px; text-transform: uppercase; font-weight: 600; }}
        tr:hover {{ background: rgba(255,255,255,0.02); }}
        
        .badge {{ padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }}
        .badge-buy {{ background: rgba(16, 185, 129, 0.2); color: var(--success); }}
        .badge-sell {{ background: rgba(239, 68, 68, 0.2); color: var(--danger); }}
        .stage-tag {{ background: #1e293b; color: #93c5fd; padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 11px; font-family: 'JetBrains Mono'; }}
        .reason-tag {{ background: #1f293d; padding: 2px 6px; border-radius: 4px; font-size: 11px; color: #cbd5e1; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>🎯 <span>SMC 假突破反轉 (Sweep Fade)</span> 2026 回測成果報告</h1>
                <div class="sub">標的: {cfg.contract_type} ({'小台指 NT$50/點' if cfg.contract_type=='MTX' else '大台指 NT$200/點'}) | 區間: {start_str} 至 {end_str} (9個月完整數據) | 停損保底: {cfg.min_sl_points:.0f} 點 | 時段: {'全時段' if not cfg.allowed_hours else '黃金時段 (' + ','.join(map(str, sorted(cfg.allowed_hours))) + '點)'}</div>
            </div>
            <div style="text-align: right;">
                <div style="font-size: 12px; color: var(--text-muted);">回測產生時間</div>
                <div style="font-family: 'JetBrains Mono'; font-size: 14px;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
            </div>
        </header>

        <div class="grid-cards">
            <div class="card">
                <div class="card-title">淨損益 (Net Profit)</div>
                <div class="card-val" style="color: {'var(--success)' if s['net_profit']>0 else 'var(--danger)'}">NT$ {s['net_profit']:+,.0f}</div>
                <div class="card-sub">總報酬率: <span style="color: {'var(--success)' if s['total_return_pct']>0 else 'var(--danger)'}; font-weight:600;">{s['total_return_pct']:+.2f}%</span></div>
            </div>

            <div class="card">
                <div class="card-title">獲利因子 (Profit Factor)</div>
                <div class="card-val" style="color: {'var(--success)' if s['profit_factor']>=1.3 else 'var(--warning)'}">{s['profit_factor']:.2f}</div>
                <div class="card-sub">勝率: <strong>{s['win_rate']:.1f}%</strong> ({s['winning_trades']}勝 / {s['losing_trades']}負)</div>
            </div>

            <div class="card">
                <div class="card-title">最大回撤 (Max Drawdown)</div>
                <div class="card-val" style="color: var(--warning);">{s['max_drawdown_pct']:.2f}%</div>
                <div class="card-sub">二段階梯停利平滑回撤</div>
            </div>

            <div class="card">
                <div class="card-title">分批停利表現 (Two-Stage TP)</div>
                <div class="card-val" style="color: #60a5fa;">{s['tp1_count']} / {s['tp2_count']}</div>
                <div class="card-sub">TP1(VWAP鎖利): {s['tp1_count']}次 | TP2(對側全平): {s['tp2_count']}次</div>
            </div>
        </div>

        <div class="chart-box">
            <div class="chart-header">
                <div class="chart-title">📈 2026 累計權益曲線 (Equity Curve)</div>
                <div style="font-size:13px; color:var(--text-muted);">起始資金: NT$ {cfg.start_capital:,.0f} ➔ 結算資金: NT$ {s['ending_capital']:,.0f}</div>
            </div>
            <div style="height: 380px;">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <div class="card">
            <div class="card-title" style="margin-bottom: 15px;">📜 最新 30 筆真實分批撮合記錄 (展示 TP1 與 TP2 執行歷程)</div>
            <table>
                <thead>
                    <tr>
                        <th>編號</th>
                        <th>階段</th>
                        <th>方向</th>
                        <th>進場時間</th>
                        <th>進場價</th>
                        <th>出場時間</th>
                        <th>出場價</th>
                        <th>離場原因</th>
                        <th>口數</th>
                        <th>點數</th>
                        <th>淨損益 (NT$)</th>
                        <th>獵取目標池 (刺穿深度)</th>
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
                    label: 'SMC 假突破反轉策略 (Sweep Fade)',
                    data: data,
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.08)',
                    borderWidth: 2.2,
                    fill: true,
                    tension: 0.1,
                    pointRadius: 0
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                interaction: {{ intersect: false, mode: 'index' }},
                plugins: {{
                    legend: {{ labels: {{ color: '#9ca3af', font: {{ family: 'Outfit' }} }} }}
                }},
                scales: {{
                    x: {{
                        grid: {{ color: '#1f293d' }},
                        ticks: {{ color: '#9ca3af', maxTicksLimit: 12, font: {{ family: 'JetBrains Mono' }} }}
                    }},
                    y: {{
                        grid: {{ color: '#1f293d' }},
                        ticks: {{
                            color: '#9ca3af',
                            font: {{ family: 'JetBrains Mono' }},
                            callback: function(value) {{ return 'NT$ ' + value.toLocaleString(); }}
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
    parser = argparse.ArgumentParser(description="台指期 SMC 假突破反轉策略回測執行器")
    parser.add_argument("--code", type=str, default="TXFR1")
    parser.add_argument("--contract", type=str, default="MTX", choices=["MTX", "TX"])
    parser.add_argument("--start", type=str, default="2026-01-01")
    parser.add_argument("--end", type=str, default="2026-09-16")
    parser.add_argument("--min_pen", type=float, default=3.0)
    parser.add_argument("--max_pen", type=float, default=35.0)
    parser.add_argument("--enable_htf_swings", action="store_true", default=False, help="是否開啟 5K 波段點")
    parser.add_argument("--disable_trend_filter", action="store_true", default=False, help="是否停用 5K EMA 趨勢濾網")
    parser.add_argument("--disable_dynamic_atr", action="store_true", default=False, help="是否停用 5K 動態 ATR 刺穿防護")
    parser.add_argument("--min_sl", type=float, default=25.0, help="最低停損點數")
    parser.add_argument("--hours", type=str, default="10,11,15,19,20,21,4", help="允許的交易小時 (逗號分隔，或 'all')")

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

    run_sweep_fade_analysis(
        df_1k,
        df_5k,
        contract_type=args.contract,
        min_pen=args.min_pen,
        max_pen=args.max_pen,
        enable_htf_swings=args.enable_htf_swings,
        min_sl=args.min_sl,
        allowed_hours=allowed_hours,
        enable_trend_filter=not args.disable_trend_filter,
        enable_dynamic_atr=not args.disable_dynamic_atr
    )
