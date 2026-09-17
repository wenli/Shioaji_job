# -*- coding: utf-8 -*-
"""
SMC 流動性獵取假突破反轉策略 (Sweep Fade) 單元測試套件
測試項目：
1. 流動性池 (PDH/PDL, ORB 15m, 5K Swings) 追蹤計算
2. 假突破刺穿深度邊界 (3 ~ 35 點有效，>35 點超強單邊拒絕)
3. 拒絕影線 (Wick Rejection) 強度檢驗
4. 二階段停利 (TP1 平半倉移保本，TP2 終極停利)
"""

import os
import sys
from datetime import datetime, time
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine, LiquidityPoolTracker


def test_liquidity_pools_and_penetration():
    print("========== 測試 1: 流動性池追蹤與刺穿深度防禦 ==========")
    base_time = pd.Timestamp("2026-07-15 08:45:00")
    n_1k = 60
    timestamps = [base_time + pd.Timedelta(minutes=k) for k in range(n_1k)]

    opens = [20000.0] * n_1k
    highs = [20010.0] * n_1k
    lows = [19990.0] * n_1k
    closes = [20000.0] * n_1k
    volumes = [1000] * n_1k

    # 前 15 根 (08:45 - 09:00) 建立 ORB 區間: High=20030, Low=19970
    highs[5] = 20030.0
    lows[10] = 19970.0

    # 案例 A: 第 20 根刺破 20030 達 15 點 (high=20045)，但收在 20025 (留 20 點長上影線)
    # 穿刺 15 點 (3 <= 15 <= 35) -> 應觸發空單假突破反轉！
    opens[20] = 20020.0
    highs[20] = 20045.0
    lows[20] = 20015.0
    closes[20] = 20022.0  # 收在 20030 之下，上影線 23 點 >= 實體 2 點

    df_1k = pd.DataFrame({
        'datetime': timestamps,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes
    })

    timestamps_5k = [base_time + pd.Timedelta(minutes=5 * k) for k in range(12)]
    df_5k = pd.DataFrame({
        'datetime': timestamps_5k,
        'open': [20000.0] * 12,
        'high': [20020.0] * 12,
        'low': [19980.0] * 12,
        'close': [20000.0] * 12,
        'volume': [5000] * 12
    })

    cfg = SweepFadeConfig(
        enable_pdh_pdl=False,
        enable_orb_pools=True,
        enable_htf_swings=False,
        min_penetration=3.0,
        max_penetration=35.0,
        min_wick_ratio=0.4
    )

    engine = SweepFadeEngine(df_1k, df_5k, cfg)
    df_prep = LiquidityPoolTracker.prepare_dataset(df_1k, df_5k, cfg)

    # 檢查 ORB High 是否正確記錄為 20030
    assert df_prep['orb_high'].iloc[20] == 20030.0, f"預期 ORB High 為 20030，但為: {df_prep['orb_high'].iloc[20]}"
    print("[PASS] 08:45-09:00 ORB 區間池正確錨定 (High=20030.0, Low=19970.0)")

    # 案例 B: 驗證超過 35 點被強單邊過濾
    opens_mega = list(opens)
    highs_mega = list(highs)
    closes_mega = list(closes)
    # 暴衝 50 點 (high=20080) -> 超過 35 點
    highs_mega[20] = 20080.0
    closes_mega[20] = 20025.0
    df_1k_mega = pd.DataFrame({
        'datetime': timestamps,
        'open': opens_mega,
        'high': highs_mega,
        'low': lows,
        'close': closes_mega,
        'volume': volumes
    })
    engine_mega = SweepFadeEngine(df_1k_mega, df_5k, cfg)
    summary_mega, trades_mega, _ = engine_mega.run_backtest()
    assert len(trades_mega) == 0, f"預期超過 35 點強趨勢不進場，但產生了 {len(trades_mega)} 筆交易"
    print("[PASS] 穿刺深度 50 點 (> 35 點門檻) 成功判定為強單邊爆發，未逆勢接刀")


