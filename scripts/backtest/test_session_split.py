import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import pandas as pd
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data
from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code='TXFR1', start_date='2026-01-01', end_date='2026-09-16')

cfg = SweepFadeConfig(
    contract_type='MTX',
    enable_htf_swings=False,
    enable_orb_pools=False,
    enable_pdh_pdl=True,
    min_sl_points=30.0,
    risk_pct=0.01
)
engine = SweepFadeEngine(df_1k, df_5k, cfg)
s, trades, eq = engine.run_backtest()

df_trades = pd.DataFrame(trades)
df_trades['entry_dt'] = pd.to_datetime(df_trades['entry_time'])
df_trades['hour'] = df_trades['entry_dt'].dt.hour
df_trades['is_day'] = (df_trades['hour'] >= 8) & (df_trades['hour'] < 14)

print("=== DAY VS NIGHT SESSION PERFORMANCE (PDH/PDL) ===")
print("\nBy Session:")
print(df_trades.groupby('is_day')[['pnl_points', 'net_pnl']].agg(['count', 'sum', 'mean']))

print("\nBy Session and Side:")
print(df_trades.groupby(['is_day', 'side'])[['pnl_points', 'net_pnl']].agg(['count', 'sum', 'mean']))
