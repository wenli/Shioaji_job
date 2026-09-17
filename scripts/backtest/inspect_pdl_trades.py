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
print("=== ONLY PDH/PDL TRADES BREAKDOWN ===")
print(f"Total trades: {len(df_trades)}")
print(f"Win Rate: {s['win_rate']}%, PF: {s['profit_factor']}, Net: NT$ {s['net_profit']:+,.0f}, MDD: {s['max_drawdown_pct']}%")

print("\nBy Exit Reason:")
print(df_trades.groupby('exit_reason')[['pnl_points', 'net_pnl']].agg(['count', 'sum', 'mean']))

print("\nBy Pool (PDH vs PDL):")
df_trades['pool'] = df_trades['indicators'].apply(lambda x: x.get('swept_pool', ''))
print(df_trades.groupby('pool')[['pnl_points', 'net_pnl']].agg(['count', 'sum', 'mean']))

print("\nBy Side (BUY vs SELL):")
print(df_trades.groupby('side')[['pnl_points', 'net_pnl']].agg(['count', 'sum', 'mean']))