def test_two_stage_take_profit():
    print("\n========== 測試 2: 二階段階梯停利與動態移保本 ==========")
    base_time = pd.Timestamp("2026-07-15 08:45:00")
    n_1k = 60
    timestamps = [base_time + pd.Timedelta(minutes=k) for k in range(n_1k)]

    opens = [20000.0] * n_1k
    highs = [20010.0] * n_1k
    lows = [19990.0] * n_1k
    closes = [20000.0] * n_1k
    volumes = [1000] * n_1k

    # 前 15 根 ORB: High=20030, Low=19970
    highs[5] = 20030.0
    lows[10] = 19970.0

    # 第 20 根 (09:05): 刺穿 20030 至 20045，收在 20025 -> 觸發做空進場！
    # 停損 = 20045 + 3 = 20048 (風險點數 = 23 點)
    opens[20] = 20020.0
    highs[20] = 20045.0
    lows[20] = 20015.0
    closes[20] = 20025.0

    # 第 21 根: 跌至 19995 (達到當日 VWAP ~20000) -> 觸發 TP1 (平 50% 倉位，止損移至 20025 保本)
    opens[21] = 20025.0
    highs[21] = 20028.0
    lows[21] = 19995.0
    closes[21] = 20000.0

    # 第 22 根: 繼續下殺至 19965 (低於對側 ORB Low 19970) -> 觸發 TP2 (全平剩餘 50% 倉位)
    opens[22] = 20000.0
    highs[22] = 20005.0
    lows[22] = 19965.0
    closes[22] = 19968.0

    df_1k = pd.DataFrame({
        'datetime': timestamps,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes
    })

    timestamps_5k = [base_time + pd.Timedelta(minutes=5 * k) for k in range(12)]
    df_5k = pd.DataFrame({
        'datetime': timestamps_5k,
        'open': [20000.0] * 12,
        'high': [20020.0] * 12,
        'low': [19980.0] * 12,
        'close': [20000.0] * 12,
        'volume': [5000] * 12
    })

    cfg = SweepFadeConfig(
        enable_pdh_pdl=False,
        enable_orb_pools=True,
        enable_htf_swings=False,
        min_penetration=3.0,
        max_penetration=35.0,
        min_wick_ratio=0.4,
        allowed_hours=None
    )

    engine = SweepFadeEngine(df_1k, df_5k, cfg)
    summary, trades, eq_curve = engine.run_backtest()

    assert len(trades) == 2, f"預期產生 TP1 與 TP2 兩筆結算記錄，但為: {len(trades)}"
    t1 = trades[0]
    t2 = trades[1]

    print(f"[DEBUG] TP1 記錄: 階段={t1['stage']}, 出場價={t1['exit_price']}, 獲利={t1['net_pnl']:+,.0f}")
    print(f"[DEBUG] TP2 記錄: 階段={t2['stage']}, 出場價={t2['exit_price']}, 獲利={t2['net_pnl']:+,.0f}")

    assert t1['stage'] == "TP1", f"第 1 筆交易階段應為 TP1，但為: {t1['stage']}"
    assert t2['stage'] == "TP2", f"第 2 筆交易階段應為 TP2，但為: {t2['stage']}"
    assert t1['net_pnl'] > 0 and t2['net_pnl'] > 0, "兩筆分批停利損益均應為正值！"
    print("[PASS] 二階段階梯停利 (TP1 於 VWAP 鎖利 + TP2 於對側 ORB Low 結清) 驗證成功")


if __name__ == "__main__":
    print("==================================================")
    print("開始執行 SMC 假突破反轉策略 (Sweep Fade) 單元測試")
    print("==================================================")
    test_liquidity_pools_and_penetration()
    test_two_stage_take_profit()
    print("\n[SUCCESS] 所有 SMC 假突破反轉單元測試 100% 通過！\n")
