import os
import sys
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1")

# Test custom config with tight SL cap and fixed RR 1.5R / 3.0R
for contract in ["MTX", "TX"]:
    print(f"\n==================== Contract: {contract} ====================")
    cfg = OrderBlockConfig(
        contract_type=contract,
        swing_window=5,
        min_fvg_points=12.0,
        max_ob_age_bars=40,
        min_wick_ratio=0.35,
        min_sl_points=25.0,
        tp1_mode="fixed_rr",
        tp1_fixed_rr=1.5,
        tp2_mode="fixed_rr",
        tp2_fixed_rr=3.0,
        allowed_hours=[9, 10, 11, 15, 19, 20, 21, 4],
        enable_trend_filter=True
    )
    engine = OrderBlockEngine(df_1k, df_5k, cfg)
    summary, trades, _ = engine.run_backtest()
    df_tr = pd.DataFrame(trades)
    if not df_tr.empty:
        df_tr['entry_time'] = pd.to_datetime(df_tr['entry_time'])
        df_tr['year'] = df_tr['entry_time'].dt.year
        for yr in [2024, 2025, 2026]:
            sub = df_tr[df_tr['year'] == yr]
            pnl = sub['net_pnl'].sum() if not sub.empty else 0
            wr = (sub['net_pnl'] > 0).mean() * 100 if not sub.empty else 0
            print(f"Year {yr}: Trades={len(sub)}, WinRate={wr:.1f}%, NetPnL={pnl:,.0f}")
        print(f"TOTAL: Trades={len(df_tr)}, NetPnL={df_tr['net_pnl'].sum():,.0f}, PF={summary['profit_factor']}, MDD={summary['max_drawdown_pct']:.1f}%")
