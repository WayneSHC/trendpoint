# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 010 — 連續月拼接引擎錨定測試（SC-002）。

規則：量最大月轉倉（第 k 日近月由第 k−1 日量能判定；次月量>近月 → 換；單向不回切；
首日以當日量最大初始化——唯一例外）；back-adjust = 轉倉日新舊契約前一日收盤差
回溯累積平移。全部數字手算。
"""

import pandas as pd
import pytest

from data_sources.rollover import (
    build_continuous,
    compute_roll_events,
    select_front_month,
)


def _raw(rows):
    """rows: (date_str, contract, close, volume)；OHLC 以 close 展開（引擎不看 O/H/L 邏輯）。"""
    return pd.DataFrame([
        {"date": pd.Timestamp(d), "contract": c,
         "open": px - 1.0, "high": px + 2.0, "low": px - 2.0, "close": px,
         "volume": float(v), "settlement": px, "open_interest": 1000.0}
        for d, c, px, v in rows
    ])


# 錨定情境：A=202301、B=202302、C=202303；8 個交易日
# d1: A(100,v100) B(108,v10)          → 首日：量最大 = A
# d2: A(102,v80)  B(110,v90)          → d2 判定用 d1 量（A100>B10）→ A；d3 判定用 d2 量（B90>A80）→ 換 B
# d3: B(111,v95)  A(103,v5)           → front=B；roll@d3：adj1 = B(d2)−A(d2) = 110−102 = 8
# d4: B(112,v90)  C(118,v20)
# d5: B(113,v50)  C(120,v60)          → d6 判定用 d5 量（C60>B50）→ 換 C
# d6: C(121,v80)  B(114,v10)          → roll@d6：adj2 = C(d5)−B(d5) = 120−113 = 7
# d7: C(122,v90)
# d8: C(123,v95)
_ROWS = [
    ("2023-01-02", "202301", 100.0, 100), ("2023-01-02", "202302", 108.0, 10),
    ("2023-01-03", "202301", 102.0, 80),  ("2023-01-03", "202302", 110.0, 90),
    ("2023-01-04", "202301", 103.0, 5),   ("2023-01-04", "202302", 111.0, 95),
    ("2023-01-05", "202302", 112.0, 90),  ("2023-01-05", "202303", 118.0, 20),
    ("2023-01-06", "202302", 113.0, 50),  ("2023-01-06", "202303", 120.0, 60),
    ("2023-01-09", "202302", 114.0, 10),  ("2023-01-09", "202303", 121.0, 80),
    ("2023-01-10", "202303", 122.0, 90),
    ("2023-01-11", "202303", 123.0, 95),
]

_EXPECTED_FRONT = ["202301", "202301", "202302", "202302", "202302",
                   "202303", "202303", "202303"]
# 連續收盤（未調整）：100,102,111,112,113,121,122,123
# adj1=8（生效於 d3 之前的所有列）、adj2=7（生效於 d6 之前）→ 累積：
# d1,d2: +15；d3..d5: +7；d6..d8: +0
_EXPECTED_CONT_CLOSE = [115.0, 117.0, 118.0, 119.0, 120.0, 121.0, 122.0, 123.0]


def test_front_month_selection_anchor():
    raw = _raw(_ROWS)
    front = select_front_month(raw)
    assert list(front.values) == _EXPECTED_FRONT
    assert front.index.is_monotonic_increasing


def test_front_month_never_rolls_back():
    """量回落不回切：d4 起 A 已無足夠量，且規則只考慮更晚契約——front 單調非遞減。"""
    raw = _raw(_ROWS)
    front = select_front_month(raw)
    assert list(front.values) == sorted(front.values), "近月序列必須單調非遞減"


def test_roll_events_anchor():
    raw = _raw(_ROWS)
    front = select_front_month(raw)
    events = compute_roll_events(raw, front)
    assert len(events) == 2
    e1, e2 = events
    assert (str(e1.roll_date.date()), e1.from_contract, e1.to_contract) == ("2023-01-04", "202301", "202302")
    assert e1.adjustment == pytest.approx(8.0)    # 110 − 102
    assert (str(e2.roll_date.date()), e2.from_contract, e2.to_contract) == ("2023-01-09", "202302", "202303")
    assert e2.adjustment == pytest.approx(7.0)    # 120 − 113


def test_back_adjust_continuous_anchor():
    raw = _raw(_ROWS)
    front = select_front_month(raw)
    events = compute_roll_events(raw, front)
    cont = build_continuous(raw, front, events)
    assert list(cont["close"].values) == pytest.approx(_EXPECTED_CONT_CLOSE)
    # O/H/L 同步平移：close−open 恆為 1（構造性質）
    assert (cont["close"] - cont["open"]).round(9).eq(1.0).all()
    # 量不平移：連續量 = 各日近月契約量
    assert list(cont["volume"].values) == pytest.approx(
        [100, 80, 95, 90, 50, 80, 90, 95])


def test_delta_equals_same_contract_true_change():
    """back-adjust 核心性質：相鄰 Δclose 恆等於「當日近月契約」的真實變動（無轉倉跳空）。"""
    raw = _raw(_ROWS)
    front = select_front_month(raw)
    cont = build_continuous(raw, front, compute_roll_events(raw, front))
    closes = {(r["date"], r["contract"]): r["close"] for _, r in raw.iterrows()}
    dates = list(cont.index)
    for i in range(1, len(dates)):
        c = front.iloc[i]                       # 當日近月
        expected = closes[(dates[i], c)] - closes[(dates[i - 1], c)]
        actual = cont["close"].iloc[i] - cont["close"].iloc[i - 1]
        assert actual == pytest.approx(expected), f"{dates[i]} Δ 含轉倉跳空"


def test_truncation_invariance_of_front_sequence():
    """看前紀律（FR-004）：截斷尾段不改變既往近月選擇序列。"""
    raw = _raw(_ROWS)
    full_front = select_front_month(raw)
    for cut in ["2023-01-05", "2023-01-09"]:
        sub = raw[raw["date"] <= pd.Timestamp(cut)]
        sub_front = select_front_month(sub)
        joint = sub_front.index
        assert list(sub_front.values) == list(full_front.loc[joint].values)


def test_expiry_force_roll():
    """近月到期消失（量未先交叉）→ 當日強制換至次一存在契約（資料存在性，非預測）。"""
    rows = [
        ("2023-02-01", "202302", 100.0, 100), ("2023-02-01", "202303", 105.0, 10),
        ("2023-02-02", "202302", 101.0, 100), ("2023-02-02", "202303", 106.0, 20),
        # 202302 到期消失
        ("2023-02-03", "202303", 107.0, 90),
        ("2023-02-06", "202303", 108.0, 95),
    ]
    raw = _raw(rows)
    front = select_front_month(raw)
    assert list(front.values) == ["202302", "202302", "202303", "202303"]
    events = compute_roll_events(raw, front)
    assert len(events) == 1
    assert events[0].adjustment == pytest.approx(106.0 - 101.0)   # 新舊 k−1 收盤差
    cont = build_continuous(raw, front, events)
    # d1,d2 平移 +5：105,106,107,108
    assert list(cont["close"].values) == pytest.approx([105.0, 106.0, 107.0, 108.0])
