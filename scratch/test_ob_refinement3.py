import os
import sys
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1")

hour_options = [
    ("Hours [10, 11, 20]", [10, 11, 20], 4.0, 25.0, "swing_target", 1.5, 3.0),
    ("Hours [10, 11, 14, 20]", [10, 11, 14, 20], 4.0, 25.0, "swing_target", 1.5, 3.0),
    ("Hours [10, 11, 20, 4]", [10, 11, 20, 4], 4.0, 25.0, "swing_target", 1.5, 3.0),
    ("Hours [10, 11, 20] + FVG>=6.0", [10, 11, 20], 6.0, 25.0, "swing_target", 1.5, 3.0),
    ("Hours [10, 11, 20] + FVG>=8.0", [10, 11, 20], 8.0, 25.0, "swing_target", 1.5, 3.0),
    ("Hours [10, 11, 20] + Fixed RR (1.5R / 3.0R)", [10, 11, 20], 4.0, 25.0, "fixed_rr", 1.5, 3.0),
    ("Hours [10, 11, 20] + Fixed RR (2.0R / 4.0R)", [10, 11, 20], 4.0, 25.0, "fixed_rr", 2.0, 4.0),
    ("Hours [10, 11, 20] + Min SL 30 pts", [10, 11, 20], 4.0, 30.0, "swing_target", 1.5, 3.0),
]

for contract in ["MTX", "TX"]:
    print(f"\n==================== Contract: {contract} ====================")
    for name, hrs, fvg, min_sl, tp1_mode, r1, r2 in hour_options:
        cfg = OrderBlockConfig(
            contract_type=contract,
            allowed_hours=hrs,
            min_fvg_points=fvg,
            min_sl_points=min_sl,
            tp1_mode=tp1_mode,
            tp1_fixed_rr=r1,
            tp2_fixed_rr=r2,
            enable_trend_filter=True
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
        tot = df_tr['net_pnl'].sum()
        wr = (df_tr['net_pnl'] > 0).mean() * 100
        pf = summary['profit_factor']
        mdd = summary['max_drawdown_pct']

        print(f"[{name: <46}] Tot={tot:>9,.0f} | 2024={pnl_24:>8,.0f} | 2025={pnl_25:>8,.0f} | 2026={pnl_26:>9,.0f} | WR={wr:.1f}% | PF={pf:.2f} | MDD={mdd:.1f}% | Trades={len(df_tr)}")
