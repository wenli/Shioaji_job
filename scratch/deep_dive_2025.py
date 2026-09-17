import os
import sys
import sqlite3
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1")

cfg = SweepFadeConfig(contract_type="MTX")
engine = SweepFadeEngine(df_1k, df_5k, cfg)
summary, trades, eq_curve = engine.run_backtest()

df_trades = pd.DataFrame(trades)
df_trades['entry_time'] = pd.to_datetime(df_trades['entry_time'])
df_trades['year'] = df_trades['entry_time'].dt.year
df_trades['month'] = df_trades['entry_time'].dt.month
df_trades['swept_pool'] = df_trades['indicators'].apply(lambda x: x.get('swept_pool') if isinstance(x, dict) else '')

t2025 = df_trades[df_trades['year'] == 2025]

print("=== 2025 R:R and Win/Loss Magnitude Analysis ===")
wins = t2025[t2025['net_pnl'] > 0]
losses = t2025[t2025['net_pnl'] < 0]
be = t2025[t2025['exit_reason'].str.contains('BE', na=False)]

print(f"Total Trades: {len(t2025)}")
print(f"Wins: {len(wins)} (Avg Win: {wins['net_pnl'].mean():,.1f}, Max Win: {wins['net_pnl'].max():,.1f})")
print(f"Losses: {len(losses)} (Avg Loss: {losses['net_pnl'].mean():,.1f}, Max Loss: {losses['net_pnl'].min():,.1f})")
print(f"BE Trades: {len(be)} (Avg PnL: {be['net_pnl'].mean():,.1f})")
print(f"Payoff Ratio (Avg Win / |Avg Loss|): {wins['net_pnl'].mean() / abs(losses['net_pnl'].mean()):.2f}")

print("\n=== 2025 Loss Distribution by Stage ===")
print(t2025.groupby(['stage', 'exit_reason'])['net_pnl'].agg(['count', 'sum', 'mean']))

print("\n=== 2025 Deep Dive into Bad Months (Jul, Aug, Sep, Dec) ===")
bad_months = t2025[t2025['month'].isin([7, 8, 9, 12])]
print(bad_months.groupby(['month', 'swept_pool', 'side'])['net_pnl'].agg(['count', 'sum', 'mean']))

print("\n=== Why 2026 is so profitable compared to 2025? ===")
t2026 = df_trades[df_trades['year'] == 2026]
wins26 = t2026[t2026['net_pnl'] > 0]
losses26 = t2026[t2026['net_pnl'] < 0]
print(f"2026 Avg Win: {wins26['net_pnl'].mean():,.1f}, Avg Loss: {losses26['net_pnl'].mean():,.1f}, Payoff Ratio: {wins26['net_pnl'].mean() / abs(losses26['net_pnl'].mean()):.2f}")
print("2026 PnL by Exit Reason:")
print(t2026.groupby('exit_reason')['net_pnl'].agg(['count', 'sum', 'mean']))
