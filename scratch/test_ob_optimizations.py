import os
import sys
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1")

tests = [
    ("Baseline (FVG>=4, age<=120)", 5, 4.0, 120, 0.35, "candle", True),
    ("Institutional FVG (FVG>=15, age<=40)", 5, 15.0, 40, 0.40, "candle", True),
    ("Institutional FVG (FVG>=20, age<=30)", 5, 20.0, 30, 0.40, "candle", True),
    ("Body OB + High FVG (FVG>=25, age<=36)", 5, 25.0, 36, 0.40, "body", True),
    ("Wider Swing (Window=7, FVG>=15, age<=36)", 7, 15.0, 36, 0.40, "candle", True),
    ("Strict Wick Confirmation (Wick>=45%, FVG>=20)", 5, 20.0, 36, 0.45, "candle", True),
    ("Trend Filter Fast EMA 9/21 + FVG>=18", 5, 18.0, 36, 0.40, "candle", True)
]

for contract in ["MTX", "TX"]:
    print(f"\n==================== Contract: {contract} ====================")
    for name, sw, fvg, age, wick, mode, tf in tests:
        cfg = OrderBlockConfig(
            contract_type=contract,
            swing_window=sw,
            min_fvg_points=fvg,
            max_ob_age_bars=age,
            min_wick_ratio=wick,
            ob_zone_mode=mode,
            enable_trend_filter=tf
        )
        engine = OrderBlockEngine(df_1k, df_5k, cfg)
        summary, trades, _ = engine.run_backtest()
        df_tr = pd.DataFrame(trades)
        if df_tr.empty:
            continue
        df_tr['entry_time'] = pd.to_datetime(df_tr['entry_time'])
        df_tr['year'] = df_tr['entry_time'].dt.year

        pnl_24 = df_tr[df_tr['year']==2024]['net_pnl'].sum() if 2024 in df_tr['year'].values else 0
        pnl_25 = df_tr[df_tr['year']==2025]['net_pnl'].sum() if 2025 in df_tr['year'].values else 0
        pnl_26 = df_tr[df_tr['year']==2026]['net_pnl'].sum() if 2026 in df_tr['year'].values else 0
        tot_pnl = df_tr['net_pnl'].sum()
        wr = (df_tr['net_pnl'] > 0).mean() * 100
        pf = summary['profit_factor']
        mdd = summary['max_drawdown_pct']

        print(f"[{name: <45}] Tot={tot_pnl:>9,.0f} | 2024={pnl_24:>8,.0f} | 2025={pnl_25:>8,.0f} | 2026={pnl_26:>9,.0f} | WR={wr:.1f}% | PF={pf:.2f} | MDD={mdd:.1f}% | Trades={len(df_tr)}")
