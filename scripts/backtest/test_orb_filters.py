# -*- coding: utf-8 -*-
"""
單元測試腳本: test_orb_filters.py
驗證 ORBFilterPipeline 及其 4 大過濾器的判斷邏輯與極限邊界。
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

from app.strategy.orb_filters import (
    FilterConfig,
    FilterResult,
    VolumeSpikeFilter,
    MomentumFilter,
    VWAPFilter,
    OverExtensionFilter,
    ORBFilterPipeline
)


def test_volume_spike_filter():
    print("========== 測試 1: VolumeSpikeFilter (成交量突波) ==========")
    config = FilterConfig(enable_volume_spike=True, vol_spike_ratio=1.5)
    f = VolumeSpikeFilter(config)

    # 1. 達標案例 (300 / 150 = 2.0x >= 1.5x)
    ctx_pass = {"current_volume": 300, "vol_ma": 150.0}
    passed, reason, detail = f.check(ctx_pass)
    assert passed, f"應通過但拒絕: {reason}"
    assert detail["vol_ratio"] == 2.0
    print(" [PASS] 爆量通過測試 (2.0x >= 1.5x)")

    # 2. 未達標案例 (180 / 150 = 1.2x < 1.5x)
    ctx_fail = {"current_volume": 180, "vol_ma": 150.0}
    passed, reason, detail = f.check(ctx_fail)
    assert not passed, "應該拒絕但誤放行"
    assert "VolumeSpike" in reason
    print(f" [PASS] 無量拒絕測試: {reason}")


def test_momentum_filter():
    print("\n========== 測試 2: MomentumFilter (動能門檻) ==========")
    config = FilterConfig(enable_momentum=True, mom_day_pct=0.0005, mom_night_pct=0.0003)
    f = MomentumFilter(config)

    # 1. 日盤達標案例 (20015 - 20000) / 20000 = 0.075% >= 0.05%
    ctx_day_pass = {"bar_open": 20000.0, "bar_close": 20015.0, "direction": "LONG", "session": "day"}
    passed, reason, _ = f.check(ctx_day_pass)
    assert passed, f"日盤應通過但拒絕: {reason}"
    print(" [PASS] 日盤動能通過測試 (0.075% >= 0.05%)")

    # 2. 日盤未達標案例 (20005 - 20000) / 20000 = 0.025% < 0.05%
    ctx_day_fail = {"bar_open": 20000.0, "bar_close": 20005.0, "direction": "LONG", "session": "day"}
    passed, reason, _ = f.check(ctx_day_fail)
    assert not passed, "日盤動能不足應拒絕"
    print(f" [PASS] 日盤動能不足拒絕測試: {reason}")

    # 3. 夜盤達標案例 (20008 - 20000) / 20000 = 0.04% >= 0.03%
    ctx_night_pass = {"bar_open": 20000.0, "bar_close": 20008.0, "direction": "LONG", "session": "night"}
    passed, reason, _ = f.check(ctx_night_pass)
    assert passed, f"夜盤應通過但拒絕: {reason}"
    print(" [PASS] 夜盤動能通過測試 (0.04% >= 0.03%)")


def test_vwap_filter():
    print("\n========== 測試 3: VWAPFilter (均價線過濾) ==========")
    config = FilterConfig(enable_vwap=True)
    f = VWAPFilter(config)

    # 1. 多單站在 VWAP 之上 (20050 >= 20000) -> Pass
    ctx_long_pass = {"current_price": 20050.0, "vwap": 20000.0, "direction": "LONG"}
    passed, reason, _ = f.check(ctx_long_pass)
    assert passed, f"多單 VWAP 上應通過但拒絕: {reason}"
    print(" [PASS] 多單高於 VWAP 通過測試")

    # 2. 多單在 VWAP 之下 (19950 < 20000) -> Fail
    ctx_long_fail = {"current_price": 19950.0, "vwap": 20000.0, "direction": "LONG"}
    passed, reason, _ = f.check(ctx_long_fail)
    assert not passed, "多單低於 VWAP 應拒絕"
    print(f" [PASS] 多單逆勢低於 VWAP 拒絕測試: {reason}")

    # 3. 空單在 VWAP 之下 (19950 <= 20000) -> Pass
    ctx_short_pass = {"current_price": 19950.0, "vwap": 20000.0, "direction": "SHORT"}
    passed, reason, _ = f.check(ctx_short_pass)
    assert passed, f"空單 VWAP 下應通過但拒絕: {reason}"
    print(" [PASS] 空單低於 VWAP 通過測試")

    # 4. 空單在 VWAP 之上 (20050 > 20000) -> Fail
    ctx_short_fail = {"current_price": 20050.0, "vwap": 20000.0, "direction": "SHORT"}
    passed, reason, _ = f.check(ctx_short_fail)
    assert not passed, "空單高於 VWAP 應拒絕"
    print(f" [PASS] 空單逆勢高於 VWAP 拒絕測試: {reason}")


def test_over_extension_filter():
    print("\n========== 測試 4: OverExtensionFilter (過度延伸保護) ==========")
    config = FilterConfig(enable_over_extension=True, over_ext_atr_mult=2.0, over_ext_orb_mult=1.5)
    f = OverExtensionFilter(config)

    # 正常小幅突破 (Price = 20040, ORB_High = 20030, ORB_Low = 19980 [Range=50], ATR = 15.0)
    # Distance = 10, Limit_ATR = 30, Limit_ORB = 75 -> Pass
    ctx_normal = {
        "current_price": 20040.0,
        "direction": "LONG",
        "orb_high": 20030.0,
        "orb_low": 19980.0,
        "atr": 15.0
    }
    passed, reason, _ = f.check(ctx_normal)
    assert passed, f"正常突破應通過但拒絕: {reason}"
    print(" [PASS] 正常範圍突破通過測試")

    # 爆發過度延伸 (Price = 20075, Distance = 45 > Limit_ATR 30) -> Fail
    ctx_over_atr = {
        "current_price": 20075.0,
        "direction": "LONG",
        "orb_high": 20030.0,
        "orb_low": 19980.0,
        "atr": 15.0
    }
    passed, reason, _ = f.check(ctx_over_atr)
    assert not passed, "超過 ATR 門檻應拒絕"
    print(f" [PASS] 超過 ATR 門檻過度延伸拒絕測試: {reason}")


def test_pipeline_integration():
    print("\n========== 測試 5: ORBFilterPipeline 完整管道整合 ==========")
    config = FilterConfig()
    pipeline = ORBFilterPipeline(config)

    # 1. 完整全通過情境
    ctx_perfect = {
        "direction": "LONG",
        "current_price": 20030.0,
        "bar_open": 20000.0,
        "bar_close": 20030.0,
        "current_volume": 400,
        "vol_ma": 200.0,
        "vwap": 20010.0,
        "orb_high": 20025.0,
        "orb_low": 19980.0,
        "atr": 15.0,
        "session": "day"
    }
    res = pipeline.filter(ctx_perfect)
    assert res.passed, f"完美條件應全過但被阻擋: {res.reasons}"
    assert len(res.reasons) == 0
    print(" [PASS] 管道全過測試成功")

    # 2. 多重違規情境 (成交量不足 + 逆勢低於 VWAP)
    ctx_multi_fail = {
        "direction": "LONG",
        "current_price": 20005.0,
        "bar_open": 20000.0,
        "bar_close": 20005.0,
        "current_volume": 100,  # 100 / 200 = 0.5x < 1.5x
        "vol_ma": 200.0,
        "vwap": 20010.0,        # 20005 < 20010 (VWAP 逆勢)
        "orb_high": 20000.0,
        "orb_low": 19950.0,
        "atr": 15.0,
        "session": "day"
    }
    res_fail = pipeline.filter(ctx_multi_fail)
    assert not res_fail.passed, "多重違規應阻擋"
    assert len(res_fail.reasons) >= 2, f"應記錄至少 2 個拒絕原因: {res_fail.reasons}"
    print(f" [PASS] 管道多重拒絕測試成功，阻擋原因: {res_fail.reasons}")


if __name__ == "__main__":
    test_volume_spike_filter()
    test_momentum_filter()
    test_vwap_filter()
    test_over_extension_filter()
    test_pipeline_integration()
    print("\n[SUCCESS] 所有 ORBFilterPipeline 單元測試成功通過！")
