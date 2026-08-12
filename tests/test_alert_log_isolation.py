# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""守門：測試不得寫入正式的事後表現紀錄目錄。

`alert_log/` 是**進版本庫的原始觀察**——不可再生成，且 `alert_scheduler.yml`
會 `git add alert_log/` 自動提交。測試若寫得進去，`TEST.TW`、`close=80.0`、
`bar_time=2024-02-29` 這類 fixture 列會被提交，混進真正的樣本裡再也分不出來，
而 spec 015 的整個目的就是累積那份樣本。

**這個缺陷在總開關關閉時完全不顯現**，因為關閉時 recorder 根本不寫檔。它在
啟用的那一刻才出現——而啟用正是 spec 015 的目的。2026-08-12 啟用時實測有五個
測試檔會污染：`test_bos_volume_monitor`、`test_micro_index_instrument`、
`test_monitor_short`、`test_monitor_signals`、`test_real_data_integration`。

隔離由 `conftest.py` 的 `_isolate_alert_log` autouse fixture 提供；本檔斷言
它有效。兩者刻意分開放：fixture 被拿掉時這裡要紅。
"""

import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_LOG_DIR = os.path.join(REPO_ROOT, "alert_log")


def _snapshot():
    if not os.path.isdir(REAL_LOG_DIR):
        return set()
    return set(os.listdir(REAL_LOG_DIR))


def test_monitor_config_points_away_from_the_real_dir():
    """monitor 手上那份組態的 log_dir 必須已被改指到 tmp。

    直接斷言 monitor 模組層的 `cfg`，而不是 `load_config()` 新載的一份：
    後者每次呼叫都回新物件，改它等於改一個沒人在用的東西。
    """
    import monitor_signals

    log_dir = monitor_signals.cfg.alerts.outcome_tracking.log_dir
    assert os.path.abspath(log_dir) != os.path.abspath(REAL_LOG_DIR)


def test_writing_through_monitor_config_leaves_real_dir_untouched():
    """走 monitor 的實際寫入路徑，正式目錄必須毫髮無傷。"""
    import alert_outcomes
    import monitor_signals

    before = _snapshot()
    log_dir = monitor_signals.cfg.alerts.outcome_tracking.log_dir
    record = alert_outcomes.make_record(
        ticker="TEST.TW",
        bar_time="2024-01-01",
        alert_type="BULLISH_BOS",
        timeframe="daily",
        bar=None,
        param_fingerprint="isolation-probe",
    )
    assert alert_outcomes.upsert_records(log_dir, [record]) == 1

    assert _snapshot() == before, "測試寫進了正式紀錄目錄"
    assert alert_outcomes.load_all(log_dir), "寫入應落在 tmp 沙箱且讀得回來"


@pytest.mark.parametrize(
    "module_name",
    [
        "test_bos_volume_monitor",
        "test_micro_index_instrument",
        "test_monitor_short",
        "test_monitor_signals",
        "test_real_data_integration",
    ],
)
def test_known_polluters_still_exist(module_name):
    """實測會污染的五個檔案仍在，否則上面的迴歸保護就失去對象。

    檔案改名或移除時這裡會紅，提醒回頭確認新位置是否仍受 fixture 保護。
    """
    assert os.path.exists(os.path.join(os.path.dirname(__file__), f"{module_name}.py"))
