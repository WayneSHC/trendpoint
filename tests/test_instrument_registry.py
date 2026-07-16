# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 008a US2 — Instrument registry 解析與向後相容（SC-005）。
"""

import pytest

from instruments import InstrumentRegistry, Instrument, AssetClass, ContractSpec, equity_instrument
# spec 008b：futures instrument 必帶 ContractSpec（TX 權威值 200/1/20）
_C = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)



def test_plain_ticker_resolves_to_equity_yfinance():
    reg = InstrumentRegistry.from_config(["2330.TW"], [])
    inst = reg.resolve("2330.TW")
    assert inst.asset_class == AssetClass.EQUITY and inst.source == "yfinance"


def test_structured_instrument_parsed():
    fut = Instrument(id="TXF", asset_class=AssetClass.FUTURES, source="mock", contract=_C)
    reg = InstrumentRegistry.from_config([], [fut])
    assert reg.resolve("TXF").asset_class == AssetClass.FUTURES


def test_id_conflict_failfast():
    fut = Instrument(id="2330.TW", asset_class=AssetClass.FUTURES, source="mock", contract=_C)
    with pytest.raises(ValueError):
        InstrumentRegistry.from_config(["2330.TW"], [fut])  # ticker 與 instrument 撞名


def test_all_and_resolve_unknown():
    reg = InstrumentRegistry.from_config(["A", "B"], [])
    assert {i.id for i in reg.all()} == {"A", "B"}
    with pytest.raises(KeyError):
        reg.resolve("ZZZ")


def test_equity_instrument_backward_compat():
    inst = equity_instrument("0050.TW")
    assert inst.id == "0050.TW" and inst.asset_class == AssetClass.EQUITY
    assert inst.source == "yfinance" and "daily" in inst.timeframes
