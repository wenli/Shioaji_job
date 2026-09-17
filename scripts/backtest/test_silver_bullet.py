# -*- coding: utf-8 -*-
"""
SMC 台指銀色子彈 (Silver Bullet) 策略單元測試套件
測試項目：
1. 美國夏冬令時 (DST) 與台指期 Killzone 自動辨識
2. SMCFilterPipeline 多維度過濾 (VWAP + Volume Spike)
3. 50% CE 限價單回測撮合與超時撤單
4. 1R 動態移保本 (Breakeven, BE) 機制
"""

import os
import sys
from datetime import datetime, time
import pandas as pd
import numpy as np

# 加入根目錄至 sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.strategy.silver_bullet import (
    SilverBulletConfig,
    SilverBulletEngine,
    USDaylightSavingDetector,
    SMCFilterPipeline,
    SMCVWAPFilter,
    SMCVolumeSpikeFilter,
    PendingOrder
)


def test_us_dst_and_killzones():
    print("========== 測試 1: 美國夏冬令時與 Killzone 檢測 ==========")
    # 2026 年夏季 (7 月) 為夏令時 EDT
    summer_dt = datetime(2026, 7, 15, 21, 45)
    is_dst_summer = USDaylightSavingDetector.is_daylight_saving(summer_dt)
    assert is_dst_summer is True, f"預期 7 月為夏令時，但回傳: {is_dst_summer}"
    print("[PASS] 7 月夏令時間檢測成功")

    # 2026 年冬季 (12 月) 為冬令時 EST
    winter_dt = datetime(2026, 12, 15, 22, 45)
    is_dst_winter = USDaylightSavingDetector.is_daylight_saving(winter_dt)
    assert is_dst_winter is False, f"預期 12 月為冬令時，但回傳: {is_dst_winter}"
    print("[PASS] 12 月冬令時間檢測成功")

    # 檢驗 Killzone 判定
    dummy_1k = pd.DataFrame({'datetime': [pd.Timestamp(summer_dt)]})
    dummy_5k = pd.DataFrame({'datetime': [pd.Timestamp(summer_dt)]})
    engine = SilverBulletEngine(dummy_1k, dummy_5k)

    # 日盤 09:15 應在日盤 Killzone
    day_ts = pd.Timestamp(datetime(2026, 7, 15, 9, 15))
    in_kz, tag = engine.is_in_killzone(day_ts)
    assert in_kz is True and tag == "day_killzone", f"日盤 09:15 判定失敗: {in_kz}, {tag}"
    print("[PASS] 日盤 09:15 Killzone 判定成功")

    # 夏令夜盤 21:45 應在夏令 Killzone
    night_summer_ts = pd.Timestamp(datetime(2026, 7, 15, 21, 45))
    in_kz, tag = engine.is_in_killzone(night_summer_ts)
    assert in_kz is True and tag == "night_dst_killzone", f"夏令夜盤 21:45 判定失敗: {in_kz}, {tag}"
    print("[PASS] 夏令夜盤 21:45 Killzone 判定成功")

    # 冬令夜盤 22:45 應在冬令 Killzone
    night_winter_ts = pd.Timestamp(datetime(2026, 12, 15, 22, 45))
    in_kz, tag = engine.is_in_killzone(night_winter_ts)
    assert in_kz is True and tag == "night_std_killzone", f"冬令夜盤 22:45 判定失敗: {in_kz}, {tag}"
    print("[PASS] 冬令夜盤 22:45 Killzone 判定成功")

    # 非時段 (14:30) 應判定為非 Killzone
    off_ts = pd.Timestamp(datetime(2026, 7, 15, 14, 30))
    in_kz, tag = engine.is_in_killzone(off_ts)
    assert in_kz is False, f"非時段 14:30 誤判為 Killzone: {in_kz}, {tag}"
    print("[PASS] 盤外時間 14:30 阻擋成功")


def test_smc_filter_pipeline():
    print("\n========== 測試 2: SMCFilterPipeline 多維度過濾 ==========")
    cfg = SilverBulletConfig(
        enable_vwap=True,
        enable_volume_spike=True,
        vol_spike_ratio=1.3
    )
    pipeline = SMCFilterPipeline(cfg)

    # 1. 多單順勢且量能充足 (應通過)
    ctx_pass = {
        "current_price": 20050.0,
        "vwap": 20000.0,
        "side": 1,
        "current_volume": 1500,
        "vol_ma": 1000.0
    }
    res = pipeline.evaluate(ctx_pass)
    assert res.passed is True, f"預期通過但被拒絕: {res.reasons}"
    print("[PASS] 多單高於 VWAP 且量能 1.5x 通過驗證")

    # 2. 多單逆勢 (價格低於 VWAP，應被 VWAP 拒絕)
    ctx_vwap_reject = {
        "current_price": 19950.0,
        "vwap": 20000.0,
        "side": 1,
        "current_volume": 1500,
        "vol_ma": 1000.0
    }
    res = pipeline.evaluate(ctx_vwap_reject)
    assert res.passed is False and any("VWAP" in r for r in res.reasons), f"預期 VWAP 攔截逆勢多單，結果: {res.reasons}"
    print("[PASS] VWAP 成功攔截價格低於均價線之多單")

    # 3. 空單順勢但量能不足 (應被 VolumeSpike 拒絕)
    ctx_vol_reject = {
        "current_price": 19900.0,
        "vwap": 20000.0,
        "side": -1,
        "current_volume": 1100,
        "vol_ma": 1000.0  # 1.1x < 1.3x 門檻
    }
    res = pipeline.evaluate(ctx_vol_reject)
    assert res.passed is False and any("VolumeSpike" in r for r in res.reasons), f"預期量能突波攔截，結果: {res.reasons}"
    print("[PASS] VolumeSpike 成功攔截無量位移空單")


