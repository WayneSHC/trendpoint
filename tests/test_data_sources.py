# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 008a US1 — 資料來源 adapter 介面契約與分派。

驗證：`get_adapter` 依 source 鍵分派、未知鍵 fail-fast；各 adapter 的 `fetch`
回傳符合資料契約的連續 OHLCV（欄位、datetime 遞增、正價、量≥0）。
"""

import pandas as pd
import pytest

from instruments import Instrument, AssetClass
from data_sources import get_adapter
from data_sources.base import DataSourceAdapter

_OHLCV = ["open", "high", "low", "close", "volume"]


def _assert_ohlcv_contract(df):
    assert isinstance(df, pd.DataFrame) and len(df) > 0
    assert list(df.columns[:5]) == _OHLCV or set(_OHLCV).issubset(df.columns)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    assert (df["close"] > 0).all() and (df["low"] > 0).all()
    assert (df["volume"] >= 0).all()


def test_get_adapter_dispatch_and_unknown_failfast():
    for key in ("mock", "csv", "yfinance"):
        a = get_adapter(key)
        assert isinstance(a, DataSourceAdapter) and a.source_key == key
    with pytest.raises(ValueError):
        get_adapter("no_such_source")


def test_mock_adapter_contract():
    inst = Instrument(id="TXF", asset_class=AssetClass.FUTURES, source="mock")
    df = get_adapter("mock").fetch(inst, "daily")
    _assert_ohlcv_contract(df)


def test_mock_adapter_deterministic():
    inst = Instrument(id="TXF", asset_class=AssetClass.FUTURES, source="mock")
    a = get_adapter("mock")
    pd.testing.assert_frame_equal(a.fetch(inst, "daily"), a.fetch(inst, "daily"))


def test_csv_adapter_contract(tmp_path):
    from data_sources.csv_source import CsvAdapter
    # 手工 CSV：datetime + OHLCV
    idx = pd.bdate_range("2021-01-04", periods=6, name="datetime")
    df0 = pd.DataFrame({
        "open": [100, 101, 102, 103, 104, 105],
        "high": [101, 102, 103, 104, 105, 106],
        "low": [99, 100, 101, 102, 103, 104],
        "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
        "volume": [1000, 1100, 1200, 1300, 1400, 1500],
    }, index=idx)
    df0.to_csv(tmp_path / "AAA_daily.csv")
    inst = Instrument(id="AAA", asset_class=AssetClass.FUTURES, source="csv")
    out = CsvAdapter(base_dir=str(tmp_path)).fetch(inst, "daily")
    _assert_ohlcv_contract(out)
    assert len(out) == 6
