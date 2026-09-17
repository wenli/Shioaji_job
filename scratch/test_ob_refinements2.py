import os
import sys
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1")

experiments = [
    ("Fresh OB (Age <= 12 bars = 1 hr)", 5, 15.0, 12, 0.40, 25.0, True),
    ("Fresh OB (Age <= 8 bars = 40 min)", 5, 12.0, 8, 0.35, 25.0, True),
    ("Fresh OB (Age <= 6 bars = 30 min)", 5, 10.0, 6, 0.35, 25.0, True),
    ("Fresh OB + Tight SL 20 pts", 5, 10.0, 8, 0.40, 20.0, True),
    ("Golden Hours Only [10, 11, 15, 19, 20]", 5, 12.0, 10, 0.40, 25.0, True),
]

for contract in ["MTX", "TX"]:
    print(f"\n==================== Contract: {contract} ====================")
    for name, sw, fvg, age, wick, sl, tf in experiments:
        cfg = OrderBlockConfig(
            contract_type=contract,
            swing_window=sw,
            min_fvg_points=fvg,
            max_ob_age_bars=age,
            min_wick_ratio=wick,
            min_sl_points=sl,
            allowed_hours=[10, 11, 15, 19, 20, 21, 4] if "Golden" in name else [9, 10, 11, 15, 19, 20, 21, 4],
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

        print(f"[{name: <45}] Tot={tot_pnl:>9,.0f} | 2024={pnl_24:>8,.0f} | 2025={pnl_25:>8,.0f} | 2026={pnl_26:>9,.0f} | WR={wr:.1f}% | PF={pf:.2f} | Trades={len(df_tr)}")
