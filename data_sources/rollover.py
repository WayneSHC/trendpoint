# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 期貨連續月拼接引擎 (spec 010)

由「按契約月份」的原始長表產生連續 OHLCV 序列：
1. select_front_month：量最大月規則——第 k 日近月由第 k−1 日各契約成交量判定
   （次一更晚契約量 > 現行近月 → 換月）；單向不回切；首日以當日量最大初始化
   （唯一允許用當日資訊的例外，見 spec 010 contracts）；近月到期消失時強制
   前進至次一存在契約（資料存在性，非預測）。
2. compute_roll_events：轉倉事件（roll_date, from, to, adjustment=新舊契約
   前一日收盤差）。
3. build_continuous：各日取近月列，依事件差額回溯**累積**平移 O/H/L/C
   （量與未平倉不平移）——消除轉倉跳空、保持 Δ點正確；早期絕對水位可能 ≤ 0
   （無害：系統僅消費相對變化，spec 010 Assumptions）。

看前紀律（憲章 I / FR-004）：判定僅用前一交易日資訊——截斷尾段不改變既往
近月選擇序列（back-adjust 平移基準隨尾端而異屬預期，斷言以近月序列為準）。
"""

from __future__ import annotations

from typing import List, NamedTuple

import pandas as pd


class RollEvent(NamedTuple):
    roll_date: pd.Timestamp      # 轉倉生效日（該日起用新契約）
    from_contract: str
    to_contract: str
    adjustment: float            # 新契約(前一日收盤) − 舊契約(前一日收盤)


_REQUIRED = {"date", "contract", "close", "volume"}


def _check_raw(raw: pd.DataFrame) -> None:
    missing = _REQUIRED - set(raw.columns)
    if missing:
        raise ValueError(f"rollover 輸入缺少必要欄位 {missing}")
    if raw.empty:
        raise ValueError("rollover 輸入為空")


def select_front_month(raw: pd.DataFrame) -> pd.Series:
    """回傳 date → 近月契約 之序列（index 為排序後的交易日）。"""
    _check_raw(raw)
    vol = raw.pivot_table(index="date", columns="contract", values="volume",
                          aggfunc="sum")
    dates = vol.index.sort_values()
    front: List[str] = []
    prev: str | None = None

    for i, d in enumerate(dates):
        today = vol.loc[d].dropna()
        if prev is None:
            # 首日初始化：當日量最大（唯一例外）
            prev = str(today.idxmax())
        else:
            # 到期強制前進：近月當日已無資料 → 換至次一存在契約
            if prev not in today.index:
                later = sorted(c for c in today.index if str(c) > prev)
                if not later:
                    raise ValueError(f"{d.date()} 無可用契約可承接近月 '{prev}'")
                prev = str(later[0])
            else:
                # 量最大月規則：以前一交易日量判定「次一更晚契約」是否超越近月
                yesterday = vol.loc[dates[i - 1]].dropna()
                if prev in yesterday.index:
                    later = sorted(c for c in yesterday.index if str(c) > prev)
                    if later:
                        challenger = str(later[0])
                        if yesterday[challenger] > yesterday[prev]:
                            prev = challenger
        front.append(prev)

    return pd.Series(front, index=dates, name="front_contract")


def compute_roll_events(raw: pd.DataFrame, front: pd.Series) -> List[RollEvent]:
    """由近月序列導出轉倉事件；adjustment = 新舊契約於轉倉前一日之收盤差。"""
    _check_raw(raw)
    closes = raw.set_index(["date", "contract"])["close"]
    events: List[RollEvent] = []
    dates = list(front.index)
    for i in range(1, len(dates)):
        old_c, new_c = front.iloc[i - 1], front.iloc[i]
        if new_c == old_c:
            continue
        prev_d = dates[i - 1]
        try:
            adj = float(closes.loc[(prev_d, new_c)] - closes.loc[(prev_d, old_c)])
        except KeyError as e:
            raise ValueError(
                f"轉倉差額無法計算：{prev_d.date()} 缺 '{old_c}' 或 '{new_c}' 收盤"
            ) from e
        events.append(RollEvent(dates[i], old_c, new_c, adj))
    return events


def build_continuous(raw: pd.DataFrame, front: pd.Series,
                     events: List[RollEvent]) -> pd.DataFrame:
    """產生連續 OHLCV：各日取近月列、事件差額回溯累積平移價格欄。"""
    _check_raw(raw)
    indexed = raw.set_index(["date", "contract"])
    rows = []
    for d, c in front.items():
        try:
            r = indexed.loc[(d, c)]
        except KeyError as e:
            raise ValueError(f"連續序列缺列：{d.date()} × '{c}'") from e
        rows.append({
            "date": d,
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": float(r["volume"]),
        })
    cont = pd.DataFrame(rows).set_index("date")

    # 回溯累積平移：每一事件對其生效日**之前**的所有列加上 adjustment
    price_cols = ["open", "high", "low", "close"]
    for ev in events:
        mask = cont.index < ev.roll_date
        cont.loc[mask, price_cols] = cont.loc[mask, price_cols] + ev.adjustment

    cont.index.name = None
    return cont
