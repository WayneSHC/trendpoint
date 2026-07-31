# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 013 — `risk_gates.py` 兩個純元件的契約測試（T003 / T004）。

對應 contracts/entry-gate.md §1（`settlement_days`）與 §2（`DrawdownGate`），
以及 data-model.md §1 的狀態轉移表。本檔**不碰回測引擎**——單向依賴的另一面是
這兩個元件必須能單獨測完。
"""

import datetime as dt

import pandas as pd
import pytest

from gate_fixtures import third_wednesday
from risk_gates import DrawdownGate, settlement_days


# ---------------------------------------------------------------- settlement_days

def _bdays(start: str, periods: int) -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=periods, name="datetime")


def test_settlement_days_picks_third_wednesday():
    """(a) 每月第三個週三正確。"""
    idx = _bdays("2024-01-01", 260)          # 約 12 個月
    days = settlement_days(idx)

    for month in range(1, 13):
        tw = third_wednesday(2024, month).date()
        if tw in {d.date() for d in idx}:
            assert tw in days, f"2024-{month:02d} 的第三個週三 {tw} 應為結算日"

    # 全部結算日都是週三（本索引無假日缺席，故不會後推）
    assert all(d.weekday() == 2 for d in days)


def test_settlement_days_rolls_forward_when_absent():
    """(b) 第三個週三缺席時取其後第一個交易日。"""
    idx = _bdays("2024-01-01", 120)
    target = third_wednesday(2024, 3)                     # 2024-03-20（週三）
    holed = idx[idx != target]

    days = settlement_days(holed)
    assert target.date() not in days
    assert (target + pd.Timedelta(days=1)).date() in days, "應後推至次一交易日 2024-03-21"

    # 其他月份不受影響
    assert third_wednesday(2024, 2).date() in days


def test_settlement_days_handles_intraday_index():
    """(c) 日內索引（同日多棒）去重後仍正確。"""
    days_idx = _bdays("2024-05-01", 40)
    stamps = []
    for day in days_idx:
        stamps.extend(pd.date_range(day + pd.Timedelta(hours=9), periods=54, freq="5min"))
    intraday = pd.DatetimeIndex(stamps, name="datetime")

    assert settlement_days(intraday) == settlement_days(days_idx)
    assert third_wednesday(2024, 5).date() in settlement_days(intraday)


def test_settlement_days_month_truncated_at_tail_is_skipped():
    """(d) 該月第三個週三之後無交易日 → 該月不列入，且不拋錯。"""
    # 索引止於 2024-03-11，3 月第三個週三（03-20）之後無資料
    idx = pd.bdate_range(start="2024-03-01", end="2024-03-11", name="datetime")
    days = settlement_days(idx)          # 不得拋錯
    assert days == set()


def test_settlement_days_is_pure():
    """(e) 純函式：同輸入同輸出，且輸出僅依賴索引（不含價量）。"""
    idx = _bdays("2024-01-01", 200)
    assert settlement_days(idx) == settlement_days(idx)

    # 同一索引、不同價格 → 結果相同（結構上即成立：函式只收索引）
    assert settlement_days(pd.DatetimeIndex(list(idx), name="other")) == settlement_days(idx)

    # 回傳型別為 date 集合（比較粒度＝日）
    days = settlement_days(idx)
    assert days and all(isinstance(d, dt.date) and not isinstance(d, dt.datetime) for d in days)


def test_settlement_days_empty_index():
    assert settlement_days(pd.DatetimeIndex([], name="datetime")) == set()


# ---------------------------------------------------------------- DrawdownGate

LIMIT, RESUME = 0.20, 0.10


def _gate(initial: float = 1_000_000.0, limit: float = LIMIT, resume: float = RESUME):
    return DrawdownGate(initial_equity=initial, limit_pct=limit, resume_pct=resume)


def test_dd_gate_starts_open():
    """(a) 初始 OPEN。"""
    assert _gate().blocked is False


def test_dd_gate_blocks_at_limit():
    """(b) dd <= -limit → BLOCKED。"""
    g = _gate()
    g.update(850_000.0)          # dd = -15%，未達門檻
    assert g.blocked is False
    g.update(800_000.0)          # dd = -20%，恰達門檻（含等號）
    assert g.blocked is True


def test_dd_gate_resumes_at_resume_threshold():
    """(c) dd >= -resume → OPEN。"""
    g = _gate()
    g.update(750_000.0)
    assert g.blocked is True
    g.update(890_000.0)          # dd = -11%，仍在遲滯區
    assert g.blocked is True
    g.update(900_000.0)          # dd = -10%，恰達恢復門檻（含等號）
    assert g.blocked is False


def test_dd_gate_hysteresis_is_path_dependent():
    """(d) 遲滯區間內維持原狀態——同一 dd 值由兩個方向進入，結果不同。"""
    down = _gate()
    down.update(850_000.0)                       # 由上方進入 -15%
    assert down.blocked is False

    up = _gate()
    up.update(700_000.0)                         # 先跌破封鎖門檻
    up.update(850_000.0)                         # 由下方回到同一個 -15%
    assert up.blocked is True

    # 這正是「遲滯」的定義：狀態不是 dd 的函數，而是路徑的函數
    assert down.blocked != up.blocked


def test_dd_gate_peak_is_monotonic():
    """(e) peak 單調不減。"""
    g = _gate()
    peaks = []
    for eq in (1_100_000.0, 900_000.0, 1_050_000.0, 1_300_000.0, 400_000.0):
        g.update(eq)
        peaks.append(g.peak)
    assert peaks == sorted(peaks)
    assert peaks[0] == 1_100_000.0 and peaks[-1] == 1_300_000.0


def test_dd_gate_resume_zero_is_legal():
    """(f) resume_pct = 0.0 合法：需回撤完全回復才解除。"""
    g = _gate(resume=0.0)
    g.update(700_000.0)
    assert g.blocked is True
    g.update(999_999.0)          # 差一元也不解除
    assert g.blocked is True
    g.update(1_000_000.0)        # dd = 0 → 解除
    assert g.blocked is False


def test_dd_gate_non_positive_peak_does_not_divide_by_zero():
    """(g) peak <= 0 不除零（防禦；正常情況由爆倉防護先終止回測）。"""
    g = DrawdownGate(initial_equity=0.0, limit_pct=LIMIT, resume_pct=RESUME)
    g.update(-500.0)             # 不得拋 ZeroDivisionError
    assert isinstance(g.blocked, bool)

    g2 = DrawdownGate(initial_equity=-1.0, limit_pct=LIMIT, resume_pct=RESUME)
    g2.update(-2.0)
    assert isinstance(g2.blocked, bool)


def test_dd_gate_rejects_resume_not_below_limit():
    """防禦性斷言：resume >= limit 在元件層即拒絕（schema 為第一道，這是第二道）。"""
    with pytest.raises(ValueError):
        _gate(limit=0.10, resume=0.10)
    with pytest.raises(ValueError):
        _gate(limit=0.10, resume=0.20)


def test_dd_gate_does_not_import_engine_modules():
    """單向依賴：risk_gates 不得 import 回測/策略模組（否則無法獨立單元測試）。"""
    import risk_gates
    src = open(risk_gates.__file__, encoding="utf-8").read()
    for forbidden in ("import backtester", "from backtester", "import ladder_system", "from ladder_system"):
        assert forbidden not in src, f"risk_gates 不得依賴引擎模組：{forbidden}"
