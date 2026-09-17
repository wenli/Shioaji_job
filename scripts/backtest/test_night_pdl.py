import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import pandas as pd
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data
from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code='TXFR1', start_date='2026-01-01', end_date='2026-09-16')

# Filter 1k to only night session or test an option
# Let's inspect the equity curve and metrics if we only take trades in the night session
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
night_trades = df_trades[~((df_trades['hour'] >= 8) & (df_trades['hour'] < 14))].copy()

total_trades = len(night_trades)
winning_trades = night_trades[night_trades['net_pnl'] > 0]
losing_trades = night_trades[night_trades['net_pnl'] < 0]
win_rate = len(winning_trades) / total_trades * 100
gross_profit = winning_trades['net_pnl'].sum()
gross_loss = abs(losing_trades['net_pnl'].sum())
pf = gross_profit / gross_loss
net_profit = night_trades['net_pnl'].sum()

print("=== NIGHT SESSION PDH/PDL SWEEP FADE PERFORMANCE ===")
print(f"Total Trades: {total_trades}")
print(f"Win Rate: {win_rate:.2f}%")
print(f"Profit Factor: {pf:.2f}")
print(f"Gross Profit: NT$ {gross_profit:,.0f}")
print(f"Gross Loss: NT$ {gross_loss:,.0f}")
print(f"Net Profit: NT$ {net_profit:+,.0f}")
print(f"Total Points: {night_trades['pnl_points'].sum():+,.1f} pts")
print(f"Avg PnL per Trade: NT$ {night_trades['net_pnl'].mean():+,.0f}")
