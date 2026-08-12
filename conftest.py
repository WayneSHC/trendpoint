# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import List

import pytest

# Ensure the project root (directory containing this file) is in sys.path
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 專案模組必須等 sys.path 補完後才能 import，故不置於檔首
from db_security import table_name_for  # noqa: E402
from instruments import equity_instrument  # noqa: E402


def _tickers_with_data(db_path: str, tickers: List[str]) -> List[str]:
    """
    回傳在 db_path 中確實具備「非空日線資料表」的標的清單。

    判準刻意與回測引擎的真實前提完全一致（表存在 且 有資料列 → df.empty 為
    False，見 portfolio_backtester._load_and_calculate_indicators），如此前提
    檢查不會與引擎需求漂移；只要兩者判準不同，就會重現「該跳過卻失敗」的 bug：
    monitor_signals.init_sent_alerts_db() 為了去重表而 sqlite3.connect(DB_PATH)，
    sqlite3 順手把 trendpoint.db 建出來，只看檔案存在的 guard 便會誤判可執行。

    以唯讀模式開啟，並先檢查檔案存在——sqlite3.connect 對不存在的路徑會直接
    建檔，本函數不得產生任何副作用檔案（否則它就成了自己要防的那顆地雷）。
    """
    if not os.path.exists(db_path):
        return []

    # table_name_for 內部已過 db_security 的白名單正則（fail-fast），
    # 故此處拼接表名與 safe_load_db_data 的既有作法一致、無注入風險。
    wanted = {table_name_for(equity_instrument(t), "daily"): t for t in tickers}

    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
        existing = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        return [
            ticker
            for table, ticker in wanted.items()
            if table in existing
            and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
        ]


#: 正式的事後表現紀錄目錄。測試一律不得寫入此處。
_REAL_ALERT_LOG = project_root / "alert_log"


@pytest.fixture(autouse=True)
def _isolate_alert_log(tmp_path, monkeypatch):
    """把 spec 015 的觀察紀錄目錄改指到 tmp（**全測試自動套用**）。

    為什麼要在 conftest 做，而不是各測試自己傳 log_dir：

    `alert_log/` 是**進版本庫的原始觀察**——不可再生成，且 CI 會 `git add`
    它。任何走 monitor 真實路徑的測試，只要讀到正式組態，就會把
    `TEST.TW`、`close=80.0`、`bar_time=2024-02-29` 這種 fixture 列寫進去，
    然後被自動提交，混進真正的樣本裡再也分不出來。

    這個缺陷在總開關關閉時**完全不會顯現**——關閉時 recorder 不寫檔。它
    在啟用的那一刻才出現，而啟用正是 spec 015 的目的。實測（2026-08-12）
    五個測試檔會污染：`test_bos_volume_monitor`、`test_micro_index_instrument`、
    `test_monitor_short`、`test_monitor_signals`、`test_real_data_integration`。

    逐檔修不夠：第六個走同一條路徑的測試會再犯一次，而症狀是安靜的。
    故改在制度層一次擋掉，並由 `test_no_test_writes_to_real_alert_log`
    斷言此保護有效。
    """
    import monitor_signals

    # **要改的是 monitor 手上那一份，不是新載一份。** `load_config()` 每次
    # 呼叫都回一個新的 SystemConfig；改新載的那份等於改了一個沒人在用的
    # 物件，隔離看起來生效、實際完全沒作用。monitor 的組態是模組層的
    # `monitor_signals.cfg`，在 import 時就固定了。
    monkeypatch.setattr(
        monitor_signals.cfg.alerts.outcome_tracking,
        "log_dir",
        str(tmp_path / "alert_log"),
    )
    yield


@pytest.fixture(scope="session")
def tickers_with_data():
    """
    factory-as-fixture：回傳上述查詢函數本身，供「需要真實 trendpoint.db」的
    測試判斷前提是否成立（不成立就 pytest.skip，跳過與否的決定留在測試裡，
    以免略過的理由被藏進 fixture）。

    用法：
        def test_x(tickers_with_data):
            cfg = load_config()
            if not tickers_with_data(cfg.data.database_path, cfg.data.tickers):
                pytest.skip("...")
    """
    return _tickers_with_data
