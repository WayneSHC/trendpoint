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

# 時框 → interval。日線的 period 來自 config `data.equity_history_period`（憲章 V，
# 不硬編碼）；5 分線固定 5 天——那是 yfinance 對 5m 的上限，不是可調策略參數。
_INTERVAL = {"daily": "1d", "5m": "5m"}
_FIVE_MIN_PERIOD = "5d"


class YfinanceAdapter(DataSourceAdapter):
    source_key = "yfinance"

    def __init__(self, cfg=None):
        # 沿用 taifex_source 的惰性載入慣例：模組匯入時就讀 config 會讓
        # 測試無法注入，且 adapter 在 registry 中是單例。
        self._cfg = cfg

    def _period_for(self, timeframe: str) -> str:
        if timeframe == "5m":
            return _FIVE_MIN_PERIOD
        cfg = self._cfg
        if cfg is None:
            from config import load_config
            cfg = load_config().data
        return cfg.equity_history_period

    def fetch(self, instrument, timeframe: str):
        interval = _INTERVAL.get(timeframe, "1d")
        df = fetch_stock_data(ticker=instrument.id,
                              period=self._period_for(timeframe),
                              interval=interval)
        if df is None:
            raise RuntimeError(f"yfinance 取得 {instrument.id} [{timeframe}] 失敗")
        return df


register_adapter(YfinanceAdapter())
