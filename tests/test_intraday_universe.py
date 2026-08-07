# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - spec 016 標的納入準則驗收（T027–T030）。

對應 SC-005（擾動不敏感）、SC-006（排除理由可追溯）。

本檔守的是**選擇偏誤**這一項。它比缺樣本外切分更根本：切分錯了只是結論
偏弱，標的挑錯了則污染所有後續數字——包含樣本外的那些。
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fixtures_016_intraday as fx  # noqa: E402
import intraday_report as irep  # noqa: E402
import intraday_snapshot as isnap  # noqa: E402
import intraday_universe as iuni  # noqa: E402
from config import load_config  # noqa: E402


@pytest.fixture(scope="module")
def ie():
    return load_config().intraday_evaluation


@pytest.fixture(scope="module")
def histories():
    return {
        "2330.TW": isnap.normalize_frame(fx.intraday_frame(60, seed=42)),
        "2454.TW": isnap.normalize_frame(fx.intraday_frame(60, seed=7)),
    }


# ---------------------------------------------------------------------------
# SC-005 擾動不敏感
# ---------------------------------------------------------------------------


def test_perturbation_insensitive(monkeypatch, histories, ie):
    """人為改變回測輸出後，納入清單與實測值皆不得改變（FR-010）。"""
    before_decisions, before_included = iuni.build_universe(histories, ie)

    def poisoned(*args, **kwargs):
        raise AssertionError("納入準則不得呼叫回測——這是選擇偏誤的入口")

    monkeypatch.setattr(irep, "run_backtest", poisoned)
    monkeypatch.setattr(irep, "build_indicator", poisoned)

    after_decisions, after_included = iuni.build_universe(histories, ie)

    assert [d.to_dict() for d in before_decisions] == [
        d.to_dict() for d in after_decisions
    ]
    assert sorted(before_included) == sorted(after_included)


