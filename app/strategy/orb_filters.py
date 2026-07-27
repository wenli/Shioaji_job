# -*- coding: utf-8 -*-
"""
ORB 突破過濾器管道 (ORB Filter Pipeline) 模組
包含 4 大過濾機制：
1. VolumeSpikeFilter (成交量突波)
2. MomentumFilter (動能門檻)
3. VWAPFilter (VWAP 均價線)
4. OverExtensionFilter (過度延伸保護)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple, Optional
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


@dataclass
class FilterConfig:
    """過濾器參數與開關配置"""
    # 1. 成交量突波過濾器
    enable_volume_spike: bool = True
    vol_spike_ratio: float = 1.5
    vol_ma_period: int = 20

    # 2. 動能門檻過濾器
    enable_momentum: bool = True
    mom_day_pct: float = 0.0005    # 0.05% (日盤)
    mom_night_pct: float = 0.0003  # 0.03% (夜盤)

    # 3. VWAP 均價線過濾器
    enable_vwap: bool = True

    # 4. 過度延伸保護過濾器
    enable_over_extension: bool = True
    over_ext_atr_mult: float = 2.0
    over_ext_orb_mult: float = 1.5


@dataclass
class FilterResult:
    """過濾結果紀錄"""
    passed: bool
    reasons: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


class BaseFilter:
    """過濾器抽象基類"""
    def __init__(self, config: FilterConfig):
        self.config = config

    def check(self, context: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """
        執行單一過濾器判斷。
        回傳: (is_passed: bool, reject_reason: Optional[str], detail_dict: dict)
        """
        raise NotImplementedError


class VolumeSpikeFilter(BaseFilter):
    """
    1. 成交量突波過濾器 (Volume Spike Filter)
    突破當下的成交量必須大於過去 N 筆 (預設 20 筆) 平均成交量的 1.5 倍。
    """
    def check(self, context: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        if not self.config.enable_volume_spike:
            return True, None, {}

        current_volume = context.get("current_volume", 0)
        vol_ma = context.get("vol_ma", 0.0)

        # 防禦降級：若量能數據不存在或前幾筆數據尚無法計算 MA
        if vol_ma <= 0:
            return True, None, {"vol_ratio": 0.0, "status": "skipped_insufficient_data"}

        vol_ratio = current_volume / vol_ma
        required_ratio = self.config.vol_spike_ratio

        detail = {"current_volume": current_volume, "vol_ma": vol_ma, "vol_ratio": vol_ratio}

        if vol_ratio >= required_ratio:
            return True, None, detail
        else:
            reason = f"VolumeSpike: 量比不足 ({vol_ratio:.2f}x < 門檻 {required_ratio:.2f}x)"
            return False, reason, detail


class MomentumFilter(BaseFilter):
    """
    2. 動能門檻過濾器 (Momentum Filter)
    突破當前 1K K線的價格變化率 (Close - Open) / Open 必須達標（日盤 0.05%，夜盤 0.03%）。
    """
    def check(self, context: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        if not self.config.enable_momentum:
            return True, None, {}

        open_price = context.get("bar_open", 0.0)
        close_price = context.get("bar_close", context.get("current_price", 0.0))
        direction = context.get("direction", "LONG").upper()
        session = context.get("session", "day").lower()

        if open_price <= 0:
            return True, None, {"status": "skipped_invalid_price"}

        # 計算變化率 (多頭為正變幅，空頭為負變幅的絕對值)
        raw_pct = (close_price - open_price) / open_price
        direction_pct = raw_pct if direction == "LONG" else -raw_pct

        target_pct = self.config.mom_day_pct if session == "day" else self.config.mom_night_pct
        detail = {"bar_open": open_price, "bar_close": close_price, "mom_pct": direction_pct, "target_pct": target_pct}

        if direction_pct >= target_pct:
            return True, None, detail
        else:
            reason = f"Momentum: 動能不足 ({direction_pct*100:.3f}% < 門檻 {target_pct*100:.3f}%)"
            return False, reason, detail


class VWAPFilter(BaseFilter):
    """
    3. VWAP 均價線過濾器 (VWAP Filter)
    做多時，價格必須在 VWAP 之上 (Close >= VWAP)；
    做空時，價格必須在 VWAP 之下 (Close <= VWAP)。
    """
    def check(self, context: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        if not self.config.enable_vwap:
            return True, None, {}

        price = context.get("current_price", context.get("bar_close", 0.0))
        vwap = context.get("vwap", 0.0)
        direction = context.get("direction", "LONG").upper()

        if vwap <= 0 or price <= 0:
            return True, None, {"status": "skipped_no_vwap"}

        detail = {"current_price": price, "vwap": vwap, "direction": direction}

        if direction == "LONG":
            if price >= vwap:
                return True, None, detail
            else:
                reason = f"VWAP: 逆勢接刀多單 (價格 {price:.1f} < VWAP {vwap:.1f})"
                return False, reason, detail
        else:  # SHORT
            if price <= vwap:
                return True, None, detail
            else:
                reason = f"VWAP: 逆勢接刀空單 (價格 {price:.1f} > VWAP {vwap:.1f})"
                return False, reason, detail


class OverExtensionFilter(BaseFilter):
    """
    4. 過度延伸保護過濾器 (Over-Extension Protection Filter)
    如果突破價格瞬間噴出太快，距離 ORB 區間邊界超過 2.0 倍的 ATR 或 1.5 倍 ORB 區間寬度，
    系統會判斷「乖離過大」而放棄追價，避免買在最高點或賣在最低點。
    採用 OR 嚴格邏輯（只要超過任一門檻即觸發保護）。
    """
    def check(self, context: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        if not self.config.enable_over_extension:
            return True, None, {}

        price = context.get("current_price", context.get("bar_close", 0.0))
        direction = context.get("direction", "LONG").upper()
        orb_high = context.get("orb_high", 0.0)
        orb_low = context.get("orb_low", 0.0)
        atr = context.get("atr", 0.0)

        if orb_high <= 0 or orb_low <= 0 or price <= 0:
            return True, None, {"status": "skipped_no_orb_bounds"}

        orb_range = max(orb_high - orb_low, 1.0)

        # 計算突破距離（相對於 ORB 區間邊界）
        if direction == "LONG":
            dist = max(price - orb_high, 0.0)
        else:  # SHORT
            dist = max(orb_low - price, 0.0)

        limit_atr = self.config.over_ext_atr_mult * atr if atr > 0 else float("inf")
        limit_orb = self.config.over_ext_orb_mult * orb_range

        detail = {
            "distance": dist,
            "atr": atr,
            "limit_atr": limit_atr,
            "orb_range": orb_range,
            "limit_orb": limit_orb
        }

        # 檢測是否超過 2.0x ATR
        if atr > 0 and dist > limit_atr:
            reason = f"OverExtension: 距離超越 ATR 門檻 (延伸 {dist:.1f} > 2.0x ATR {limit_atr:.1f})"
            return False, reason, detail

        # 檢測是否超過 1.5x ORB Range
        if dist > limit_orb:
            reason = f"OverExtension: 距離超越 ORB 寬度門檻 (延伸 {dist:.1f} > 1.5x ORB寬度 {limit_orb:.1f})"
            return False, reason, detail

        return True, None, detail


class ORBFilterPipeline:
    """
    ORB 突破過濾器管道
    負責載入所有啟用的 Filter，並在訊號觸發時依序進行檢測。
    """
    def __init__(self, config: Optional[FilterConfig] = None):
        self.config = config or FilterConfig()
        self.filters: List[BaseFilter] = [
            VolumeSpikeFilter(self.config),
            MomentumFilter(self.config),
            VWAPFilter(self.config),
            OverExtensionFilter(self.config),
        ]

    def filter(self, context: Dict[str, Any]) -> FilterResult:
        """
        對當前 ORB 突破訊號執行完整管道過濾。
        AND 邏輯：所有啟用的過濾器皆必須放行。
        """
        all_passed = True
        reasons: List[str] = []
        details: Dict[str, Any] = {}

        for f in self.filters:
            try:
                passed, reason, detail = f.check(context)
                filter_name = f.__class__.__name__
                details[filter_name] = detail

                if not passed:
                    all_passed = False
                    if reason:
                        reasons.append(reason)
            except Exception as e:
                logger.exception(f"Filter {f.__class__.__name__} 執行異常: {e}")
                # 安全降級：異常不阻斷，但記錄 Log

        return FilterResult(passed=all_passed, reasons=reasons, details=details)
