# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 測試前提檢查（conftest 的 tickers_with_data fixture）單元測試

「需要真實 trendpoint.db」的測試靠這個 fixture 決定該跑還是該跳過。
它一旦誤判，整套 pytest 的綠燈就不可信（該跳過卻失敗，或該失敗卻靜默跳過），
故本身需要測試涵蓋。
"""

import os
import sqlite3
from contextlib import closing

import pandas as pd

from db_security import safe_save_to_sqlite, table_name_for
from instruments import equity_instrument


def _make_ohlcv(n: int = 3) -> pd.DataFrame:
    """建立最小可寫入的日線 frame（前提檢查只看「有無資料列」，不看欄位內容）。"""
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
        index=idx,
    )


def _save_daily(df: pd.DataFrame, ticker: str, db_path: str) -> None:
    """經正式寫入路徑落表，確保表名與前提檢查的導出結果必然一致。"""
    safe_save_to_sqlite(df, table_name_for(equity_instrument(ticker), "daily"), db_path)


def test_tickers_with_data_ignores_alert_only_db(tmp_path, tickers_with_data):
    """
    僅含 sent_alerts 的資料庫必須判為「無資料」。

    這是本前提檢查存在的理由：monitor_signals.init_sent_alerts_db() 為了去重表
    而 sqlite3.connect(DB_PATH)，sqlite3 會順手把 trendpoint.db 建出來。舊版
    guard 只看檔案存在，因此在跑過 `python monitor_signals.py --once` 的乾淨
    工作區會誤判為可執行，最終以 ValueError 失敗而非跳過。
    """
    db_path = str(tmp_path / "trendpoint.db")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "CREATE TABLE sent_alerts ("
            "ticker TEXT, bar_time TEXT, alert_type TEXT, sent_time TEXT)"
        )
        conn.commit()

    assert os.path.exists(db_path), "前提：檔案確實存在（舊 guard 於此誤判可執行）"
    assert tickers_with_data(db_path, ["2330.TW"]) == []


def test_tickers_with_data_detects_populated_table(tmp_path, tickers_with_data):
    """有非空日線表的標的才算「有資料」，且僅回報實際存在者。"""
    db_path = str(tmp_path / "trendpoint.db")
    _save_daily(_make_ohlcv(), "2330.TW", db_path)

    assert tickers_with_data(db_path, ["2330.TW", "0050.TW"]) == ["2330.TW"]


def test_tickers_with_data_ignores_empty_table(tmp_path, tickers_with_data):
    """
    表存在但無資料列時，_load_and_calculate_indicators 會 df.empty → continue，
    故前提檢查也必須判為無資料——兩者判準必須完全一致，否則就是舊 bug 的變形。
    """
    db_path = str(tmp_path / "trendpoint.db")
    _save_daily(_make_ohlcv().iloc[0:0], "2330.TW", db_path)

    assert tickers_with_data(db_path, ["2330.TW"]) == []


def test_tickers_with_data_does_not_create_db_file(tmp_path, tickers_with_data):
    """
    前提檢查本身不得產生副作用檔案。sqlite3.connect 會建檔，若 guard 直接連線，
    它就會變成自己要防的那顆地雷（在乾淨工作區留下空的 trendpoint.db）。
    """
    db_path = str(tmp_path / "trendpoint.db")

    assert tickers_with_data(db_path, ["2330.TW"]) == []
    assert not os.path.exists(db_path), "前提檢查不得建出資料庫檔案"
