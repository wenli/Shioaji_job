# -*- coding: utf-8 -*-
import os
import sys
import sqlite3
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tx_backtest import SMCBacktestSimulator, TaiwanFuturesSMCEngine
from app.strategy.silver_bullet import SilverBulletConfig, SilverBulletEngine

db_path = r"C:\Intel\Database\Shioaji-future.db"
conn = sqlite3.connect(db_path)
df_1k = pd.read_sql_query("SELECT ts as datetime, open, high, low, close, volume FROM futures1k WHERE code='TXFR1' AND ts >= '2026-05-01 00:00:00' AND ts <= '2026-07-01 23:59:59' ORDER BY ts;", conn)
df_5k = pd.read_sql_query("SELECT ts as datetime, open, high, low, close, volume FROM futures5k WHERE code='TXFR1' AND ts >= '2026-05-01 00:00:00' AND ts <= '2026-07-01 23:59:59' ORDER BY ts;", conn)
conn.close()

df_1k['datetime'] = pd.to_datetime(df_1k['datetime'])
df_5k['datetime'] = pd.to_datetime(df_5k['datetime'])

def get_session(dt):
    t = dt.time()
    from datetime import time
    if time(8, 45) <= t <= time(13, 45):
        return 'day'
    else:
        return 'night'

df_1k['session'] = df_1k['datetime'].apply(get_session)
df_5k['session'] = df_5k['datetime'].apply(get_session)

print("Total 1k bars:", len(df_1k), "5k bars:", len(df_5k))

# 1. 測試 Unicorn
df_5k_analyzed = TaiwanFuturesSMCEngine.calculate_smc_htf_5k(df_5k)
df_merged = TaiwanFuturesSMCEngine.align_and_merge_timeframes(df_1k, df_5k_analyzed)
sim = SMCBacktestSimulator(df_merged, start_capital=1000000.0, contract_type='MTX')
res_uni, trades_uni, _ = sim.run_strategy('unicorn', rr_ratio=2.0)
print("\n[Unicorn 獨角獸策略]:")
print(res_uni)

from tx_backtest import SMCBacktestSimulator, TaiwanFuturesSMCEngine, ORBBacktestSimulator

# 2. 測試 ORB 策略
orb_sim = ORBBacktestSimulator(df_1k, start_capital=1000000.0, contract_type='MTX')
res_orb, trades_orb, _ = orb_sim.run_strategy(orb_probe_minutes=15, orb_breakout_ticks=5, vol_spike_ratio=1.2, momentum_threshold=0.0003, rr_ratio=2.0, sl_mode='range_edge', min_sl_points=20.0)
print("\n[ORB 開盤突破策略 (range_edge)]: ")
print(res_orb)

# 3. 測試 Silver Bullet 在不同時段 / 條件下的表現
# 檢查 Silver Bullet 為什麼交易次數少：
# 到底是哪個條件最嚴苛？
engine_sb = SilverBulletEngine(df_1k, df_5k, SilverBulletConfig(enable_vwap=True, enable_volume_spike=True, entry_mode='market_close'))
prepared = engine_sb.prepare_data()

in_kz_count = 0
htf_align_count = 0
sweep_count = 0
fvg_count = 0
full_signal_count = 0

for i in range(1, len(prepared)):
    dt = prepared['datetime'].iloc[i]
    in_kz, _ = engine_sb.is_in_killzone(dt)
    if in_kz:
        in_kz_count += 1
        c_cur = prepared['close'].iloc[i]
        htf_ob_b_bot = prepared['htf_ob_bullish_bottom'].iloc[i]
        htf_sweep_l = prepared['htf_sweep_low'].iloc[max(0, i-30):i+1]
        htf_bull_align = (not np.isnan(htf_ob_b_bot) and c_cur >= htf_ob_b_bot) or any(htf_sweep_l)
        if htf_bull_align:
            htf_align_count += 1
            sweep_l_recent = any(prepared['ltf_sweep_l'].iloc[max(0, i-5):i+1])
            if sweep_l_recent:
                sweep_count += 1
                if prepared['ltf_fvg_bullish'].iloc[i]:
                    fvg_count += 1

print(f"\n[Silver Bullet 漏斗分析 (日盤+夜盤 Killzone)]: In Killzone bars: {in_kz_count} | HTF Align: {htf_align_count} | Sweep recent: {sweep_count} | FVG formed: {fvg_count}")
