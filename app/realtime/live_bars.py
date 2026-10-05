"""
即時 1K K 棒建構與伺服器對帳的純邏輯 (無 Shioaji / FastAPI 相依，便於單元測試)。

慣例 (與 Shioaji kbars / futures1k 資料表一致):
    1K K 棒以「結束時間」標記。例如 08:45:00 ~ 08:45:59 的 Tick 歸屬於 08:46:00 這根 K 棒。
    日盤第一根為 08:46、最後一根為 13:45；夜盤第一根為 15:01、最後一根為 05:00。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Callable, Optional

import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "volume", "datetime", "session"]

DAY_OPEN = time(8, 45)
DAY_CLOSE = time(13, 45)
NIGHT_OPEN = time(15, 0)
NIGHT_CLOSE = time(5, 0)


def bar_label(dt: datetime) -> pd.Timestamp:
    """Tick 時間 -> 所屬 1K K 棒的結束時間標籤 (floor(minute) + 1min)。"""
    ts = pd.Timestamp(dt)
    return ts.floor("min") + pd.Timedelta(minutes=1)


def is_trading_time(dt: datetime) -> bool:
    """台指期一般/夜盤交易時段判斷 (不含國定假日)。

    日盤 08:45~13:45 (週一~週五)、夜盤 15:00~隔日 05:00 (週一~週五開盤，跨至週二~週六清晨)。
    """
    t = dt.time()
    wd = dt.weekday()  # Mon=0 ... Sun=6
    if DAY_OPEN <= t <= DAY_CLOSE:
        return wd <= 4
    if t >= NIGHT_OPEN:
        return wd <= 4
    if t <= NIGHT_CLOSE:
        # 清晨屬於前一個交易日的夜盤：週二~週六清晨
        return 1 <= wd <= 5
    return False


def is_simtrade(tick) -> bool:
    """是否為開盤前試撮 Tick (TickFOPv1.simtrade)。"""
    return bool(getattr(tick, "simtrade", False))


def default_session(dt) -> str:
    t = pd.Timestamp(dt).time()
    return "day" if DAY_OPEN <= t <= DAY_CLOSE else "night"


@dataclass
class TickResult:
    """apply_tick 的結果。

    status:
        "updated"      更新目前形成中的 K 棒
        "new_bar"      開立新的 K 棒
        "late_updated" 遲到 Tick，更新記憶體中較早的 K 棒
        "late_dropped" 遲到 Tick，對應 K 棒已不在記憶體中 (交由對帳修正)
    gap_minutes: 新 K 棒與上一根之間跳過的分鐘數 (僅 new_bar 時有意義)
    """
    status: str
    label: pd.Timestamp
    gap_minutes: int = 0


def apply_tick(
    df: pd.DataFrame,
    tick_dt: datetime,
    price: float,
    volume: int,
    session_fn: Callable = default_session,
    max_rows: int = 3000,
    keep_rows: int = 2500,
) -> tuple[pd.DataFrame, TickResult]:
    """將單筆 Tick 套用至 1K DataFrame (以結束時間標記)。回傳 (新 df, 結果)。

    df 需含 BAR_COLUMNS，且依 datetime 遞增排序。
    """
    label = bar_label(tick_dt)

    if df.empty:
        new_df = pd.DataFrame([_new_bar(label, price, volume, session_fn)], columns=BAR_COLUMNS)
        return new_df, TickResult("new_bar", label, 0)

    last_idx = df.index[-1]
    last_label = pd.Timestamp(df.at[last_idx, "datetime"])

    if label == last_label:
        _update_bar(df, last_idx, price, volume, update_close=True)
        return df, TickResult("updated", label)

    if label > last_label:
        gap = int((label - last_label).total_seconds() // 60) - 1
        new_row = pd.DataFrame([_new_bar(label, price, volume, session_fn)], columns=BAR_COLUMNS)
        df = pd.concat([df, new_row], ignore_index=True)
        if len(df) > max_rows:
            df = df.iloc[-keep_rows:].reset_index(drop=True)
        return df, TickResult("new_bar", label, max(gap, 0))

    # 遲到的 Tick：只在最近幾根中尋找
    tail = df.tail(10)
    match = tail.index[pd.to_datetime(tail["datetime"]) == label]
    if len(match) > 0:
        # 遲到 Tick 的先後順序未知，不覆寫 close (由伺服器對帳修正)
        _update_bar(df, match[-1], price, volume, update_close=False)
        return df, TickResult("late_updated", label)
    return df, TickResult("late_dropped", label)


def _new_bar(label, price, volume, session_fn) -> dict:
    return {
        "open": float(price),
        "high": float(price),
        "low": float(price),
        "close": float(price),
        "volume": int(volume),
        "datetime": label,
        "session": session_fn(label),
    }


def _update_bar(df, idx, price, volume, update_close: bool) -> None:
    price = float(price)
    if price > df.at[idx, "high"]:
        df.at[idx, "high"] = price
    if price < df.at[idx, "low"]:
        df.at[idx, "low"] = price
    if update_close:
        df.at[idx, "close"] = price
    df.at[idx, "volume"] = int(df.at[idx, "volume"]) + int(volume)


@dataclass
class MergeResult:
    """merge_server_bars 結果。"""
    changed: pd.DataFrame = field(default_factory=pd.DataFrame)  # 新增或被修正的 K 棒 (伺服器版本)
    mismatches: list = field(default_factory=list)  # [(label, live_volume, server_volume)] (僅追蹤中的即時 K 棒)
    tracked_count: int = 0   # 對帳區間內、由即時 Tick 建構且伺服器也有的 K 棒數
    live_volume: int = 0     # 上述 K 棒的 Tick 量合計
    server_volume: int = 0   # 上述 K 棒的伺服器 kbars 量合計


def kbars_to_df(kbars, session_fn: Callable = default_session) -> pd.DataFrame:
    """Shioaji kbars 物件 -> 標準 1K DataFrame (BAR_COLUMNS)。"""
    if not kbars or not hasattr(kbars, "ts") or len(kbars.ts) == 0:
        return pd.DataFrame(columns=BAR_COLUMNS)
    df = pd.DataFrame({
        "open": pd.Series(kbars.Open, dtype=float),
        "high": pd.Series(kbars.High, dtype=float),
        "low": pd.Series(kbars.Low, dtype=float),
        "close": pd.Series(kbars.Close, dtype=float),
        "volume": pd.Series(kbars.Volume, dtype="int64"),
        "datetime": pd.to_datetime(pd.Series(kbars.ts), unit="ns"),
    })
    df["session"] = df["datetime"].apply(session_fn)
    return df[BAR_COLUMNS]


def merge_server_bars(
    df: pd.DataFrame,
    df_server: pd.DataFrame,
    cutoff: pd.Timestamp,
    forming_label: Optional[pd.Timestamp] = None,
    track_labels: Optional[set] = None,
) -> tuple[pd.DataFrame, MergeResult]:
    """以伺服器 K 棒為準覆寫記憶體中「已收盤」的 K 棒 (keep='last'，伺服器版本勝出)。

    - 僅採用 label <= cutoff 的伺服器 K 棒 (避免碰觸形成中的 K 棒)。
    - 永遠不覆寫 forming_label (由即時 Tick 建立、尚未收盤) 及其之後的 K 棒。
    - 不補任何無成交分鐘 (no forward-fill)。
    - track_labels: 由即時 Tick 建構的 K 棒標籤；只對這些 K 棒統計量能差異 (診斷用)。
    """
    result = MergeResult()
    if df_server is None or df_server.empty:
        return df, result

    srv = df_server.copy()
    srv["datetime"] = pd.to_datetime(srv["datetime"])
    srv = srv[srv["datetime"] <= cutoff]
    if forming_label is not None:
        srv = srv[srv["datetime"] < pd.Timestamp(forming_label)]
    if not df.empty:
        # 不回寫早於記憶體視窗的舊資料 (避免記憶體無限成長)
        srv = srv[srv["datetime"] >= pd.Timestamp(df["datetime"].iloc[0])]
    if srv.empty:
        return df, result

    srv = srv.drop_duplicates(subset=["datetime"], keep="last")
    mem = df.set_index(pd.to_datetime(df["datetime"])) if not df.empty else df
    ohlcv = ["open", "high", "low", "close", "volume"]

    changed_rows = []
    for _, row in srv.iterrows():
        label = row["datetime"]
        tracked = track_labels is not None and label in track_labels
        if not df.empty and label in mem.index:
            m = mem.loc[label]
            if isinstance(m, pd.DataFrame):
                m = m.iloc[-1]
            if tracked:
                result.tracked_count += 1
                result.live_volume += int(m["volume"])
                result.server_volume += int(row["volume"])
                if int(m["volume"]) != int(row["volume"]):
                    result.mismatches.append((label, int(m["volume"]), int(row["volume"])))
            same = all(float(m[c]) == float(row[c]) for c in ohlcv)
            if not same:
                changed_rows.append(row)
        else:
            changed_rows.append(row)

    if not changed_rows:
        return df, result

    changed = pd.DataFrame(changed_rows)[BAR_COLUMNS].reset_index(drop=True)
    base = df[BAR_COLUMNS] if not df.empty else pd.DataFrame(columns=BAR_COLUMNS)
    merged = (
        pd.concat([base, changed], ignore_index=True)
        .drop_duplicates(subset=["datetime"], keep="last")
        .sort_values("datetime")
        .reset_index(drop=True)
    )
    result.changed = changed
    return merged, result


def cutoff_for(now: datetime, settle_minutes: int = 1) -> pd.Timestamp:
    """已收盤且已沉澱 settle_minutes 分鐘的 K 棒標籤上限。

    例: now=09:03:20 -> floor=09:03 (09:02~09:03 那根剛收) -> settle 1 分鐘 -> cutoff=09:02。
    """
    return pd.Timestamp(now).floor("min") - pd.Timedelta(minutes=settle_minutes)
