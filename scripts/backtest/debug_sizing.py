import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import pandas as pd
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data
from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code='TXFR1', start_date='2026-01-01', end_date='2026-09-16')

cfg = SweepFadeConfig(contract_type='MTX')
engine = SweepFadeEngine(df_1k, df_5k, cfg)
summary, trades, eq = engine.run_backtest()

df_trades = pd.DataFrame(trades)
print("=== LOTS DESCRIPTION ===")
print(df_trades['lots'].describe())

print("\n=== FIRST 15 TRADES ===")
for t in trades[:15]:
    print(f"#{t['trade_no']} | {t['stage']} | lots={t['lots']} | pts={t['pnl_points']:.1f} | risk_pts={t['risk_points']:.1f} | pnl={t['net_pnl']:.0f} | cap={t['capital_after']:.0f}")

print("\n=== WORST 10 TRADES BY LOSS ===")
worst = df_trades.sort_values('net_pnl').head(10)
for _, r in worst.iterrows():
    print(f"#{r['trade_no']} | {r['stage']} | lots={r['lots']} | pts={r['pnl_points']:.1f} | risk_pts={r['risk_points']:.1f} | pnl={r['net_pnl']:.0f} | cap={r['capital_after']:.0f}")

print("\n=== BEST 10 TRADES BY PROFIT ===")
best = df_trades.sort_values('net_pnl', ascending=False).head(10)
for _, r in best.iterrows():
    print(f"#{r['trade_no']} | {r['stage']} | lots={r['lots']} | pts={r['pnl_points']:.1f} | risk_pts={r['risk_points']:.1f} | pnl={r['net_pnl']:.0f} | cap={r['capital_after']:.0f}")
