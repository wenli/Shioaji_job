import os
import sys
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine, OrderBlockTracker
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1")

# Let's inspect the losing trades of the initial Order Block
cfg = OrderBlockConfig(contract_type="MTX")
engine = OrderBlockEngine(df_1k, df_5k, cfg)
summary, trades, _ = engine.run_backtest()
df_tr = pd.DataFrame(trades)
df_tr['entry_time'] = pd.to_datetime(df_tr['entry_time'])
df_tr['hour'] = df_tr['entry_time'].dt.hour
df_tr['year'] = df_tr['entry_time'].dt.year

print("Total trades:", len(df_tr))
print("Win rate:", (df_tr['net_pnl'] > 0).mean() * 100)
print("Avg Win:", df_tr[df_tr['net_pnl'] > 0]['net_pnl'].mean())
print("Avg Loss:", df_tr[df_tr['net_pnl'] < 0]['net_pnl'].mean())
print("\nPnL by Exit Reason:")
print(df_tr.groupby('exit_reason')['net_pnl'].agg(['count', 'sum', 'mean']))

print("\nPnL by Side:")
print(df_tr.groupby('side')['net_pnl'].agg(['count', 'sum', 'mean']))

print("\nPnL by Hour:")
print(df_tr.groupby('hour')['net_pnl'].agg(['count', 'sum', 'mean']))
