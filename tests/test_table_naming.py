# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 008a US2 — 集中表命名與白名單 regex（SC-003 / SC-001 parity）。
"""

import pytest

from instruments import equity_instrument, Instrument, AssetClass, ContractSpec
# spec 008b：futures instrument 必帶 ContractSpec（TX 權威值 200/1/20）
_C = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)

from db_security import table_name_for, TABLE_NAME_PATTERN, validate_table_name


def test_equity_table_name_identical_to_legacy():
    """equity 表名與 008a 前 f\"stock_{clean}_{tf}\" 逐字元相同（否則讀不到既有資料）。"""
    for tk in ["2330.TW", "0050.TW", "00878.TW", "00919.TW", "00631L.TW"]:
        for tf in ["daily", "5m"]:
            legacy = f"stock_{tk.replace('.', '_')}_{tf}"
            assert table_name_for(equity_instrument(tk), tf) == legacy


def test_futures_table_name_namespace():
    fut = Instrument(id="TXF", asset_class=AssetClass.FUTURES, source="mock", contract=_C)
    assert table_name_for(fut, "daily") == "fut_TXF_daily"
    assert table_name_for(fut, "5m") == "fut_TXF_5m"


def test_regex_accepts_both_namespaces():
    assert TABLE_NAME_PATTERN.match("stock_2330_TW_daily")
    assert TABLE_NAME_PATTERN.match("fut_TXF_5m")


def test_regex_rejects_illegal():
    for bad in ("evil; DROP TABLE", "stock_2330_TW_weekly", "../etc/passwd", "options_TXO_daily"):
        assert not TABLE_NAME_PATTERN.match(bad)
        with pytest.raises(ValueError):
            validate_table_name(bad)
