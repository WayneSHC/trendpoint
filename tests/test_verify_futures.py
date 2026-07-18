# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""spec 010 — 雙源交叉驗證器（SC-004）。全離線（注入 fake adapters）。"""

from datetime import date

import pandas as pd
import pytest

from data_sources.finmind_source import MissingTokenError
from instruments import AssetClass, ContractSpec, Instrument
from verify_futures_data import cross_verify

TXF = Instrument(
    id="TXF", asset_class=AssetClass.FUTURES, source="taifex",
    contract=ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0),
)


def _raw(rows):
    return pd.DataFrame([
        {"date": pd.Timestamp(d), "contract": c, "open": o, "high": o + 2,
         "low": o - 2, "close": o + 1, "volume": v, "settlement": o + 1,
         "open_interest": 1000.0}
        for d, c, o, v in rows
    ])


class _Fake:
    def __init__(self, frame=None, exc=None):
        self.frame = frame
        self.exc = exc

    def fetch_raw(self, instrument, timeframe, start, end):
        if self.exc:
            raise self.exc
        return self.frame


_ROWS = [("2023-07-03", "202307", 16901.0, 75504.0),
         ("2023-07-04", "202307", 16950.0, 70000.0)]


def test_identical_sources_pass():
    rep = cross_verify(date(2023, 7, 1), date(2023, 7, 31), tolerance=0.0,
                       taifex=_Fake(_raw(_ROWS)), finmind=_Fake(_raw(_ROWS)),
                       instrument=TXF)
    assert not rep.skipped and rep.passed and rep.total_rows == 2


def test_injected_divergence_flags_row():
    fm = _raw(_ROWS)
    fm.loc[fm["date"] == pd.Timestamp("2023-07-04"), "close"] += 5.0   # 注入超差
    rep = cross_verify(date(2023, 7, 1), date(2023, 7, 31), tolerance=0.0,
                       taifex=_Fake(_raw(_ROWS)), finmind=_Fake(fm), instrument=TXF)
    assert not rep.passed
    m = rep.mismatches
    assert len(m) == 1
    r = m.iloc[0]
    assert r["field"] == "close" and r["diff"] == pytest.approx(5.0)
    assert r["taifex"] == pytest.approx(16951.0) and r["finmind"] == pytest.approx(16956.0)


def test_tolerance_absorbs_small_diff():
    fm = _raw(_ROWS)
    fm.loc[0, "volume"] += 0.5
    rep = cross_verify(date(2023, 7, 1), date(2023, 7, 31), tolerance=1.0,
                       taifex=_Fake(_raw(_ROWS)), finmind=_Fake(fm), instrument=TXF)
    assert rep.passed


def test_missing_token_skips_without_raise():
    rep = cross_verify(date(2023, 7, 1), date(2023, 7, 31), tolerance=0.0,
                       taifex=_Fake(_raw(_ROWS)),
                       finmind=_Fake(exc=MissingTokenError("no token")),
                       instrument=TXF)
    assert rep.skipped and "no token" in rep.reason and not rep.passed


def test_finmind_http_error_skips():
    rep = cross_verify(date(2023, 7, 1), date(2023, 7, 31), tolerance=0.0,
                       taifex=_Fake(_raw(_ROWS)),
                       finmind=_Fake(exc=ConnectionError("boom")), instrument=TXF)
    assert rep.skipped and "boom" in rep.reason
