import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import pandas as pd
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data
from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code='TXFR1', start_date='2026-01-01', end_date='2026-09-16')

# Let's test filtering by allowed trading hours
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

# Filter 1: Exclude 09:00-10:00 and 16:00-19:00
filtered_trades = df_trades[~df_trades['hour'].isin([9, 16, 17, 18, 0, 1, 2, 3])].copy()

total = len(filtered_trades)
wins = filtered_trades[filtered_trades['net_pnl'] > 0]
losses = filtered_trades[filtered_trades['net_pnl'] < 0]
gp = wins['net_pnl'].sum()
gl = abs(losses['net_pnl'].sum())
pf = gp / gl if gl > 0 else 99.0
net = filtered_trades['net_pnl'].sum()
pts = filtered_trades['pnl_points'].sum()

print("=== EXCLUDING TOXIC HOURS (No 9am open shock, no 16-18 dinner, no 00-03 midnight) ===")
print(f"Total Trades: {total}")
print(f"Win Rate: {len(wins)/total*100:.2f}%")
print(f"Profit Factor: {pf:.2f}")
print(f"Gross Profit: NT$ {gp:,.0f}")
print(f"Gross Loss: NT$ {gl:,.0f}")
print(f"Net Profit: NT$ {net:+,.0f}")
print(f"Total Points: {pts:+,.1f} pts")
print(f"Avg PnL per Trade: NT$ {filtered_trades['net_pnl'].mean():+,.0f}")

# Filter 2: Pure Golden Windows: [10, 11, 15, 19, 20, 21, 4]
golden = df_trades[df_trades['hour'].isin([10, 11, 15, 19, 20, 21, 4])].copy()
tot_g = len(golden)
wins_g = golden[golden['net_pnl'] > 0]
loss_g = golden[golden['net_pnl'] < 0]
gp_g = wins_g['net_pnl'].sum()
gl_g = abs(loss_g['net_pnl'].sum())
pf_g = gp_g / gl_g if gl_g > 0 else 99.0

print("\n=== PURE GOLDEN WINDOWS (Hours: 10, 11, 15, 19, 20, 21, 4) ===")
print(f"Total Trades: {tot_g}")
print(f"Win Rate: {len(wins_g)/tot_g*100:.2f}%")
print(f"Profit Factor: {pf_g:.2f}")
print(f"Gross Profit: NT$ {gp_g:,.0f}")
print(f"Gross Loss: NT$ {gl_g:,.0f}")
print(f"Net Profit: NT$ {golden['net_pnl'].sum():+,.0f}")
print(f"Total Points: {golden['pnl_points'].sum():+,.1f} pts")
print(f"Avg PnL per Trade: NT$ {golden['net_pnl'].mean():+,.0f}")
