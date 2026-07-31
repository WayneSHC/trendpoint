# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - spec 014 均線觸價通知的合成資料產生器（T001）。

本案的驗收需要**精確控制**價格與均線的相對位置（自上方跌破／持續低於／
同日反覆穿越／資料不足），真實市場資料無法保證這些邊界情境出現在測試窗內，
故一律以合成序列驗收（見 specs/014-ma-touch-alerts/quickstart.md A 段）。

所有產生器皆為決定性：給定相同參數恆得相同輸出，不使用隨機數。
時間一律取過去日期，使「末根已收盤」成立（select_closed_bar_indices 的前提）。
"""

from typing import List, Sequence

import pandas as pd


# 台股日盤 09:00-13:30 → 5 分線每日 54 根
BARS_PER_DAY_5M = 54


def frame_from_closes(closes: Sequence[float],
                      index: pd.DatetimeIndex,
                      volume: float = 1000.0) -> pd.DataFrame:
    """
    由收盤價序列建構標準 OHLCV DataFrame。

    open 取前一根收盤（首根取自身），high/low 由 open/close 外擴一個**絕對**
    小量（pad），使 K 線形狀合法（high >= max(open, close)、
    low <= min(open, close)）且不影響本案關注的收盤價判定。

    pad 刻意採絕對值而非百分比：百分比外擴會隨價位放大，在高價位時
    「前一根的 high」可能超過「本根收盤」，使突破型訊號（BOS／三關價）
    永遠無法成立——基準凍結（T002）會因此得到空的告警集合而失去鑑別力。
    """
    if len(closes) != len(index):
        raise ValueError(f"closes 長度 {len(closes)} 與 index 長度 {len(index)} 不符")

    close = pd.Series([float(c) for c in closes], index=index)
    open_ = close.shift(1)
    open_.iloc[0] = close.iloc[0]

    pad = 0.01
    high = pd.concat([open_, close], axis=1).max(axis=1) + pad
    low = pd.concat([open_, close], axis=1).min(axis=1) - pad

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": float(volume)},
        index=index,
    )


def daily_index(n: int, start: str = "2024-01-01") -> pd.DatetimeIndex:
    """n 個交易日的索引（以工作日近似，不含假日行事曆——本案不需要）。"""
    return pd.bdate_range(start=start, periods=n, name="datetime")


def intraday_index(n_days: int,
                   start: str = "2026-06-01",
                   bars_per_day: int = BARS_PER_DAY_5M) -> pd.DatetimeIndex:
    """n_days 個交易日、每日 bars_per_day 根的 5 分線索引（09:00 起）。"""
    stamps: List[pd.Timestamp] = []
    day = pd.Timestamp(start)
    added = 0
    while added < n_days:
        if day.weekday() < 5:
            base = day + pd.Timedelta(hours=9)
            stamps.extend(base + pd.Timedelta(minutes=5 * i) for i in range(bars_per_day))
            added += 1
        day += pd.Timedelta(days=1)
    return pd.DatetimeIndex(stamps, name="datetime")


# ---------------------------------------------------------------------------
# 日線序列（供均線計算）
# ---------------------------------------------------------------------------

def daily_frame(n: int = 300,
                base: float = 100.0,
                slope: float = 0.0,
                start: str = "2024-01-01") -> pd.DataFrame:
    """
    線性日線序列。slope=0 時所有收盤價相同——此時四條均線皆等於 base，
    使「價格 vs 均線」的測試可精確預期（均線值即 base）。
    """
    idx = daily_index(n, start=start)
    closes = [base + slope * i for i in range(n)]
    return frame_from_closes(closes, idx)


# ---------------------------------------------------------------------------
# 5 分線序列（供穿越判定）
# ---------------------------------------------------------------------------

def intraday_closes(pattern: str,
                    level: float,
                    n_days: int = 2,
                    bars_per_day: int = BARS_PER_DAY_5M) -> List[float]:
    """
    產生相對 `level`（均線值）具指定型態的 5 分線收盤價序列。

    pattern:
      - "cross_below"：全程在 level 之上，**最後一根**落到 level 之下（單次跌破）
      - "stay_below"：跌破後持續低於（用於驗證不重複通知）
      - "oscillate"：同一交易日內反覆上下穿越（用於驗證每日至多一則）
      - "above"：全程在 level 之上（不應觸發）
      - "below_all"：全程在 level 之下（前值即在下方，穿越不成立）
    """
    total = n_days * bars_per_day
    above, below = level + 5.0, level - 5.0

    if pattern == "above":
        return [above] * total
    if pattern == "below_all":
        return [below] * total
    if pattern == "cross_below":
        return [above] * (total - 1) + [below]
    if pattern == "stay_below":
        # 前半在上、後半在下：僅在轉折處穿越一次，之後持續低於
        half = total // 2
        return [above] * half + [below] * (total - half)
    if pattern == "oscillate":
        # 末日多次上下穿越；前一日全程在上，確保首次穿越發生於末日
        head = [above] * bars_per_day
        tail: List[float] = []
        for i in range(total - bars_per_day):
            tail.append(below if i % 2 else above)
        return head + tail
    raise ValueError(f"未知 pattern: {pattern!r}")


def intraday_frame(pattern: str,
                   level: float,
                   n_days: int = 2,
                   start: str = "2026-06-01",
                   bars_per_day: int = BARS_PER_DAY_5M) -> pd.DataFrame:
    """依 `pattern` 產生 5 分線 OHLCV（型態語意見 intraday_closes）。"""
    idx = intraday_index(n_days, start=start, bars_per_day=bars_per_day)
    closes = intraday_closes(pattern, level, n_days=n_days, bars_per_day=bars_per_day)
    return frame_from_closes(closes, idx)


def trending_intraday_frame(n_days: int = 3,
                            base: float = 100.0,
                            step: float = 0.5,
                            start: str = "2026-06-01") -> pd.DataFrame:
    """
    穩定上升的 5 分線序列——用於既有六種告警的基準凍結（T002）：
    其結構訊號與三關價判定不依賴本案任何新程式碼，故可作為回歸錨點。

    step 須大於 frame_from_closes 的 pad，否則「前一根 high」會蓋過
    「本根收盤」，突破型訊號永不成立、基準退化為空集合而失去鑑別力。
    """
    idx = intraday_index(n_days, start=start)
    closes = [base + step * i for i in range(len(idx))]
    return frame_from_closes(closes, idx)
