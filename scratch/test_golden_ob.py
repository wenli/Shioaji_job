import os
import sys
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1")

# Test 1: Restrict to profitable hours [10, 11, 20]
print("\n--- Test 1: Hours [10, 11, 20] Only ---")
cfg = OrderBlockConfig(
    contract_type="MTX",
    allowed_hours=[10, 11, 20],
    min_fvg_points=4.0,
    min_sl_points=25.0
)
engine = OrderBlockEngine(df_1k, df_5k, cfg)
summary, trades, _ = engine.run_backtest()
df_tr = pd.DataFrame(trades)
df_tr['entry_time'] = pd.to_datetime(df_tr['entry_time'])
df_tr['year'] = df_tr['entry_time'].dt.year
print(f"MTX [10, 11, 20]: Net PnL = {summary['net_profit']:+,.0f}, PF = {summary['profit_factor']}, WR = {summary['win_rate']}%, Trades = {summary['total_trades']}")
for yr, g in df_tr.groupby('year'):
    print(f"  Year {yr}: PnL = {g['net_pnl'].sum():+,.0f}, Trades = {len(g)}, WR = {(g['net_pnl']>0).mean()*100:.1f}%")

# Test 2: In TX
cfg_tx = OrderBlockConfig(
    contract_type="TX",
    allowed_hours=[10, 11, 20],
    min_fvg_points=4.0,
    min_sl_points=25.0
)
engine_tx = OrderBlockEngine(df_1k, df_5k, cfg_tx)
sum_tx, tr_tx, _ = engine_tx.run_backtest()
print(f"TX [10, 11, 20]: Net PnL = {sum_tx['net_profit']:+,.0f}, PF = {sum_tx['profit_factor']}, WR = {sum_tx['win_rate']}%, Trades = {sum_tx['total_trades']}")
