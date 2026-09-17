import os
import sys
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1")

for contract in ["MTX", "TX"]:
    print(f"\n=======================================================")
    print(f"               Contract: {contract}")
    print(f"=======================================================")
    
    # 1. Baseline
    cfg_base = SweepFadeConfig(
        contract_type=contract,
        enable_trend_filter=False,
        enable_dynamic_atr=False
    )
    eng_base = SweepFadeEngine(df_1k, df_5k, cfg_base)
    sum_base, tr_base, _ = eng_base.run_backtest()
    df_base = pd.DataFrame(tr_base)
    df_base['entry_time'] = pd.to_datetime(df_base['entry_time'])
    df_base['year'] = df_base['entry_time'].dt.year

    # 2. Optimized (Trend Filter + Dynamic ATR)
    cfg_opt = SweepFadeConfig(
        contract_type=contract,
        enable_trend_filter=True,
        enable_dynamic_atr=True
    )
    eng_opt = SweepFadeEngine(df_1k, df_5k, cfg_opt)
    sum_opt, tr_opt, _ = eng_opt.run_backtest()
    df_opt = pd.DataFrame(tr_opt)
    df_opt['entry_time'] = pd.to_datetime(df_opt['entry_time'])
    df_opt['year'] = df_opt['entry_time'].dt.year

    print("\n--- [BASELINE: No Filter] Performance by Year ---")
    for yr in [2024, 2025, 2026]:
        sub = df_base[df_base['year'] == yr]
        wins = sub[sub['net_pnl'] > 0]
        losses = sub[sub['net_pnl'] < 0]
        pnl = sub['net_pnl'].sum()
        wr = len(wins) / len(sub) * 100 if len(sub) > 0 else 0
        pf = wins['net_pnl'].sum() / abs(losses['net_pnl'].sum()) if len(losses) > 0 and losses['net_pnl'].sum() != 0 else 99
        print(f"Year {yr}: Trades={len(sub)}, WinRate={wr:.1f}%, NetPnL={pnl:,.0f}, PF={pf:.2f}, AvgPnL={sub['net_pnl'].mean():.1f}")
    print(f"TOTAL BASELINE: Trades={len(df_base)}, NetPnL={df_base['net_pnl'].sum():,.0f}")

    print("\n--- [OPTIMIZED: 5K Trend Filter + Dynamic ATR] Performance by Year ---")
    for yr in [2024, 2025, 2026]:
        sub = df_opt[df_opt['year'] == yr]
        wins = sub[sub['net_pnl'] > 0]
        losses = sub[sub['net_pnl'] < 0]
        pnl = sub['net_pnl'].sum()
        wr = len(wins) / len(sub) * 100 if len(sub) > 0 else 0
        pf = wins['net_pnl'].sum() / abs(losses['net_pnl'].sum()) if len(losses) > 0 and losses['net_pnl'].sum() != 0 else 99
        print(f"Year {yr}: Trades={len(sub)}, WinRate={wr:.1f}%, NetPnL={pnl:,.0f}, PF={pf:.2f}, AvgPnL={sub['net_pnl'].mean():.1f}")
    print(f"TOTAL OPTIMIZED: Trades={len(df_opt)}, NetPnL={df_opt['net_pnl'].sum():,.0f}")
