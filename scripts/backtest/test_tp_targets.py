import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import pandas as pd
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data
from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code='TXFR1', start_date='2026-01-01', end_date='2026-09-16')

# Let's test modifying tp2 target:
# Instead of unbounded PDL, what if tp2_mode is fixed_rr (e.g. 2.0R, 2.5R, 3.0R) or opposite_pool capped at fixed_rr?

print("=== TESTING TP2 MODES & R-MULTIPLES ON PDH/PDL + ORB ===")

for tp2_r in [1.8, 2.0, 2.5, 3.0]:
    for tp1_r in [1.0, 1.2, 1.5]:
        cfg = SweepFadeConfig(
            contract_type='MTX',
            enable_htf_swings=False,
            enable_pdh_pdl=True,
            enable_orb_pools=True,
            min_sl_points=25.0,
            tp1_mode='vwap',
            tp1_fixed_rr=tp1_r,
            tp2_mode='fixed_rr',
            tp2_fixed_rr=tp2_r,
            risk_pct=0.01
        )
        engine = SweepFadeEngine(df_1k, df_5k, cfg)
        s, t, _ = engine.run_backtest()
        print(f"TP1_R={tp1_r}, TP2_R={tp2_r} | WinRate: {s['win_rate']}%, PF: {s['profit_factor']}, Net: NT$ {s['net_profit']:+,.0f}, MDD: {s['max_drawdown_pct']}%, TP1: {s['tp1_count']}, TP2: {s['tp2_count']}")
