import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import pandas as pd
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data
from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code='TXFR1', start_date='2026-01-01', end_date='2026-09-16')

bar_range = df_1k['high'] - df_1k['low']
print(f"Average 1K bar range in 2026: {bar_range.mean():.2f} pts")
print(f"Median 1K bar range in 2026: {bar_range.median():.2f} pts")
print(f"75th percentile: {bar_range.quantile(0.75):.2f} pts")
print(f"90th percentile: {bar_range.quantile(0.90):.2f} pts")

tests = [
    ("Default (All pools, min_sl=12)", SweepFadeConfig(contract_type='MTX')),
    ("No 5K swings (PDH/PDL + ORB)", SweepFadeConfig(contract_type='MTX', enable_htf_swings=False)),
    ("Only PDH/PDL", SweepFadeConfig(contract_type='MTX', enable_htf_swings=False, enable_orb_pools=False)),
    ("Only ORB", SweepFadeConfig(contract_type='MTX', enable_htf_swings=False, enable_pdh_pdl=False)),
    ("All pools with min_sl=20", SweepFadeConfig(contract_type='MTX', min_sl_points=20.0)),
    ("All pools with min_sl=25", SweepFadeConfig(contract_type='MTX', min_sl_points=25.0)),
    ("No 5K swings with min_sl=20", SweepFadeConfig(contract_type='MTX', enable_htf_swings=False, min_sl_points=20.0)),
    ("No 5K swings with min_sl=25", SweepFadeConfig(contract_type='MTX', enable_htf_swings=False, min_sl_points=25.0)),
]

for label, cfg in tests:
    engine = SweepFadeEngine(df_1k, df_5k, cfg)
    s, t, _ = engine.run_backtest()
    print(f"\n--- {label} ---")
    print(f"Trades: {s['total_trades']}, WinRate: {s['win_rate']}%, PF: {s['profit_factor']}, Net: NT$ {s['net_profit']:+,.0f}, MDD: {s['max_drawdown_pct']}%, TP1: {s['tp1_count']}, TP2: {s['tp2_count']}")
