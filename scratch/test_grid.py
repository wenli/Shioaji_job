import os
import sys
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1")

experiments = [
    ("Baseline (No Filter)", False, False, 3.0, 35.0, 20, 50, 0.08, 0.60),
    ("Trend Filter Only (EMA 20/50)", True, False, 3.0, 35.0, 20, 50, 0.08, 0.60),
    ("Trend Filter (EMA 9/21 Faster)", True, False, 3.0, 35.0, 9, 21, 0.08, 0.60),
    ("Dynamic ATR Only", False, True, 3.0, 35.0, 20, 50, 0.05, 0.75),
    ("Trend Filter + Adaptive ATR (0.05~0.80 ATR)", True, True, 3.0, 45.0, 20, 50, 0.05, 0.80),
    ("Trend Filter + Wider Max Pen (50 pts)", True, False, 3.0, 50.0, 20, 50, 0.08, 0.60),
    ("Trend Filter + Higher TP2 (Fixed RR 2.5R)", True, False, 3.0, 40.0, 20, 50, 0.08, 0.60),
]

for contract in ["MTX", "TX"]:
    print(f"\n=========================================================================================")
    print(f"                                   Contract: {contract}")
    print(f"=========================================================================================")
    for name, tf, datr, minp, maxp, ef, es, min_m, max_m in experiments:
        cfg = SweepFadeConfig(
            contract_type=contract,
            enable_trend_filter=tf,
            enable_dynamic_atr=datr,
            min_penetration=minp,
            max_penetration=maxp,
            ema_fast=ef,
            ema_slow=es,
            min_pen_atr_mult=min_m,
            max_pen_atr_mult=max_m
        )
        engine = SweepFadeEngine(df_1k, df_5k, cfg)
        summary, trades, _ = engine.run_backtest()
        df_tr = pd.DataFrame(trades)
        if df_tr.empty:
            continue
        df_tr['entry_time'] = pd.to_datetime(df_tr['entry_time'])
        df_tr['year'] = df_tr['entry_time'].dt.year
        
        pnl_24 = df_tr[df_tr['year']==2024]['net_pnl'].sum() if 2024 in df_tr['year'].values else 0
        pnl_25 = df_tr[df_tr['year']==2025]['net_pnl'].sum() if 2025 in df_tr['year'].values else 0
        pnl_26 = df_tr[df_tr['year']==2026]['net_pnl'].sum() if 2026 in df_tr['year'].values else 0
        tot_pnl = df_tr['net_pnl'].sum()
        
        w24 = (df_tr[df_tr['year']==2024]['net_pnl']>0).mean()*100
        w25 = (df_tr[df_tr['year']==2025]['net_pnl']>0).mean()*100
        w26 = (df_tr[df_tr['year']==2026]['net_pnl']>0).mean()*100
        
        print(f"[{name: <42}] Tot={tot_pnl:>9,.0f} | 2024={pnl_24:>8,.0f} ({w24:.0f}%) | 2025={pnl_25:>8,.0f} ({w25:.0f}%) | 2026={pnl_26:>9,.0f} ({w26:.0f}%) | Trades={len(df_tr)}")
