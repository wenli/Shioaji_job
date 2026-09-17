import os
import sys
import copy
import time
import pandas as pd
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine, OrderBlockTracker
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

t0 = time.time()
db_path = get_db_path()
print(f"載入台指期歷史數據 (2024-01-01 ~ 2026-09-16)...", flush=True)
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1", start_date="2024-01-01", end_date="2026-09-16")
print(f"載入完成: 1K={len(df_1k):,}, 5K={len(df_5k):,}, 耗時 {time.time()-t0:.2f}s", flush=True)

# 基準配置
base_cfg = OrderBlockConfig(contract_type="MTX", max_lots=2)

# 預先計算 1K 與 5K 合併數據集 (僅做一次)
t1 = time.time()
print("正在預先建構 1K/5K 特徵特徵矩陣 (VWAP, PDH/PDL, HTF EMA)...", flush=True)
prepared_df, _ = OrderBlockTracker.prepare_dataset(df_1k, df_5k, base_cfg)
print(f"特徵矩陣建置完成，耗時 {time.time()-t1:.2f}s", flush=True)

# 測試候選組合 (專注 2口限制下的穩健型與高效型配置)
candidates = [
    # (名稱, hours, fvg, min_sl, max_ob_age, min_wick, tp1_mode, r1, r2, enable_tf)
    ("基準: 現有預設 (黃金窗口 10/11/20, FVG 6, SL 25)", [10, 11, 20], 6.0, 25.0, 40, 0.35, "swing_target", 1.5, 3.0, True),
    ("方案 A: 嚴格 FVG 8.0 + 緊密生命週期 20 根", [10, 11, 20], 8.0, 25.0, 20, 0.35, "swing_target", 1.5, 3.0, True),
    ("方案 B: 嚴格 FVG 10.0 + 緊密生命週期 20 根", [10, 11, 20], 10.0, 25.0, 20, 0.35, "swing_target", 1.5, 3.0, True),
    ("方案 C: 固定 RR (1.5R / 3.0R) + FVG 6.0", [10, 11, 20], 6.0, 25.0, 40, 0.35, "fixed_rr", 1.5, 3.0, True),
    ("方案 D: 固定 RR (2.0R / 3.5R) + FVG 6.0", [10, 11, 20], 6.0, 25.0, 40, 0.35, "fixed_rr", 2.0, 3.5, True),
    ("方案 E: 固定 RR (1.5R / 3.0R) + FVG 8.0 + SL 25", [10, 11, 20], 8.0, 25.0, 24, 0.35, "fixed_rr", 1.5, 3.0, True),
    ("方案 F: 保底 SL 30 點 + FVG 6.0", [10, 11, 20], 6.0, 30.0, 40, 0.35, "swing_target", 1.5, 3.0, True),
    ("方案 G: 保底 SL 20 點 + FVG 8.0 + 影線 0.40", [10, 11, 20], 8.0, 20.0, 20, 0.40, "swing_target", 1.5, 3.0, True),
    ("方案 H: 擴充黃金時段 [10, 11, 14, 20] + FVG 6.0", [10, 11, 14, 20], 6.0, 25.0, 40, 0.35, "swing_target", 1.5, 3.0, True),
    ("方案 I: 擴充黃金時段 [10, 11, 14, 20] + FVG 8.0", [10, 11, 14, 20], 8.0, 25.0, 24, 0.35, "swing_target", 1.5, 3.0, True),
    ("方案 J: 擴充黃金時段 [10, 11, 14, 20] + 固定 RR (1.5R / 3.0R)", [10, 11, 14, 20], 6.0, 25.0, 30, 0.35, "fixed_rr", 1.5, 3.0, True),
    ("方案 K: 日盤純趨勢 [10, 11] + FVG 6.0 + 快速移保本", [10, 11], 6.0, 25.0, 30, 0.35, "fixed_rr", 1.5, 3.0, True),
    ("方案 L: 歐盤順勢 [19, 20, 21] + FVG 6.0", [19, 20, 21], 6.0, 25.0, 30, 0.35, "swing_target", 1.5, 3.0, True),
    ("方案 M: 全時段回踩 [10, 11, 15, 20, 4] + FVG 8.0", [10, 11, 15, 20, 4], 8.0, 25.0, 24, 0.35, "swing_target", 1.5, 3.0, True),
    ("方案 N: 嚴選回踩 (FVG 10.0 + SL 25 + 固定 2.0R/4.0R)", [10, 11, 20], 10.0, 25.0, 20, 0.35, "fixed_rr", 2.0, 4.0, True),
]

results = []
print(f"開始評估 {len(candidates)} 種 2 口限制策略配置...\n", flush=True)

for name, hrs, fvg, sl, age, wick, tp_mode, r1, r2, tf in candidates:
    cfg = OrderBlockConfig(
        contract_type="MTX",
        max_lots=2,
        swing_window=5,
        min_fvg_points=fvg,
        max_ob_age_bars=age,
        min_wick_ratio=wick,
        min_sl_points=sl,
        tp1_mode=tp_mode,
        tp1_fixed_rr=r1,
        tp2_fixed_rr=r2,
        enable_trend_filter=tf,
        allowed_hours=hrs
    )
    
    # 僅重新檢測 5K OB（毫秒級）並複用 prepared_df
    _, obs = OrderBlockTracker.detect_5k_order_blocks(df_5k, cfg)
    
    engine = OrderBlockEngine(df_1k, df_5k, cfg)
    engine.df = prepared_df.copy()
    engine.order_blocks = obs
    
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

    results.append({
        'name': name,
        'hrs': str(hrs),
        'fvg': fvg,
        'sl': sl,
        'age': age,
        'wick': wick,
        'tp_mode': tp_mode,
        'r1': r1,
        'r2': r2,
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
df_res.to_csv("scratch/ob_2lots_candidates_ranked.csv", index=False)

print("=" * 110)
print(f"{'2 口限制下 SMC 訂單塊最佳策略回測評比排行 (2024-2026 全歷史)':^100}")
print("=" * 110)

# 依 Profit Factor 排序
df_res = df_res.sort_values(by='pf', ascending=False)
for idx, r in df_res.iterrows():
    pos_mark = "[三年皆正]" if r['all_pos'] else "[某年為負]"
    print(f"【{r['name']}】 ({pos_mark})")
    print(f"   總淨利: NT$ {r['tot_pnl']:>+9,.0f} | 獲利因子(PF): {r['pf']:>4.2f} | 最大回撤(MDD): {r['mdd']:>4.1f}% | 勝率: {r['wr']:>4.1f}% | 交易數: {r['trades']} 筆")
    print(f"   年度分佈 -> 2024: NT$ {r['pnl_24']:>+8,.0f} | 2025: NT$ {r['pnl_25']:>+8,.0f} | 2026: NT$ {r['pnl_26']:>+8,.0f}")
    print("-" * 110)
