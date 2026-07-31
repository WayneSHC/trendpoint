# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 013 — 消融清單整合驗收（SC-011，T030）。

以合成資料實跑兩列風控閘門的消融，證明它們產出的是**可判讀的數字**
（含裁決用的 MDD 與 Calmar），而非空值或與基準相同的複本。
"""

import pytest

from backtester import BacktestEngine
from config.config import SingleStrategyParams, SystemConfig
from run_ablation import ABLATION_TARGETS, RISK_GATE_KEYS, run_ablation_for_ticker

from gate_fixtures import losing_then_recovering_klines

TICKER = "0050.TW"


def _cfg(**overrides):
    cfg = SystemConfig()
    base = dict(use_adx_filter=cfg.strategy.default.use_adx_filter)
    cfg.strategy.ticker_overrides[TICKER] = SingleStrategyParams(**{**base, **overrides})
    return cfg


def _row(results, label):
    return next(r for r in results if r["label"] == label)


def test_sc011_ablation_targets_contain_both_gates():
    keys = [k for _, k in ABLATION_TARGETS]
    assert "dd_gate" in keys and "settlement_gate" in keys
    assert set(RISK_GATE_KEYS) == {"dd_gate", "settlement_gate"}
    # 既有訊號濾網列不得被更動
    assert keys[:8] == [None, "structure", "momentum", "trend", "volatility",
                        "global", "regime", "fvg"]


def test_sc011_disabled_gate_rows_are_marked_skipped():
    """閘門未啟用時該列必須明示略過——不得靜默輸出與基準相同的數字。"""
    engine = BacktestEngine(initial_capital=1_000_000.0)
    results = run_ablation_for_ticker(engine, _cfg(), TICKER, losing_then_recovering_klines(800))

    for label in ("停用回撤閘門", "停用結算日閘門"):
        row = _row(results, label)
        assert row["skipped"] is True
        assert "未啟用" in row["note"]
        assert "total_return" not in row, "略過的列不得帶著看起來可比的數字"


def test_sc011_enabled_gate_row_produces_decidable_metrics():
    """啟用後該列須產出交易筆數、期望值、PF、MDD、Calmar——全部非空、非 NaN。"""
    engine = BacktestEngine(initial_capital=1_000_000.0)
    cfg = _cfg(use_dd_gate=True, dd_limit_pct=0.02, dd_resume_pct=0.005)
    results = run_ablation_for_ticker(engine, cfg, TICKER, losing_then_recovering_klines())

    row = _row(results, "停用回撤閘門")
    assert row["skipped"] is False and row["is_risk_gate"] is True
    for key in ("total_trades", "expectancy", "profit_factor", "max_drawdown", "calmar"):
        value = row[key]
        assert value is not None and value == value, f"{key} 為 NaN"

    # 停用閘門 → 交易數必須多於基準（否則這一列沒有在測任何東西）
    baseline = _row(results, "基準 (全濾網)")
    assert row["total_trades"] > baseline["total_trades"]
    for key in ("max_drawdown", "calmar"):
        assert baseline[key] == baseline[key]


def test_sc011_baseline_row_carries_calmar_for_risk_gate_comparison():
    """T029：基準列必須也有 Calmar，否則風控列無從比較（判讀提示會失效）。"""
    engine = BacktestEngine(initial_capital=1_000_000.0)
    results = run_ablation_for_ticker(engine, _cfg(), TICKER, losing_then_recovering_klines(800))
    baseline = _row(results, "基準 (全濾網)")
    assert "calmar" in baseline and "expectancy" in baseline


def test_sc011_risk_gate_heuristic_uses_risk_adjusted_metrics(capsys):
    """T029：風控列的判讀提示不得沿用『報酬未惡化且交易數增加』那條啟發式。"""
    from run_ablation import print_ablation_table

    results = [
        {"label": "基準 (全濾網)", "skipped": False, "is_risk_gate": False,
         "total_return": 0.05, "max_drawdown": -0.08, "calmar": 1.2, "sharpe": 0.9,
         "total_trades": 10, "win_rate": 0.5, "profit_factor": 1.4, "expectancy": 0.004},
        # 停用風控後報酬更高、交易更多，但風險惡化——舊啟發式會誤判為「扼殺樣本數」
        {"label": "停用回撤閘門", "skipped": False, "is_risk_gate": True,
         "total_return": 0.09, "max_drawdown": -0.25, "calmar": 0.6, "sharpe": 0.7,
         "total_trades": 18, "win_rate": 0.45, "profit_factor": 1.2, "expectancy": 0.005},
        {"label": "停用結算日閘門", "skipped": True, "note": "未啟用（use_settlement_gate=false），略過"},
    ]
    print_ablation_table(TICKER, results)
    out = capsys.readouterr().out

    assert "扼殺樣本數" not in out, "風控列被套用了訊號濾網的判讀啟發式"
    assert "該閘門確有降低風險" in out
    assert "未啟用" in out
    assert "Calmar" in out and "期望值" in out
