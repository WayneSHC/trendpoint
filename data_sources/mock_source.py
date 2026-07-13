# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - Mock 資料來源 adapter (spec 008a)。

產生確定性（依 instrument.id 播種）的連續 OHLCV 日線序列，**刻意含一段 rollover
跳空**（模擬期貨換月），供期貨資料管線端到端驗證，無需真 TAIFEX 資料。
"""

import numpy as np
import pandas as pd

from .base import DataSourceAdapter
from . import register_adapter


class MockAdapter(DataSourceAdapter):
    source_key = "mock"

    def fetch(self, instrument, timeframe: str) -> pd.DataFrame:
        seed = sum(ord(c) for c in instrument.id) or 1
        rng = np.random.default_rng(seed)
        n = 300
        idx = pd.bdate_range("2020-01-02", periods=n, name="datetime")
        rets = rng.normal(0.0002, 0.01, n)
        rets[150] += 0.06  # rollover 跳空（換月缺口約 6%，realistic 且低於 equity 3.0 門檻）
        close = 15000.0 * np.exp(np.cumsum(rets))
        open_ = np.empty(n)
        open_[0] = close[0]
        open_[1:] = close[:-1]
        span = rng.uniform(0.0, 0.006, n)
        high = np.maximum(open_, close) * (1.0 + span)
        low = np.minimum(open_, close) * (1.0 - span)
        volume = np.round(rng.lognormal(9.0, 0.4, n)).astype(np.int64)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=idx,
        )


register_adapter(MockAdapter())
