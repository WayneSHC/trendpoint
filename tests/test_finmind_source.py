# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""spec 010 — FinMind 驗證源解析與 token 語意（SC-003/004 支柱）。全離線。"""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from data_sources.finmind_source import FinMindAdapter, MissingTokenError
from instruments import AssetClass, ContractSpec, Instrument

FIXTURE = Path(__file__).parent / "fixtures" / "finmind_sample.json"

TXF = Instrument(
    id="TXF", asset_class=AssetClass.FUTURES, source="finmind",
    contract=ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0),
)


def test_parse_fixture_matches_taifex_anchor():
    """FinMind 樣本解析 → raw schema；錨定列與 TAIFEX fixture 同日同值（同源鏡像實證）。"""
    records = json.loads(FIXTURE.read_text())["data"]
    df = FinMindAdapter._parse(records)
    assert set(df.columns) >= {"date", "contract", "open", "high", "low",
                               "close", "volume", "settlement", "open_interest"}
    row = df[(df["date"] == pd.Timestamp("2023-07-03")) & (df["contract"] == "202307")]
    assert len(row) == 1
    r = row.iloc[0]
    assert (r["open"], r["high"], r["low"], r["close"]) == (16901.0, 17038.0, 16897.0, 17035.0)
    assert r["volume"] == 75504.0 and r["settlement"] == 17035.0 and r["open_interest"] == 84331.0
    # 僅月契約、無重複
    assert df["contract"].str.fullmatch(r"\d{6}").all()
    assert not df.duplicated(subset=["date", "contract"]).any()


def test_missing_token_failfast(monkeypatch):
    monkeypatch.delenv("FINMIND_TOKEN", raising=False)
    ad = FinMindAdapter()
    with pytest.raises(MissingTokenError):
        ad.fetch_raw(TXF, "daily", date(2023, 7, 1), date(2023, 7, 31))
