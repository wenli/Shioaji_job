import os
import sys
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
print(f"Loading data from {db_path}...")
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1", start_date="2024-01-01", end_date="2026-09-16")
print(f"Loaded 1K: {len(df_1k)}, 5K: {len(df_5k)}")

# Define parameter combinations to explore
experiments = []

# Baseline variations
for hrs, hrs_name in [
    ([10, 11, 20], "10,11,20"),
    ([10, 11, 14, 20], "10,11,14,20"),
    ([10, 11, 15, 20], "10,11,15,20"),
]:
    for fvg in [4.0, 6.0, 8.0, 10.0]:
        for sl in [20.0, 25.0, 30.0]:
            for age in [12, 24, 40]:
                for wick in [0.30, 0.35, 0.40]:
                    for tp_mode, r1, r2 in [
                        ("swing_target", 1.5, 3.0),
                        ("fixed_rr", 1.5, 3.0),
                        ("fixed_rr", 2.0, 3.5),
                    ]:
                        experiments.append({
                            'hrs': hrs,
                            'hrs_name': hrs_name,
                            'fvg': fvg,
                            'sl': sl,
                            'age': age,
                            'wick': wick,
                            'tp_mode': tp_mode,
                            'r1': r1,
                            'r2': r2,
                            'trend_filter': True
                        })

print(f"Candidate combinations: {len(experiments)}")

results = []

for idx, p in enumerate(experiments):
    cfg = OrderBlockConfig(
        contract_type="MTX",
        max_lots=2,
        swing_window=5,
        min_fvg_points=p['fvg'],
        max_ob_age_bars=p['age'],
        min_wick_ratio=p['wick'],
        min_sl_points=p['sl'],
        tp1_mode=p['tp_mode'],
        tp1_fixed_rr=p['r1'],
        tp2_fixed_rr=p['r2'],
        enable_trend_filter=p['trend_filter'],
        allowed_hours=p['hrs']
    )
    engine = OrderBlockEngine(df_1k, df_5k, cfg)
    summary, trades, _ = engine.run_backtest()
    if not trades:
        continue
    
    df_tr = pd.DataFrame(trades)
    df_tr['entry_time'] = pd.to_datetime(df_tr['entry_time'])
    df_tr['year'] = df_tr['entry_time'].dt.year

    pnl_24 = df_tr[df_tr['year']==2024]['net_pnl'].sum() if 2024 in df_tr['year'].values else 0
    pnl_25 = df_tr[df_tr['year']==2025]['net_pnl'].sum() if 2025 in df_tr['year'].values else 0
    pnl_26 = df_tr[df_tr['year']==2026]['net_pnl'].sum() if 2026 in df_tr['year'].values else 0
    tot_pnl = df_tr['net_pnl'].sum()
    wr = (df_tr['net_pnl'] > 0).mean() * 100
    pf = summary['profit_factor']
    mdd = summary['max_drawdown_pct']

    if len(df_tr) >= 25:
        results.append({
            'hrs': p['hrs_name'],
            'fvg': p['fvg'],
            'sl': p['sl'],
            'age': p['age'],
            'wick': p['wick'],
            'tp_mode': p['tp_mode'],
            'r1': p['r1'],
            'r2': p['r2'],
            'trades': len(df_tr),
            'tot_pnl': tot_pnl,
            'pnl_24': pnl_24,
            'pnl_25': pnl_25,
            'pnl_26': pnl_26,
            'wr': wr,
            'pf': pf,
            'mdd': mdd,
            'all_pos': (pnl_24 > 0 and pnl_25 > 0 and pnl_26 > 0)
        })

df_res = pd.DataFrame(results)
if not df_res.empty:
    df_res.to_csv("scratch/ob_2lots_grid_results.csv", index=False)
    print("\n--- Top 15 sorted by PF (filtered all_pos == True) ---")
    pos_res = df_res[df_res['all_pos'] == True].sort_values(by='pf', ascending=False)
    if not pos_res.empty:
        print(pos_res.head(15).to_string())
    else:
        print("No combo had 3 positive years, showing top PF overall:")
        print(df_res.sort_values(by='pf', ascending=False).head(15).to_string())
