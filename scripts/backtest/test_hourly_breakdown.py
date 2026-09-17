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
    enable_orb_pools=True,
    enable_pdh_pdl=True,
    min_sl_points=30.0,
    risk_pct=0.01
)
engine = SweepFadeEngine(df_1k, df_5k, cfg)
s, trades, eq = engine.run_backtest()

df_trades = pd.DataFrame(trades)
df_trades['entry_dt'] = pd.to_datetime(df_trades['entry_time'])
df_trades['hour'] = df_trades['entry_dt'].dt.hour

print("=== HOURLY PERFORMANCE (ALL 24 HOURS) ===")
hourly = df_trades.groupby('hour').agg(
    trades=('trade_no', 'count'),
    points=('pnl_points', 'sum'),
    net_pnl=('net_pnl', 'sum'),
    win_rate=('net_pnl', lambda s: (s > 0).mean() * 100)
)
hourly['pf'] = df_trades.groupby('hour').apply(
    lambda g: g[g['net_pnl'] > 0]['net_pnl'].sum() / abs(g[g['net_pnl'] < 0]['net_pnl'].sum()) if abs(g[g['net_pnl'] < 0]['net_pnl'].sum()) > 0 else 0
)
print(hourly.to_string())
