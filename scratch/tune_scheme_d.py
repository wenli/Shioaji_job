import os
import sys
import pandas as pd
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine, OrderBlockTracker
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1", start_date="2024-01-01", end_date="2026-09-16")
base_cfg = OrderBlockConfig(contract_type="MTX", max_lots=2)
prepared_df, _ = OrderBlockTracker.prepare_dataset(df_1k, df_5k, base_cfg)

grid_d = [
    ("D1: 1.8R / 3.2R, FVG 6.0, SL 25", 6.0, 25.0, 40, 0.35, "fixed_rr", 1.8, 3.2),
    ("D2: 2.0R / 3.5R, FVG 6.0, SL 25", 6.0, 25.0, 40, 0.35, "fixed_rr", 2.0, 3.5),
    ("D3: 2.0R / 3.0R, FVG 6.0, SL 25", 6.0, 25.0, 40, 0.35, "fixed_rr", 2.0, 3.0),
    ("D4: 2.0R / 4.0R, FVG 6.0, SL 25", 6.0, 25.0, 40, 0.35, "fixed_rr", 2.0, 4.0),
    ("D5: 2.0R / 3.5R, FVG 6.0, SL 22", 6.0, 22.0, 40, 0.35, "fixed_rr", 2.0, 3.5),
    ("D6: 2.0R / 3.5R, FVG 7.0, SL 25", 7.0, 25.0, 40, 0.35, "fixed_rr", 2.0, 3.5),
    ("D7: 2.2R / 3.8R, FVG 6.0, SL 25", 6.0, 25.0, 40, 0.35, "fixed_rr", 2.2, 3.8),
]

for name, fvg, sl, age, wick, mode, r1, r2 in grid_d:
    cfg = OrderBlockConfig(
        contract_type="MTX",
        max_lots=2,
        swing_window=5,
        min_fvg_points=fvg,
        max_ob_age_bars=age,
        min_wick_ratio=wick,
        min_sl_points=sl,
        tp1_mode=mode,
        tp1_fixed_rr=r1,
        tp2_fixed_rr=r2,
        enable_trend_filter=True,
        allowed_hours=[10, 11, 20]
    )
    _, obs = OrderBlockTracker.detect_5k_order_blocks(df_5k, cfg)
    engine = OrderBlockEngine(df_1k, df_5k, cfg)
    engine.df = prepared_df.copy()
    engine.order_blocks = obs
    s, trades, _ = engine.run_backtest()
    df_tr = pd.DataFrame(trades)
    df_tr['entry_time'] = pd.to_datetime(df_tr['entry_time'])
    df_tr['year'] = df_tr['entry_time'].dt.year
    p24 = df_tr[df_tr['year']==2024]['net_pnl'].sum()
    p25 = df_tr[df_tr['year']==2025]['net_pnl'].sum()
    p26 = df_tr[df_tr['year']==2026]['net_pnl'].sum()
    print(f"{name: <35} | Tot={df_tr['net_pnl'].sum():>8,.0f} | PF={s['profit_factor']:.2f} | MDD={s['max_drawdown_pct']:.1f}% | 24={p24:>7,.0f} | 25={p25:>7,.0f} | 26={p26:>7,.0f}")
