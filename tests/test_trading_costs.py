# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 008b — 成本/口數元件單元測試。

SC-002：期貨每口成本數學（TAIFEX 權威錨定例）
SC-003：保證金/口數 sizing（含 FR-012 整數口部分出場）
SC-001 支柱：Equity 元件與現行內聯公式逐位元相同
錨定值皆於實作前在 spec/quickstart 手算定案（TX 單邊 100 NT$、保證金 220,000、口數 2/0）。
"""

import math
import random

import pytest

from config.config import FuturesCostConfig, load_config
from instruments import AssetClass, ContractSpec, Instrument
from trading_costs import (
    EquityCostModel,
    EquitySizer,
    FuturesCostModel,
    FuturesSizer,
    for_asset_class,
)

TX = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)
MTX = ContractSpec(point_value=50.0, tick_size=1.0, exchange_fee_per_lot=12.5)
TMF = ContractSpec(point_value=10.0, tick_size=1.0, exchange_fee_per_lot=8.0)
CFG = FuturesCostConfig()  # 權威預設：broker 0 / 稅 0.00002 / 滑價 1 tick / margin 5.5% / util 50%


# ---------------------------------------------------------------------------
# SC-002：期貨成本數學（T006）
# ---------------------------------------------------------------------------

def test_tx_single_side_anchor():
    """TX 1 口 @20,000 點單邊 = 定額 20 + 期交稅 20,000×200×0.00002=80 → 100 NT$。"""
    m = FuturesCostModel(TX, CFG)
    c = m.entry_costs(20000.0, 1.0)
    assert c.commission == pytest.approx(20.0)
    assert c.tax == pytest.approx(80.0)
    assert c.total == pytest.approx(100.0)


def test_both_sides_charged_same_formula():
    """期交稅與定額費**兩邊各收**：exit 與 entry 同式（來回 = 2 × 單邊）。"""
    m = FuturesCostModel(TX, CFG)
    e = m.entry_costs(20000.0, 1.0)
    x = m.exit_costs(20000.0, 1.0)
    assert e.commission == x.commission and e.tax == x.tax


def test_contract_scaling_mtx_tmf():
    """MTX/TMF 依乘數與定額費縮放。"""
    c_mtx = FuturesCostModel(MTX, CFG).entry_costs(20000.0, 1.0)
    assert c_mtx.commission == pytest.approx(12.5)
    assert c_mtx.tax == pytest.approx(20000.0 * 50.0 * 0.00002)  # 20
    c_tmf = FuturesCostModel(TMF, CFG).entry_costs(20000.0, 1.0)
    assert c_tmf.commission == pytest.approx(8.0)
    assert c_tmf.tax == pytest.approx(20000.0 * 10.0 * 0.00002)  # 4


def test_units_scale_linearly():
    m = FuturesCostModel(TX, CFG)
    c = m.entry_costs(20000.0, 3.0)
    assert c.commission == pytest.approx(60.0)
    assert c.tax == pytest.approx(240.0)


def test_slip_is_tick_offset_not_fee():
    """滑價 = 成交價 ±slippage_ticks×tick_size 點偏移（內含於價格，不重複計費）。"""
    m = FuturesCostModel(TX, CFG)
    assert m.slip(20000.0, "buy") == pytest.approx(20001.0)
    assert m.slip(20000.0, "sell") == pytest.approx(19999.0)
    cfg2 = FuturesCostConfig(slippage_ticks=2)
    m2 = FuturesCostModel(TX, cfg2)
    assert m2.slip(20000.0, "buy") == pytest.approx(20002.0)


def test_broker_commission_addon():
    """券商加收疊加於交易所定額（預設 0 = 權威下限）。"""
    cfg2 = FuturesCostConfig(broker_commission_per_lot=15.0)
    c = FuturesCostModel(TX, cfg2).entry_costs(20000.0, 1.0)
    assert c.commission == pytest.approx(35.0)


# ---------------------------------------------------------------------------
# SC-003：保證金/口數 sizing（T013，含 FR-012）
# ---------------------------------------------------------------------------

def test_margin_and_lots_anchor():
    """權益 1,000,000、收盤 20,000、TX：每口保證金 220,000 → floor(500,000/220,000)=2 口。"""
    s = FuturesSizer(TX, CFG)
    assert s.margin_per_lot(20000.0) == pytest.approx(220000.0)
    assert s.size(1_000_000.0, 20000.0) == 2.0


def test_insufficient_equity_zero_lots():
    """權益不足一口 → 0 口不進場（不拋錯）。"""
    s = FuturesSizer(TX, CFG)
    assert s.size(200_000.0, 20000.0) == 0.0
    assert s.size(0.0, 20000.0) == 0.0
    assert s.size(-50_000.0, 20000.0) == 0.0


def test_partial_units_floor_rule():
    """FR-012：部分平倉 = floor(口數×比例)；1 口→0（跳過平倉、風控照做）、3 口→1。"""
    s = FuturesSizer(TX, CFG)
    assert s.partial_units(1.0, 0.5) == 0.0
    assert s.partial_units(2.0, 0.5) == 1.0
    assert s.partial_units(3.0, 0.5) == 1.0


def test_lots_always_integer_valued():
    """口數任何時點為非負整數值（含 TMF 小口值粒度）。"""
    s = FuturesSizer(TMF, CFG)  # 每口保證金 = 20,000×10×0.055 = 11,000
    lots = s.size(1_000_000.0, 20000.0)
    assert lots == math.floor(lots) and lots >= 0
    assert lots == 45.0  # floor(500,000 / 11,000)


# ---------------------------------------------------------------------------
# SC-001 支柱：Equity 元件 = 現行內聯公式（T019）
# ---------------------------------------------------------------------------

def test_equity_components_bitwise_match_inline_formulas():
    """對隨機樣本，Equity 元件輸出與 backtester 現行內聯公式**逐位元**相同。"""
    COMM, TAX, SLIP, LOT = 0.001425, 0.003, 0.0005, 1000
    cm = EquityCostModel(COMM, TAX, SLIP)
    sz = EquitySizer(COMM, LOT)
    rng = random.Random(42)
    for _ in range(200):
        price = rng.uniform(5.0, 1500.0)
        units = float(rng.randrange(1000, 200000, 1000))
        equity = rng.uniform(1e5, 5e7)
        held = float(rng.randrange(1000, 100000, 1000))
        # 滑價（backtester.py: raw*(1±slip_rate)）
        assert cm.slip(price, "buy") == price * (1 + SLIP)
        assert cm.slip(price, "sell") == price * (1 - SLIP)
        # 進場費（fee = (shares*exec)*comm）
        assert cm.entry_costs(price, units).commission == (units * price) * COMM
        assert cm.entry_costs(price, units).tax == 0.0
        # 出場費（commission = revenue*comm、tax = revenue*tax）
        xc = cm.exit_costs(price, units)
        assert xc.commission == (units * price) * COMM
        assert xc.tax == (units * price) * TAX
        # sizing（round_to_lot(capital/(exec*(1.0+comm)))）
        expected = float(int((equity / (price * (1.0 + COMM))) // LOT) * LOT)
        assert sz.size(equity, price) == expected
        # 部分出場（round_to_lot(held*0.5)）
        assert sz.partial_units(held, 0.5) == float(int((held * 0.5) // LOT) * LOT)


# ---------------------------------------------------------------------------
# 工廠（SC-007 支柱：元件全自 config 建構）
# ---------------------------------------------------------------------------

def test_factory_dispatch_by_asset_class():
    cfg = load_config()
    cm, sz = for_asset_class(None, cfg)
    assert isinstance(cm, EquityCostModel) and isinstance(sz, EquitySizer)
    fut_insts = [i for i in cfg.data.instruments if i.asset_class == AssetClass.FUTURES]
    assert fut_insts, "config 應含 mock 期貨 instrument（TXF/MTX）"
    fcm, fsz = for_asset_class(fut_insts[0], cfg)
    assert isinstance(fcm, FuturesCostModel) and isinstance(fsz, FuturesSizer)
    # 元件費率確實來自 config（非硬編碼）：改 config 值 → 元件跟著變
    assert fcm.tax_rate == cfg.trading_cost.futures.tax_rate
    assert fsz.margin_rate == cfg.trading_cost.futures.margin_rate


def test_contract_required_for_futures_instrument():
    """Pydantic validator：futures 無 contract → fail-fast；equity 帶 contract → fail-fast。"""
    with pytest.raises(ValueError):
        Instrument(id="BAD", asset_class=AssetClass.FUTURES, source="mock")
    with pytest.raises(ValueError):
        Instrument(id="2330.TW", asset_class=AssetClass.EQUITY, contract=TX)
