# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - CSV 資料來源 adapter (spec 008a)。

從 `{base_dir}/{clean_id}_{timeframe}.csv` 讀入連續 OHLCV（供已有匯出檔的資產
接入）。CSV 須含 datetime 欄（或首欄為時間）與 OHLCV。
"""

import pandas as pd

from .base import DataSourceAdapter
from . import register_adapter

_OHLCV = ["open", "high", "low", "close", "volume"]


def _clean_id(instrument_id: str) -> str:
    return instrument_id.replace(".", "_").replace("/", "_")


class CsvAdapter(DataSourceAdapter):
    source_key = "csv"

    def __init__(self, base_dir: str = "data"):
        self.base_dir = base_dir

    def fetch(self, instrument, timeframe: str) -> pd.DataFrame:
        path = f"{self.base_dir}/{_clean_id(instrument.id)}_{timeframe}.csv"
        df = pd.read_csv(path)
        dt_col = "datetime" if "datetime" in df.columns else df.columns[0]
        df[dt_col] = pd.to_datetime(df[dt_col])
        df = df.set_index(dt_col).sort_index()
        df.index.name = "datetime"
        df.columns = [c.lower() for c in df.columns]
        return df[_OHLCV]


register_adapter(CsvAdapter())
