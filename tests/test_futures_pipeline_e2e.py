# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 008a US1 — 期貨資料端到端（SC-002）。

註冊一個 futures（mock，含 rollover 跳空）instrument，走 adapter→驗證→存
（`fut_*` 表）→載，斷言得到符合資料契約的連續 OHLCV。**不需真 TAIFEX 資料**。
"""

import pandas as pd

from config import DataQualityConfig
from instruments import Instrument, AssetClass
from data_sources import get_adapter
from data_ingestion import validate_data_contract
from db_security import table_name_for, safe_save_to_sqlite, safe_load_db_data


def test_futures_pipeline_end_to_end(tmp_path):
    inst = Instrument(id="TXF", asset_class=AssetClass.FUTURES, source="mock",
                      display_name="台指期近月（mock）")

    # 1) adapter 取得連續序列（含 rollover 跳空）
    df = get_adapter(inst.source).fetch(inst, "daily")
    assert len(df) > 0

    # 2) asset-aware 資料契約驗證（futures 門檻）
    quality = DataQualityConfig()
    assert validate_data_contract(df, asset_class=inst.asset_class, quality=quality) is True

    # 3) 存入 SQLite（futures 表命名空間）
    db = str(tmp_path / "futures_test.db")
    table = table_name_for(inst, "daily")
    assert table == "fut_TXF_daily"
    assert safe_save_to_sqlite(df, table, db) is True

    # 4) 載回並驗證資料契約成立
    loaded = safe_load_db_data(db, table)
    assert isinstance(loaded, pd.DataFrame) and len(loaded) == len(df)
    assert isinstance(loaded.index, pd.DatetimeIndex) and loaded.index.is_monotonic_increasing
    assert (loaded["close"] > 0).all() and (loaded["volume"] >= 0).all()
    # 往返一致（收盤價）
    pd.testing.assert_series_equal(
        loaded["close"].reset_index(drop=True),
        df["close"].reset_index(drop=True),
        check_names=False,
    )


def test_futures_rollover_gap_present():
    """mock 刻意含一段 rollover 跳空——驗證抽象在『真形狀』資料上受測。"""
    inst = Instrument(id="TXF", asset_class=AssetClass.FUTURES, source="mock")
    df = get_adapter("mock").fetch(inst, "daily")
    max_jump = df["close"].pct_change().abs().max()
    assert max_jump > 0.03, "mock 應含一段明顯跳空（rollover 怪癖）"
