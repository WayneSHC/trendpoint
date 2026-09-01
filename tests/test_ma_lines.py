# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 014 T003／T005：`ma_lines` 純函式契約測試。

對應 specs/014-ma-touch-alerts/contracts/ma-alerts.md §1、§2
與 SC-002／003／005／006。
"""

import pandas as pd
import pytest

from ma_lines import (INSUFFICIENT_LABEL, SNAPSHOT_HEADER, SNAPSHOT_NOTE,
                      compute_ma_set, detect_cross_below, format_ma_snapshot)
from tests.ma_fixtures import daily_frame

PERIODS = {"monthly": 20, "quarterly": 60, "half_yearly": 120, "yearly": 240}


# ---------------------------------------------------------------------------
# T003：compute_ma_set
# ---------------------------------------------------------------------------

def test_ma_value_equals_mean_of_last_period_closes():
    """SC-006：均線值＝最後 period 根收盤價算術平均，誤差為 0。"""
    df = daily_frame(n=300, base=100.0, slope=1.0)   # 收盤價 100, 101, ... 399
    close = df["close"]

    ma = compute_ma_set(close, PERIODS)

    for name, period in PERIODS.items():
        expected = close.iloc[-period:].mean()
        assert ma[name] == pytest.approx(expected, abs=1e-12), f"{name} 均線值與手算不符"


def test_flat_series_ma_equals_base():
    """slope=0 時四條均線皆等於 base——供其他測試以精確值預期。"""
    df = daily_frame(n=300, base=100.0, slope=0.0)
    ma = compute_ma_set(df["close"], PERIODS)
    assert all(v == pytest.approx(100.0) for v in ma.values())


def test_insufficient_data_returns_none_not_nan_or_zero():
    """
    SC-005：資料不足之線必須回傳 None。

    禁止 min_periods=1（ladder_system.py:463 為回測暖機期而設，語意相反）；
    亦禁止回傳 NaN——`NaN > x` 恰好為 False 是實作巧合而非契約
    （同 ladder_system.py:645-649 的 atr_ready 教訓）。
    """
    df = daily_frame(n=100, base=100.0, slope=0.0)   # 僅 100 根
    ma = compute_ma_set(df["close"], PERIODS)

    assert ma["monthly"] is not None, "20 日線在 100 根下應可計算"
    assert ma["quarterly"] is not None, "60 日線在 100 根下應可計算"
    assert ma["half_yearly"] is None, "120 日線在 100 根下必須為 None"
    assert ma["yearly"] is None, "240 日線在 100 根下必須為 None"

    # 明確排除 NaN 與 0 這兩種「看似正常」的替代品
    for name in ("half_yearly", "yearly"):
        assert not isinstance(ma[name], float), f"{name} 不得回傳 float（NaN/0 皆不可）"


def test_exactly_enough_data_is_sufficient():
    """邊界：恰好 period 根即足夠（>= 而非 >）。"""
    df = daily_frame(n=20, base=100.0, slope=0.0)
    ma = compute_ma_set(df["close"], {"monthly": 20})
    assert ma["monthly"] == pytest.approx(100.0)

    df19 = daily_frame(n=19, base=100.0, slope=0.0)
    assert compute_ma_set(df19["close"], {"monthly": 20})["monthly"] is None


def test_keys_match_periods_and_input_not_mutated():
    """回傳鍵集合與 periods 相同；不就地修改輸入。"""
    df = daily_frame(n=300)
    close = df["close"]
    before = close.copy()

    ma = compute_ma_set(close, PERIODS)

    assert set(ma.keys()) == set(PERIODS.keys())
    pd.testing.assert_series_equal(close, before)


def test_empty_series_all_none():
    """空序列：全部為 None，不拋錯。"""
    ma = compute_ma_set(pd.Series(dtype=float), PERIODS)
    assert all(v is None for v in ma.values())


# ---------------------------------------------------------------------------
# T005：detect_cross_below
# ---------------------------------------------------------------------------

_MA = {"monthly": 100.0, "quarterly": 110.0, "half_yearly": None, "yearly": 120.0}


def test_cross_below_triggers_when_crossing_down():
    """SC-002：prev > ma 且 curr <= ma → 該線觸發。"""
    assert detect_cross_below(prev_price=101.0, curr_price=99.0, ma_set=_MA) == ["monthly"]


def test_touching_exactly_counts_as_trigger():
    """「達到或低於」：curr 恰等於均線亦觸發（<= 而非 <）。"""
    assert "monthly" in detect_cross_below(101.0, 100.0, _MA)


def test_persisting_below_does_not_trigger():
    """SC-003：前值已在均線下方（持續低於）→ 不觸發，避免每根重複通知。"""
    assert detect_cross_below(prev_price=99.0, curr_price=98.0, ma_set=_MA) == []


def test_staying_above_does_not_trigger():
    assert detect_cross_below(prev_price=130.0, curr_price=125.0, ma_set=_MA) == []


def test_none_lines_are_skipped_without_error():
    """SC-005：ma 為 None 之線被略過且不拋錯。"""
    result = detect_cross_below(prev_price=200.0, curr_price=50.0, ma_set=_MA)
    assert "half_yearly" not in result
    assert set(result) == {"monthly", "quarterly", "yearly"}


def test_multiple_lines_can_trigger_together():
    """一次跌破多條線時各自回報。"""
    result = detect_cross_below(prev_price=125.0, curr_price=105.0, ma_set=_MA)
    assert set(result) == {"quarterly", "yearly"}


def test_upward_cross_never_triggers():
    """僅偵測向下穿越；站回均線不在本案範圍。"""
    assert detect_cross_below(prev_price=95.0, curr_price=105.0, ma_set=_MA) == []


def test_all_none_returns_empty_list():
    assert detect_cross_below(200.0, 1.0, {"yearly": None}) == []


# ---------------------------------------------------------------------------
# format_ma_snapshot：附在每則推播尾端的「均線現況」區塊
# ---------------------------------------------------------------------------

_SNAPSHOT_PERIODS = {"yearly": 240, "weekly": 5, "monthly": 20, "quarterly": 60}


def test_snapshot_lists_every_line_shortest_first():
    """
    呈現順序由週期長短決定，不受 dict 插入順序影響——訊息裡的順序若會漂移，
    使用者每次都得重新找哪一行是哪條線。
    """
    ma = {"weekly": 105.0, "monthly": 100.0, "quarterly": 95.0, "yearly": 90.0}
    block = format_ma_snapshot(ma, _SNAPSHOT_PERIODS, price=99.0)

    body = block.strip().splitlines()
    assert body[0] == SNAPSHOT_HEADER
    assert body[1] == SNAPSHOT_NOTE
    assert [line.split(" (")[0] for line in body[2:]] == ["週線", "月線", "季線", "年線"]


def test_snapshot_shows_value_and_deviation_against_price():
    """每行含均線值（兩位小數）與相對比較價的乖離。"""
    block = format_ma_snapshot({"monthly": 100.0}, {"monthly": 20}, price=99.0)
    assert "月線 (20 日): 100.00（-1.00%）" in block


def test_snapshot_marks_insufficient_lines_instead_of_dropping_them():
    """
    資料不足之線顯示「資料不足」而**不是略過**：漏掉一行會讓使用者以為
    那條線不存在，而真相是「算不出來」——兩者在決策上完全不同。
    """
    ma = {"weekly": 105.0, "yearly": None}
    block = format_ma_snapshot(ma, {"weekly": 5, "yearly": 240}, price=99.0)

    assert f"年線 (240 日): {INSUFFICIENT_LABEL}" in block
    assert "週線 (5 日): 105.00" in block


def test_snapshot_is_empty_when_no_periods_enabled():
    """全線關閉／總開關關閉 → 空字串，呼叫端可無條件串接。"""
    assert format_ma_snapshot({}, {}, price=99.0) == ""


def test_snapshot_starts_with_blank_line_separator():
    """以空行與訊息本文分隔——直接串接即可，呼叫端不需自行補換行。"""
    block = format_ma_snapshot({"monthly": 100.0}, {"monthly": 20}, price=99.0)
    assert block.startswith("\n\n")


def test_snapshot_does_not_reuse_the_cross_alert_deviation_label():
    """
    現況區塊刻意不含「乖離:」字樣——跌破通知以該欄位標示「觸發的是哪條線」，
    兩者若共用同一字樣，訊息裡的主從關係就消失了。
    """
    block = format_ma_snapshot({"monthly": 100.0}, {"monthly": 20}, price=99.0)
    assert "乖離:" not in block
