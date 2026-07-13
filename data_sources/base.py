# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 資料來源 adapter 介面 (spec 008a)。

adapter 交付**已 rollover 拼接、已正規化**的連續 OHLCV（標準欄位 + datetime 索引、
時序遞增、正價、量≥0、因果）。框架保持資產類別無關；rollover/正規化為 adapter
內部責任。
"""

from abc import ABC, abstractmethod

import pandas as pd

from instruments import Instrument


class DataSourceAdapter(ABC):
    """資料來源抽象：依 `source_key` 註冊、以 `fetch` 交付連續 OHLCV。"""

    source_key: str = ""

    @abstractmethod
    def fetch(self, instrument: Instrument, timeframe: str) -> pd.DataFrame:
        """回傳連續 OHLCV：欄位 open/high/low/close/volume + DatetimeIndex('datetime')。"""
        raise NotImplementedError