def test_universe_module_does_not_import_backtest_layer():
    """靜態守備：準則模組不得引用回測／訊號模組。"""
    import ast

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "intraday_universe.py",
    )
    tree = ast.parse(open(path, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    forbidden = {"backtester", "ladder_system", "intraday_report", "performance"}
    assert not (imported & forbidden), (
        f"納入準則引用了 {sorted(imported & forbidden)}——準則一旦讀得到績效，"
        "它就不再是事前的客觀判定"
    )


def test_criteria_only_read_lookback(histories, ie):
    """準則的輸入僅限 lookback 段：把評估段整段換掉，判定不得改變。"""
    df = histories["2330.TW"]
    lookback, evaluation = iuni.split_lookback_and_eval(df, ie.lookback_days)
    baseline = iuni.apply_criteria("2330.TW", lookback, ie)

    # 評估段的價格全部乘 10——若判定會變，代表它讀到了不該讀的東西。
    tampered = df.copy()
    eval_mask = tampered.index.isin(evaluation.index)
    for col in ("open", "high", "low", "close"):
        tampered.loc[eval_mask, col] *= 10.0
    tampered_lookback, _ = iuni.split_lookback_and_eval(tampered, ie.lookback_days)
    assert iuni.apply_criteria("2330.TW", tampered_lookback, ie).to_dict() == (
        baseline.to_dict()
    )


# ---------------------------------------------------------------------------
# research.md R5：lookback 與評估窗不重疊
# ---------------------------------------------------------------------------


def test_lookback_disjoint_from_eval_window(histories, ie):
    df = histories["2330.TW"]
    lookback, evaluation = iuni.split_lookback_and_eval(df, ie.lookback_days)
    assert lookback is not None and evaluation is not None
    assert len(lookback.index.intersection(evaluation.index)) == 0
    assert lookback.index.max() < evaluation.index.min()
    assert pd.Series(lookback.index.date).nunique() == ie.lookback_days


def test_insufficient_lookback_excludes_rather_than_borrows(ie):
    """lookback 不足時必須排除，不得拿評估窗資料代打。"""
    short = isnap.normalize_frame(fx.intraday_frame(ie.lookback_days, seed=42))
    lookback, evaluation = iuni.split_lookback_and_eval(short, ie.lookback_days)
    assert lookback is None and evaluation is None
    d = iuni.apply_criteria("SHORT.TW", lookback, ie)
    assert d.included is False
    assert iuni.CRITERION_LOOKBACK in d.failed_criteria


def test_build_universe_returns_eval_segment_only(histories, ie):
    """介面上拿不到 lookback 段，下游就不可能誤用它評估。"""
    _, included = iuni.build_universe(histories, ie)
    for ticker, evaluation in included.items():
        full = histories[ticker]
        lookback, _ = iuni.split_lookback_and_eval(full, ie.lookback_days)
        assert len(evaluation.index.intersection(lookback.index)) == 0


# ---------------------------------------------------------------------------
# SC-006 排除理由可追溯
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frame_factory,expected",
    [
        (lambda: fx.low_volume_frame(60), iuni.CRITERION_VOLUME),
        (lambda: fx.ragged_frame(60), iuni.CRITERION_CV),
        (lambda: fx.coarse_tick_frame(60), iuni.CRITERION_TICK),
    ],
)
def test_exclusion_traceable(frame_factory, expected, ie):
    df = isnap.normalize_frame(frame_factory())
    lookback, _ = iuni.split_lookback_and_eval(df, ie.lookback_days)
    d = iuni.apply_criteria("TEST.TW", lookback, ie)
    assert d.included is False
    assert expected in d.failed_criteria
    assert d.measured, "排除時仍須提供實測值，供讀者檢驗判定是否合理"


def test_failed_criteria_map_to_config_keys(ie):
    """每個 failed_criteria 項都必須對應到組態中的具體門檻鍵。"""
    df = isnap.normalize_frame(fx.low_volume_frame(60))
    lookback, _ = iuni.split_lookback_and_eval(df, ie.lookback_days)
    d = iuni.apply_criteria("TEST.TW", lookback, ie)
    for item in d.failed_criteria:
        assert item in iuni.ALL_CRITERIA
        if item not in (iuni.CRITERION_LOOKBACK, iuni.CRITERION_EXCLUDED):
            assert hasattr(ie, item), f"{item} 在組態中無對應門檻"


def test_explicit_exclusion_list(ie, histories):
    """槓桿型 ETF 顯式排除，且排除理由一樣要出現在報告裡。"""
    assert "00631L.TW" in ie.excluded_tickers
    d = iuni.apply_criteria("00631L.TW", histories["2330.TW"], ie)
    assert d.included is False
    assert iuni.CRITERION_EXCLUDED in d.failed_criteria


def test_included_ticker_has_empty_failed_criteria(histories, ie):
    decisions, _ = iuni.build_universe(histories, ie)
    for d in decisions:
        if d.included:
            assert d.failed_criteria == []


# ---------------------------------------------------------------------------
# FR-012 準則版本
# ---------------------------------------------------------------------------


def test_criteria_version_recorded_and_changes_with_thresholds(ie):
    v1 = iuni.criteria_version(ie)
    assert v1.startswith("v1-")

    bumped = ie.model_copy(update={"min_avg_daily_volume": ie.min_avg_daily_volume * 2})
    assert iuni.criteria_version(bumped) != v1, (
        "門檻改了而版本沒改，新舊結論會被誤當成同一條件下的對照"
    )


def test_criteria_version_stable_for_same_thresholds(ie):
    assert iuni.criteria_version(ie) == iuni.criteria_version(ie.model_copy())


def test_universe_decision_is_deterministic(histories, ie):
    a, _ = iuni.build_universe(histories, ie)
    b, _ = iuni.build_universe(histories, ie)
    assert [d.to_dict() for d in a] == [d.to_dict() for d in b]
