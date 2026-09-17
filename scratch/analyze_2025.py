import os
import sys
import sqlite3
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
print("DB Path:", db_path)

conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT min(ts), max(ts), count(*) FROM futures1k WHERE code='TXFR1'")
print("TXFR1 1K Range:", cur.fetchone())
cur.execute("SELECT min(ts), max(ts), count(*) FROM futures1k WHERE code='MXFR1'")
print("MXFR1 1K Range:", cur.fetchone())

# Load all historical data for TXFR1
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1")

for contract in ["MTX", "TX"]:
    print(f"\n==================== Analyzing Contract: {contract} ====================")
    cfg = SweepFadeConfig(contract_type=contract)
    engine = SweepFadeEngine(df_1k, df_5k, cfg)
    summary, trades, eq_curve = engine.run_backtest()
    
    df_trades = pd.DataFrame(trades)
    if df_trades.empty:
        print("No trades found.")
        continue
        
    df_trades['entry_time'] = pd.to_datetime(df_trades['entry_time'])
    df_trades['year'] = df_trades['entry_time'].dt.year
    df_trades['hour'] = df_trades['entry_time'].dt.hour
    df_trades['month'] = df_trades['entry_time'].dt.month
    
    df_trades['swept_pool'] = df_trades['indicators'].apply(lambda x: x.get('swept_pool') if isinstance(x, dict) else '')
    df_trades['penetration'] = df_trades['indicators'].apply(lambda x: x.get('penetration') if isinstance(x, dict) else 0.0)
    
    print("\n--- Performance by Year ---")
    for yr, group in df_trades.groupby('year'):
        wins = group[group['net_pnl'] > 0]
        losses = group[group['net_pnl'] < 0]
        be = group[group['exit_reason'].str.contains('BE', na=False)]
        pnl = group['net_pnl'].sum()
        wr = len(wins) / len(group) * 100 if len(group) > 0 else 0
        pf = wins['net_pnl'].sum() / abs(losses['net_pnl'].sum()) if len(losses) > 0 and losses['net_pnl'].sum() != 0 else 99
        print(f"Year {yr}: Trades={len(group)}, WinRate={wr:.1f}%, BE={len(be)}, NetPnL={pnl:,.0f}, PF={pf:.2f}, AvgPnL={group['net_pnl'].mean():.1f}")

    # Detailed comparison: 2025 vs 2026
    for target_yr in [2024, 2025, 2026]:
        ty = df_trades[df_trades['year'] == target_yr]
        if ty.empty:
            continue
        print(f"\n========== {target_yr} Breakdown ==========")
        print(f"Total Net PnL: {ty['net_pnl'].sum():,.0f}, Win Rate: {(ty['net_pnl']>0).mean()*100:.1f}%, Trades: {len(ty)}")
        
        print("--- By Side (BUY vs SELL) ---")
        for side, group in ty.groupby('side'):
            pnl = group['net_pnl'].sum()
            wr = len(group[group['net_pnl'] > 0]) / len(group) * 100
            print(f"  {side}: Count={len(group)}, WinRate={wr:.1f}%, PnL={pnl:,.0f}, Avg={group['net_pnl'].mean():.1f}")
            
        print("--- By Swept Pool ---")
        for pool, group in ty.groupby('swept_pool'):
            pnl = group['net_pnl'].sum()
            wr = len(group[group['net_pnl'] > 0]) / len(group) * 100
            print(f"  {pool}: Count={len(group)}, WinRate={wr:.1f}%, PnL={pnl:,.0f}, Avg={group['net_pnl'].mean():.1f}")

        print("--- By Exit Reason ---")
        for reason, group in ty.groupby('exit_reason'):
            pnl = group['net_pnl'].sum()
            print(f"  {reason}: Count={len(group)}, PnL={pnl:,.0f}, Avg={group['net_pnl'].mean():.1f}")

        print("--- By Hour ---")
        for h, group in ty.groupby('hour'):
            pnl = group['net_pnl'].sum()
            wr = len(group[group['net_pnl'] > 0]) / len(group) * 100
            print(f"  Hour {h:02d}: Count={len(group)}, WinRate={wr:.1f}%, PnL={pnl:,.0f}")
            
        print("--- By Month ---")
        for m, group in ty.groupby('month'):
            pnl = group['net_pnl'].sum()
            wr = len(group[group['net_pnl'] > 0]) / len(group) * 100
            print(f"  Month {m:02d}: Count={len(group)}, WinRate={wr:.1f}%, PnL={pnl:,.0f}")

print("\n--- Market Environment Comparison (Daily Range & Volatility) ---")
df_1k['trade_date'] = df_1k['datetime'].dt.date
df_1k['year'] = df_1k['datetime'].dt.year

for yr in [2024, 2025, 2026]:
    sub = df_1k[df_1k['year'] == yr]
    if sub.empty:
        continue
    daily = sub.groupby('trade_date').agg({'high': 'max', 'low': 'min', 'open': 'first', 'close': 'last', 'volume': 'sum'})
    daily['range'] = daily['high'] - daily['low']
    daily['return'] = daily['close'] - daily['open']
    print(f"Year {yr}: Days={len(daily)}, Avg Daily Range={daily['range'].mean():.1f} pts (Std={daily['range'].std():.1f}), Max Range={daily['range'].max():.1f}, Annual Trend={daily['close'].iloc[-1]-daily['open'].iloc[0]:+.0f} pts")
