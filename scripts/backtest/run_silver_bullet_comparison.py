# -*- coding: utf-8 -*-
"""
台指期 SMC 銀色子彈 (Silver Bullet) 新舊版本全維度對比回測腳本
功能：
1. 自動載入 SQLite (如 C:\\Intel\\Database\\Shioaji-future.db 或 Shioaji.db) 的真實 1K/5K 歷史數據
2. 平行運行「優化前基準 (Baseline)」vs「優化後新版 (Optimized)」
3. 終端輸出彩色對比統計表格
4. 生成精美 HTML 視覺化回測報告 (含資金曲線走勢圖與關鍵指標對照)
"""

import os
import sys
import json
import sqlite3
import argparse
from datetime import datetime
from pathlib import Path
from typing import Tuple, List, Dict, Any, Optional
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# 加入根目錄
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
load_dotenv()

from app.strategy.silver_bullet import SilverBulletConfig, SilverBulletEngine


def get_db_path() -> str:
    """自動尋找期貨資料庫路徑"""
    env_db = os.getenv("DB_NAME")
    candidates = [
        env_db,
        r"C:\Intel\Database\Shioaji-future.db",
        "Shioaji.db",
        r"C:\Intel\TW_Stock_K-Line_Chart\SK.db"
    ]
    for c in candidates:
        if c and os.path.exists(c) and os.path.getsize(c) > 1024:
            return c
    raise FileNotFoundError("找不到可用的期貨 SQLite 資料庫，請檢查 .env 的 DB_NAME 設定。")


def load_historical_data(db_path: str, code: str = "TXFR1", start_date: str = "", end_date: str = "", limit: int = 0) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """從 SQLite 讀取 1K 與 5K 數據"""
    print(f"正在從資料庫讀取數據: {db_path} (合約代碼: {code})...")
    conn = sqlite3.connect(db_path)

    date_filter = ""
    if start_date:
        date_filter += f" AND ts >= '{start_date} 00:00:00'"
    if end_date:
        date_filter += f" AND ts <= '{end_date} 23:59:59'"

    limit_clause = f" LIMIT {limit}" if limit > 0 else ""

    q_1k = f"SELECT ts as datetime, open, high, low, close, volume FROM futures1k WHERE code='{code}'{date_filter} ORDER BY ts{limit_clause};"
    q_5k = f"SELECT ts as datetime, open, high, low, close, volume FROM futures5k WHERE code='{code}'{date_filter} ORDER BY ts{limit_clause};"

    df_1k = pd.read_sql_query(q_1k, conn)
    df_5k = pd.read_sql_query(q_5k, conn)
    conn.close()

    if df_1k.empty or df_5k.empty:
        raise ValueError(f"查詢結果為空！code={code}, start={start_date}, end={end_date}")

    df_1k['datetime'] = pd.to_datetime(df_1k['datetime'])
    df_5k['datetime'] = pd.to_datetime(df_5k['datetime'])

    print(f"讀取成功: 1K 數據 {len(df_1k):,} 根 | 5K 數據 {len(df_5k):,} 根")
    print(f"區間: {df_1k['datetime'].iloc[0]} ~ {df_1k['datetime'].iloc[-1]}")
    return df_1k, df_5k


