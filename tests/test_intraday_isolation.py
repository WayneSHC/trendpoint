# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - spec 016 隔離護欄（T010 / T057）。

兩道獨立的守備：

1. **靜態零引用**：生產路徑不得 import 本案任一模組。方向是單向的——
   本案可讀既有模組（要跑回測就得用 BacktestEngine），既有模組不得反向引用。
   一旦反向，累積歷史（持有跨執行的完整未來價格）就成了訊號鏈裡的未來函數
   入口。沿用 spec 015 `tests/test_alert_outcomes.py` 的手法。

2. **逐欄基準對照**：日線生產路徑的逐筆交易、逐根權益、summary 須與
   `tests/fixtures/016_baseline_daily.json` 完全相同（SC-011）。
   基準於 T001 凍結，早於本案任何程式改動。
"""

import ast
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from baseline_016 import BASELINE_PATH, load_baseline, run_production_daily  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 生產路徑：訊號、回測、監控、通知、UI。這些檔案 import 本案即為缺陷。
PRODUCTION_MODULES = [
    "ladder_system.py",
    "backtester.py",
    "portfolio_backtester.py",
    "monitor_signals.py",
    "alerts.py",
    "ma_lines.py",
    "app.py",
    "walk_forward.py",
    "optimizer.py",
    "risk_gates.py",
    "trading_costs.py",
    "performance.py",
    "data_ingestion.py",
]

# spec 016 的模組。
FEATURE_MODULES = {
    "intraday_snapshot",
    "intraday_universe",
    "intraday_report",
    "run_intraday_eval",
}


def _imported_names(path: str) -> set:
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("module_file", PRODUCTION_MODULES)
def test_production_path_does_not_import_feature_modules(module_file):
    """FR-019：生產路徑對 spec 016 的引用數必須為 0。

    這個測試存在的理由不是「現在沒有引用」，而是**讓未來任何一次誤接線
    立刻紅燈**。它從 Phase 2 起就生效，覆蓋整個實作期間。
    """
    path = os.path.join(REPO_ROOT, module_file)
    if not os.path.exists(path):
        pytest.skip(f"{module_file} 不存在")
    leaked = _imported_names(path) & FEATURE_MODULES
    assert not leaked, (
        f"{module_file} import 了 spec 016 的模組 {sorted(leaked)}——"
        "累積歷史持有跨執行的完整價格，進入訊號鏈即為未來函數入口"
    )


def test_feature_modules_exist():
    """護欄若因模組名改動而失效會靜默通過，故先確認守備對象確實存在。"""
    present = {
        m for m in FEATURE_MODULES if os.path.exists(os.path.join(REPO_ROOT, f"{m}.py"))
    }
    assert present == FEATURE_MODULES, (
        f"缺少模組 {sorted(FEATURE_MODULES - present)}——"
        "護欄的守備對象不存在時，這個測試會假性通過"
    )


def test_baseline_fixture_frozen():
    """基準檔必須存在且非空——它是 SC-011 唯一的比對對象。"""
    assert os.path.exists(BASELINE_PATH), "基準未凍結，SC-011 無從驗證"
    baseline = load_baseline()
    assert baseline["trades"], "基準無交易——無法偵測交易層的行為改變"
    assert baseline["equity_curve"], "基準無權益曲線"


def test_daily_production_path_unchanged():
    """SC-011：日線生產路徑的逐筆、逐根、逐欄輸出與本案實作前完全相同。"""
    baseline = load_baseline()
    current = run_production_daily()

    assert current["summary"] == baseline["summary"], "summary 與基準不同"
    assert current["trades_columns"] == baseline["trades_columns"], "交易欄位集合改變"
    assert current["equity_columns"] == baseline["equity_columns"], "權益欄位集合改變"
    assert len(current["trades"]) == len(baseline["trades"]), "交易筆數改變"
    assert len(current["equity_curve"]) == len(baseline["equity_curve"]), "權益根數改變"

    for i, (cur, base) in enumerate(zip(current["trades"], baseline["trades"])):
        assert cur == base, f"第 {i} 筆交易與基準不同：\n現行 {cur}\n基準 {base}"
    for i, (cur, base) in enumerate(
        zip(current["equity_curve"], baseline["equity_curve"])
    ):
        assert cur == base, f"第 {i} 根權益與基準不同：\n現行 {cur}\n基準 {base}"


def test_baseline_is_regenerable_and_deterministic():
    """基準本身必須可重現，否則它偵測到的「改變」可能只是它自己的雜訊。"""
    a = json.dumps(run_production_daily(), sort_keys=True)
    b = json.dumps(run_production_daily(), sort_keys=True)
    assert a == b, "生產路徑本身非確定性——基準比對失去意義"
