import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import pandas as pd
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data
from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code='TXFR1', start_date='2026-01-01', end_date='2026-09-16')

# Compare sizing:
# We will modify SweepFadeEngine slightly or pass config to test:
# What if min_lots=2 and max_lots=2 (Fixed 2 lots: 1 lot TP1, 1 lot TP2)?

# Let's test min_sl_points = 25, 30, 35 with No 5K Swings
print("=== FIXED LOTS VS DYNAMIC LOTS & SL THRESHOLDS (NO 5K SWINGS) ===")

configs = [
    ("No 5K, min_sl=25, risk=1%", SweepFadeConfig(contract_type='MTX', enable_htf_swings=False, min_sl_points=25.0, risk_pct=0.01)),
    ("No 5K, min_sl=30, risk=1%", SweepFadeConfig(contract_type='MTX', enable_htf_swings=False, min_sl_points=30.0, risk_pct=0.01)),
    ("No 5K, min_sl=35, risk=1%", SweepFadeConfig(contract_type='MTX', enable_htf_swings=False, min_sl_points=35.0, risk_pct=0.01)),
    ("Only ORB, min_sl=25, risk=1%", SweepFadeConfig(contract_type='MTX', enable_htf_swings=False, enable_pdh_pdl=False, min_sl_points=25.0, risk_pct=0.01)),
    ("Only ORB, min_sl=30, risk=1%", SweepFadeConfig(contract_type='MTX', enable_htf_swings=False, enable_pdh_pdl=False, min_sl_points=30.0, risk_pct=0.01)),
    ("Only PDH/PDL, min_sl=25, risk=1%", SweepFadeConfig(contract_type='MTX', enable_htf_swings=False, enable_orb_pools=False, min_sl_points=25.0, risk_pct=0.01)),
    ("Only PDH/PDL, min_sl=30, risk=1%", SweepFadeConfig(contract_type='MTX', enable_htf_swings=False, enable_orb_pools=False, min_sl_points=30.0, risk_pct=0.01)),
]

for label, cfg in configs:
    engine = SweepFadeEngine(df_1k, df_5k, cfg)
    s, t, _ = engine.run_backtest()
    print(f"\n{label}:")
    print(f"Trades: {s['total_trades']}, WinRate: {s['win_rate']}%, PF: {s['profit_factor']}, Net: NT$ {s['net_profit']:+,.0f}, MDD: {s['max_drawdown_pct']}%, TP1: {s['tp1_count']}, TP2: {s['tp2_count']}")