def test_limit_retest_and_breakeven():
    print("\n========== 測試 3: 50% CE 限價單撮合與 1R 動態保本 ==========")
    # 構造模擬序列：
    # 基準時間：2026-07-15 09:00 日盤 Killzone
    # 基準時間：2026-07-15 08:45 日盤開盤
    base_time = pd.Timestamp("2026-07-15 08:45:00")
    n_1k = 80
    timestamps = [base_time + pd.Timedelta(minutes=k) for k in range(n_1k)]

    opens = [20000.0] * n_1k
    highs = [20010.0] * n_1k
    lows = [19990.0] * n_1k
    closes = [20000.0] * n_1k
    volumes = [1000] * n_1k

    # 1K: 第 20 根製造局部低點 (19950)
    lows[20] = 19950.0
    highs[20] = 19965.0
    closes[20] = 19960.0
    opens[20] = 19962.0

    # 1K: 第 35 根 (09:20，進入 Killzone) 發生 1K Sweep Low
    lows[35] = 19940.0   # 跌破 19950
    highs[35] = 20005.0
    closes[35] = 19995.0  # 收上 19990 -> 1K Sweep Low 成立

    # 1K: 第 36 根長陽位移形成 FVG: high[34]=19970, low[36]=19990 -> FVG=[19970, 19990], 50% CE = 19980
    highs[34] = 19970.0
    opens[36] = 19995.0
    lows[36] = 19990.0
    highs[36] = 20020.0
    closes[36] = 20015.0
    volumes[36] = 2500

    # 1K: 第 37 根未回踩 19980 (low = 20000)
    lows[37] = 20000.0
    highs[37] = 20030.0

    # 1K: 第 38 根精準回踩 19980 (low = 19975, high = 20010) -> 50% CE 限價單成交！
    lows[38] = 19975.0
    highs[38] = 20010.0

    # 1K: 第 39 根衝高達 +1R (risk = 19980 - (19940 - 3) = 43 點，目標 19980 + 43 = 20023)
    # 讓 high[39] = 20050 -> 觸發 1R 移保本
    highs[39] = 20050.0
    lows[39] = 19990.0

    # 1K: 第 40 根回落跌破進場價 19980 (low = 19960) -> 應觸發 BE 保本離場 (exit_price = 19980)
    lows[40] = 19960.0
    closes[40] = 19970.0

    df_1k = pd.DataFrame({
        'datetime': timestamps,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes
    })

    # 模擬 5K 數據 (在大週期形成 5K Sweep Low 確立共振)
    n_5k = 30
    timestamps_5k = [base_time + pd.Timedelta(minutes=5 * k) for k in range(n_5k)]
    df_5k = pd.DataFrame({
        'datetime': timestamps_5k,
        'open': [20000.0] * n_5k,
        'high': [20010.0] * n_5k,
        'low': [19990.0] * n_5k,
        'close': [20000.0] * n_5k,
        'volume': [5000] * n_5k
    })
    # 在 5K 數據第 2 根設波段低點，第 6 根設 Sweep Low
    df_5k.loc[2, 'low'] = 19950.0
    df_5k.loc[6, 'low'] = 19940.0
    df_5k.loc[6, 'close'] = 20005.0

    cfg = SilverBulletConfig(
        pivot_window=2,
        entry_mode="limit_retest",
        fvg_entry_level="ce_50",
        max_wait_bars=5,
        enable_breakeven=True,
        be_trigger_r=1.0,
        enable_vwap=False,
        enable_volume_spike=False
    )

    engine = SilverBulletEngine(df_1k, df_5k, cfg)
    summary, trades, eq_curve = engine.run_backtest()

    assert len(trades) >= 1, f"預期至少產生 1 筆交易，但為 {len(trades)}"
    first_trade = trades[0]
    print(f"[DEBUG] 交易明細: 進場價={first_trade['entry_price']}, 出場價={first_trade['exit_price']}, 離場原因={first_trade['exit_reason']}")
    assert first_trade['entry_price'] == 19980.0, f"預期 50% CE 進場價 19980.0，實際: {first_trade['entry_price']}"
    assert "BE" in first_trade['exit_reason'], f"預期觸發 1R 保本離場，實際原因: {first_trade['exit_reason']}"
    print("[PASS] 50% CE 限價單成功於回測時撮合成交，並在衝高後成功移保本離場")


if __name__ == "__main__":
    print("==================================================")
    print("開始執行 SMC 台指銀色子彈 (Silver Bullet) 單元測試")
    print("==================================================")
    test_us_dst_and_killzones()
    test_smc_filter_pipeline()
    test_limit_retest_and_breakeven()
    print("\n[SUCCESS] 所有 SMC 銀色子彈單元測試 100% 通過！\n")