def run_comparison(df_1k: pd.DataFrame, df_5k: pd.DataFrame, contract_type: str = "MTX") -> Tuple[Dict, Dict, str]:
    """執行優化前與優化後的對比回測"""
    print("\n" + "=" * 70)
    print(f"開始執行 SMC 銀色子彈對比回測 (標的規格: {contract_type})")
    print("=" * 70)

    # 1. 優化前基準 (Baseline)
    print("\n[1/2] 正在回測「優化前基準 (Baseline)」...")
    baseline_cfg = SilverBulletConfig(
        contract_type=contract_type,
        enable_vwap=False,               # 無 VWAP 均價過濾
        enable_volume_spike=False,       # 無成交量突波過濾
        entry_mode="market_close",       # 突破收盤市價立即進場 (無回踩確認)
        sl_mode="bar_extreme",           # 突破 K 棒極值防守
        enable_breakeven=False,          # 無 1R 動態移保本
        enable_dst_detection=False       # 固定時間時段
    )
    engine_base = SilverBulletEngine(df_1k, df_5k, baseline_cfg)
    summary_base, trades_base, eq_base = engine_base.run_backtest()

    # 2. 優化後新版 (Optimized)
    print("[2/2] 正在回測「優化後新版 (Optimized SMC + 50% CE + 保本)」...")
    opt_cfg = SilverBulletConfig(
        contract_type=contract_type,
        enable_vwap=True,                # 啟用當日 VWAP 籌碼方向過濾
        enable_volume_spike=True,        # 啟用 1.3x 量能突波過濾
        vol_spike_ratio=1.3,
        entry_mode="limit_retest",       # 50% CE 限價掛單回測
        fvg_entry_level="ce_50",
        max_wait_bars=5,
        sl_mode="swing_extreme",         # 結構極值防守
        sl_buffer_points=3.0,
        enable_breakeven=True,           # 1R 動態移保本
        be_trigger_r=1.0,
        tp_mode="opposite_swing",
        rr_ratio=2.0,
        enable_dst_detection=True        # 美股夏冬令自適應
    )
    engine_opt = SilverBulletEngine(df_1k, df_5k, opt_cfg)
    summary_opt, trades_opt, eq_opt = engine_opt.run_backtest()

    # 3. 輸出終端對比表格
    print_comparison_table(summary_base, summary_opt)

    # 4. 產出 HTML 視覺化報告
    report_path = generate_html_report(df_1k, summary_base, summary_opt, trades_base, trades_opt, eq_base, eq_opt, contract_type)
    return summary_base, summary_opt, report_path


def print_comparison_table(sb: Dict, so: Dict):
    """終端格式化輸出對比表格"""
    print("\n" + "=" * 75)
    print(f"{'指標項目 (Performance Metric)':<26} | {'優化前 (Baseline)':<20} | {'優化後 (Optimized)':<20}")
    print("-" * 75)

    def diff_str(val_b, val_o, is_pct=False, is_cost=False):
        d = val_o - val_b
        sign = "+" if d > 0 else ""
        unit = "%" if is_pct else ""
        if is_cost:
            # 成本或回撤越低越好
            color = "\033[92m" if d < 0 else "\033[91m"
        else:
            color = "\033[92m" if d > 0 else "\033[91m"
        reset = "\033[0m"
        return f"{color}({sign}{d:.2f}{unit}){reset}"

    items = [
        ("總交易次數 (Total Trades)", f"{sb['total_trades']:,} 次", f"{so['total_trades']:,} 次"),
        ("獲利交易 (Wins)", f"{sb['winning_trades']:,} 次", f"{so['winning_trades']:,} 次"),
        ("虧損交易 (Losses)", f"{sb['losing_trades']:,} 次", f"{so['losing_trades']:,} 次"),
        ("保本出場 (Breakeven Exits)", f"{sb['be_trades']:,} 次", f"{so['be_trades']:,} 次"),
        ("過濾攔截信號數 (Filtered)", f"{sb['filtered_count']:,} 次", f"{so['filtered_count']:,} 次"),
        ("交易勝率 (Win Rate)", f"{sb['win_rate']:.2f}%", f"{so['win_rate']:.2f}%"),
        ("獲利因子 (Profit Factor)", f"{sb['profit_factor']:.2f}", f"{so['profit_factor']:.2f}"),
        ("最大回撤 (Max Drawdown)", f"{sb['max_drawdown_pct']:.2f}%", f"{so['max_drawdown_pct']:.2f}%"),
        ("淨損益 (Net Profit)", f"NT$ {sb['net_profit']:,.0f}", f"NT$ {so['net_profit']:,.0f}"),
        ("總報酬率 (Total Return)", f"{sb['total_return_pct']:.2f}%", f"{so['total_return_pct']:.2f}%"),
        ("平均獲利 (Avg Win)", f"NT$ {sb['avg_win']:,.0f}", f"NT$ {so['avg_win']:,.0f}"),
        ("平均虧損 (Avg Loss)", f"NT$ {sb['avg_loss']:,.0f}", f"NT$ {so['avg_loss']:,.0f}"),
    ]

    for label, v_b, v_o in items:
        print(f"{label:<26} | {v_b:<20} | {v_o:<20}")
    print("=" * 75)


