# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - yfinance 資料來源 adapter (spec 008a)。

包裝現行 `fetch_stock_data`（內含 clean_kline_dataframe + validate_data_contract），
行為與 008a 前的現貨匯入路徑一致。
"""

from .base import DataSourceAdapter
from . import register_adapter
from data_ingestion import fetch_stock_data

# 時框 → (yfinance period, interval)，沿用 run_ingestion 之設定
_TIMEFRAME = {"daily": ("10y", "1d"), "5m": ("5d", "5m")}


class YfinanceAdapter(DataSourceAdapter):
    source_key = "yfinance"

    def fetch(self, instrument, timeframe: str):
        period, interval = _TIMEFRAME.get(timeframe, ("10y", "1d"))
        df = fetch_stock_data(ticker=instrument.id, period=period, interval=interval)
        if df is None:
            raise RuntimeError(f"yfinance 取得 {instrument.id} [{timeframe}] 失敗")
        return df


register_adapter(YfinanceAdapter())