def generate_html_report(df_1k: pd.DataFrame, sb: Dict, so: Dict, tb: List, to: List, eq_b: List, eq_o: List, contract: str) -> str:
    """生成包含資金曲線對比的現代化 HTML 報告"""
    output_dir = os.path.join(os.path.dirname(__file__), "../../reports")
    os.makedirs(output_dir, exist_ok=True)
    report_file = os.path.join(output_dir, f"silver_bullet_comparison_{contract.lower()}.html")

    # 抽樣資金曲線以保證圖表輕量順暢 (每 50 點抽樣 1 點或全部保留交易點)
    def sample_curve(curve):
        if len(curve) <= 1000:
            return curve
        step = max(1, len(curve) // 1000)
        sampled = curve[::step]
        if curve[-1] not in sampled:
            sampled.append(curve[-1])
        return sampled

    s_eq_b = sample_curve(eq_b)
    s_eq_o = sample_curve(eq_o)

    b_labels = [p['time'][:16] for p in s_eq_b]
    b_data = [round(p['equity'], 1) for p in s_eq_b]

    o_labels = [p['time'][:16] for p in s_eq_o]
    o_data = [round(p['equity'], 1) for p in s_eq_o]

    start_str = str(df_1k['datetime'].iloc[0])[:10]
    end_str = str(df_1k['datetime'].iloc[-1])[:10]

    # 最近 20 筆優化後明細
    recent_trades_html = ""
    for t in to[-20:]:
        pnl_color = "#10b981" if t['net_pnl'] > 0 else ("#ef4444" if t['net_pnl'] < 0 else "#94a3b8")
        side_badge = f"<span class='badge {'badge-buy' if t['side']=='BUY' else 'badge-sell'}'>{t['side']}</span>"
        reason_tag = f"<span class='tag'>{t['exit_reason']}</span>"
        recent_trades_html += f"""
        <tr>
            <td>#{t['trade_no']}</td>
            <td>{side_badge}</td>
            <td>{t['entry_time'][:16]}</td>
            <td>{t['entry_price']:.1f}</td>
            <td>{t['exit_time'][:16]}</td>
            <td>{t['exit_price']:.1f}</td>
            <td>{reason_tag}</td>
            <td style="color:{pnl_color}; font-weight:bold;">{t['net_pnl']:+,.0f}</td>
            <td>{t['indicators'].get('killzone', '-')}</td>
            <td>{t['indicators'].get('vol_ratio', 1.0):.2f}x</td>
        </tr>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SMC 台指銀色子彈 (Silver Bullet) 最佳化回測對比報告</title>
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
        .container {{ max-width: 1300px; margin: 0 auto; }}
        header {{ margin-bottom: 30px; border-bottom: 1px solid var(--border); padding-bottom: 20px; display: flex; justify-content: space-between; align-items: flex-end; }}
        h1 {{ font-size: 28px; font-weight: 700; color: #fff; }}
        h1 span {{ color: var(--accent); }}
        .sub {{ font-size: 14px; color: var(--text-muted); margin-top: 5px; }}
        
        .grid-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-bottom: 30px; }}
        .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 22px; }}
        .card-title {{ font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; }}
        .card-row {{ display: flex; justify-content: space-between; align-items: baseline; margin-top: 6px; }}
        .card-val {{ font-size: 24px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }}
        .badge-diff {{ font-size: 12px; padding: 2px 8px; border-radius: 999px; font-weight: 600; font-family: 'JetBrains Mono'; }}
        .diff-pos {{ background: rgba(16, 185, 129, 0.15); color: var(--success); }}
        .diff-neg {{ background: rgba(239, 68, 68, 0.15); color: var(--danger); }}
        
        .chart-box {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 25px; margin-bottom: 30px; }}
        .chart-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }}
        .chart-title {{ font-size: 18px; font-weight: 600; }}
        
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
        th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid var(--border); }}
        th {{ background: #162032; color: var(--text-muted); font-size: 12px; text-transform: uppercase; font-weight: 600; }}
        tr:hover {{ background: rgba(255,255,255,0.02); }}
        
        .badge {{ padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }}
        .badge-buy {{ background: rgba(16, 185, 129, 0.2); color: var(--success); }}
        .badge-sell {{ background: rgba(239, 68, 68, 0.2); color: var(--danger); }}
        .tag {{ background: #1f293d; padding: 2px 6px; border-radius: 4px; font-size: 11px; color: #93c5fd; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>⚡ <span>SMC 銀色子彈 (Silver Bullet)</span> 全維度最佳化回測報告</h1>
                <div class="sub">標的: {contract} | 回測區間: {start_str} 至 {end_str} | 資料頻率: 1K (LTF) / 5K (HTF)</div>
            </div>
            <div style="text-align: right;">
                <div style="font-size: 12px; color: var(--text-muted);">回測產生時間</div>
                <div style="font-family: 'JetBrains Mono'; font-size: 14px;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
            </div>
        </header>

        <div class="grid-cards">
            <div class="card">
                <div class="card-title">淨損益 (Net Profit)</div>
                <div class="card-row">
                    <div>
                        <span style="font-size:12px; color:var(--text-muted);">優化後: </span>
                        <span class="card-val" style="color: {'var(--success)' if so['net_profit']>0 else 'var(--danger)'}">NT$ {so['net_profit']:,.0f}</span>
                    </div>
                </div>
                <div class="card-row" style="font-size:12px; color:var(--text-muted);">
                    <span>優化前: NT$ {sb['net_profit']:,.0f}</span>
                    <span class="badge-diff {'diff-pos' if so['net_profit']>=sb['net_profit'] else 'diff-neg'}">{'+' if so['net_profit']>=sb['net_profit'] else ''}{so['net_profit']-sb['net_profit']:+,.0f}</span>
                </div>
            </div>

            <div class="card">
                <div class="card-title">交易勝率 (Win Rate)</div>
                <div class="card-row">
                    <div>
                        <span style="font-size:12px; color:var(--text-muted);">優化後: </span>
                        <span class="card-val" style="color:var(--success);">{so['win_rate']:.1f}%</span>
                    </div>
                </div>
                <div class="card-row" style="font-size:12px; color:var(--text-muted);">
                    <span>優化前: {sb['win_rate']:.1f}%</span>
                    <span class="badge-diff {'diff-pos' if so['win_rate']>=sb['win_rate'] else 'diff-neg'}">{so['win_rate']-sb['win_rate']:+.2f}%</span>
                </div>
            </div>

            <div class="card">
                <div class="card-title">獲利因子 (Profit Factor)</div>
                <div class="card-row">
                    <div>
                        <span style="font-size:12px; color:var(--text-muted);">優化後: </span>
                        <span class="card-val">{so['profit_factor']:.2f}</span>
                    </div>
                </div>
                <div class="card-row" style="font-size:12px; color:var(--text-muted);">
                    <span>優化前: {sb['profit_factor']:.2f}</span>
                    <span class="badge-diff {'diff-pos' if so['profit_factor']>=sb['profit_factor'] else 'diff-neg'}">{so['profit_factor']-sb['profit_factor']:+.2f}</span>
                </div>
            </div>

            <div class="card">
                <div class="card-title">最大回撤 (Max Drawdown)</div>
                <div class="card-row">
                    <div>
                        <span style="font-size:12px; color:var(--text-muted);">優化後: </span>
                        <span class="card-val" style="color:var(--warning);">{so['max_drawdown_pct']:.2f}%</span>
                    </div>
                </div>
                <div class="card-row" style="font-size:12px; color:var(--text-muted);">
                    <span>優化前: {sb['max_drawdown_pct']:.2f}%</span>
                    <span class="badge-diff {'diff-pos' if so['max_drawdown_pct']<=sb['max_drawdown_pct'] else 'diff-neg'}">{so['max_drawdown_pct']-sb['max_drawdown_pct']:+.2f}%</span>
                </div>
            </div>
        </div>

        <div class="chart-box">
            <div class="chart-header">
                <div class="chart-title">📈 累計權益曲線 (Equity Curves Benchmark)</div>
                <div style="font-size:13px; color:var(--text-muted);">藍線: 優化後 (VWAP+量能+50% CE+保本) | 灰虛線: 優化前基準</div>
            </div>
            <div style="height: 380px;">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <div class="card" style="margin-bottom: 30px;">
            <div class="card-title" style="margin-bottom: 15px;">🔍 核心最佳化架構與風控對照</div>
            <table>
                <thead>
                    <tr>
                        <th>評估維度</th>
                        <th>優化前基準 (Baseline)</th>
                        <th>優化後新版 (Optimized Silver Bullet)</th>
                        <th>機制升級效益</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td><strong>過濾管道 (Filters)</strong></td>
                        <td>無過濾 (易遭震盪微型缺口欺騙)</td>
                        <td>VWAP 籌碼趨勢 + Volume Spike 1.3x 突波過濾</td>
                        <td>過濾了 {so['filtered_count']} 筆假突破與逆向接刀單</td>
                    </tr>
                    <tr>
                        <td><strong>進場撮合 (Entry)</strong></td>
                        <td>位移 K 棒收盤價市價立即進場</td>
                        <td>50% CE (Consequent Encroachment) 限價回踩撮合</td>
                        <td>提升單筆盈虧比，避免追高殺低</td>
                    </tr>
                    <tr>
                        <td><strong>停損防守 (Stop Loss)</strong></td>
                        <td>突破 K 棒最低點外加 3 點</td>
                        <td>Sweep 結構波段極值防守</td>
                        <td>保護止損免於隨機洗盤掃蕩</td>
                    </tr>
                    <tr>
                        <td><strong>倉位風控 (Risk & BE)</strong></td>
                        <td>無盤中保本機制</td>
                        <td>浮盈達 1.0R 自動將止損提升至進場成本價</td>
                        <td>{so['be_trades']} 筆交易成功在回吐前鎖定保本，大幅平滑資金曲線</td>
                    </tr>
                    <tr>
                        <td><strong>時段規範 (Killzone)</strong></td>
                        <td>固定時鐘時段</td>
                        <td>美國日光節約時間 (DST) 自適應 (夏令 21:30 / 冬令 22:30)</td>
                        <td>精準同步華爾街流動性注入時刻</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <div class="card">
            <div class="card-title" style="margin-bottom: 15px;">📜 最新成交明細 (展示最近 20 筆真實撮合記錄)</div>
            <table>
                <thead>
                    <tr>
                        <th>編號</th>
                        <th>方向</th>
                        <th>進場時間</th>
                        <th>進場價</th>
                        <th>出場時間</th>
                        <th>出場價</th>
                        <th>出場類型</th>
                        <th>淨損益 (NT$)</th>
                        <th>Killzone</th>
                        <th>量能倍數</th>
                    </tr>
                </thead>
                <tbody>
                    {recent_trades_html}
                </tbody>
            </table>
        </div>
    </div>

    <script>
        const ctx = document.getElementById('equityChart').getContext('2d');
        const bLabels = {json.dumps(b_labels)};
        const bData = {json.dumps(b_data)};
        const oLabels = {json.dumps(o_labels)};
        const oData = {json.dumps(o_data)};

        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: oLabels,
                datasets: [
                    {{
                        label: '優化後 (Optimized Silver Bullet)',
                        data: oData,
                        borderColor: '#3b82f6',
                        backgroundColor: 'rgba(59, 130, 246, 0.08)',
                        borderWidth: 2.5,
                        fill: true,
                        tension: 0.1,
                        pointRadius: 0
                    }},
                    {{
                        label: '優化前 (Baseline)',
                        data: bData,
                        borderColor: '#64748b',
                        borderWidth: 1.5,
                        borderDash: [5, 5],
                        fill: false,
                        tension: 0.1,
                        pointRadius: 0
                    }}
                ]
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
        f.write(html_content)

    print(f"\n[HTML 報告已生成]: {os.path.abspath(report_file)}")
    return report_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="台指期 SMC 銀色子彈新舊版本對比回測腳本")
    parser.add_argument("--code", type=str, default="TXFR1", help="合約代碼 (預設: TXFR1)")
    parser.add_argument("--contract", type=str, default="MTX", choices=["MTX", "TX"], help="交易標的規格 (MTX: 小台指, TX: 大台指)")
    parser.add_argument("--start", type=str, default="2026-01-01", help="回測起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2026-09-16", help="回測結束日期 (YYYY-MM-DD)")
    parser.add_argument("--db", type=str, default="", help="自訂 SQLite 資料庫路徑")

    args = parser.parse_args()
    db_path = args.db if args.db else get_db_path()

    df_1k, df_5k = load_historical_data(
        db_path=db_path,
        code=args.code,
        start_date=args.start,
        end_date=args.end
    )

    run_comparison(df_1k, df_5k, contract_type=args.contract)
