# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 008b — 期貨端到端回測（SC-004 + SC-006 + FR-011）。

US1（T007）：常數 1 口 sizer（sizing 中立）——回測跑通、成本非零、long-only。
US2（T014）：工廠 margin sizer——整數口全程、保證金約束、無 NaN、爆倉終止。
序列用 tests/acceptance_fixtures.make_klines（會觸發進場的確定性 5min 序列，視為期貨點數）。
"""

import math

import numpy as np
import pandas as pd
import pytest

from acceptance_fixtures import make_klines
from backtester import BacktestEngine
from config.config import FuturesCostConfig, load_config
from instruments import AssetClass, ContractSpec, Instrument
from trading_costs import FuturesCostModel, FuturesSizer, PositionSizer, for_asset_class

TXC = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)
MTXC = ContractSpec(point_value=50.0, tick_size=1.0, exchange_fee_per_lot=12.5)


class OneLotSizer(PositionSizer):
    """US1 用：固定 1 口（sizing 中立，隔離成本層驗證）。"""

    def size(self, equity: float, price: float) -> float:
        return 1.0

    def partial_units(self, held: float, fraction: float) -> float:
        return float(math.floor(held * fraction))


def _run_futures(df, contract, sizer, init_capital=10_000_000.0):
    eng = BacktestEngine(initial_capital=init_capital)
    return eng.run_backtest(
        df,
        asset_class="futures",
        cost_model=FuturesCostModel(contract, FuturesCostConfig()),
        sizer=sizer,
        point_value=contract.point_value,
        verbose=False,
    )


# ---------------------------------------------------------------------------
# US1（T007）：常數口 e2e——SC-004 初步 + SC-006
# ---------------------------------------------------------------------------

def test_futures_e2e_constant_lot_runs_with_nonzero_costs():
    df = make_klines(300, freq="5min")
    res = _run_futures(df, TXC, OneLotSizer())  # 不拋 FuturesBacktestNotSupportedError
    trades = res["trades"]
    assert not trades.empty, "fixture 應觸發至少一筆交易"
    # 摩擦成本非零（憲章 II：禁止零成本績效）
    total_friction = (trades["commission"] + trades["tax"]).sum()
    assert total_friction > 0.0
    # 期貨進場邊也要有期交稅（兩邊各收）
    buys = trades[trades["action"] == "BUY"]
    assert (buys["tax"] > 0.0).all()
    # long-only：僅 BUY / SELL_HALF / SELL_ALL
    assert set(trades["action"]).issubset({"BUY", "SELL_HALF", "SELL_ALL"})


def test_futures_costs_match_component_math():
    """e2e 成交紀錄的成本欄位與元件公式逐筆吻合（成本層無引擎側偏差）。"""
    df = make_klines(300, freq="5min")
    res = _run_futures(df, TXC, OneLotSizer())
    cm = FuturesCostModel(TXC, FuturesCostConfig())
    for _, row in res["trades"].iterrows():
        costs = cm.entry_costs(row["price"], row["shares"]) if row["action"] == "BUY" \
            else cm.exit_costs(row["price"], row["shares"])
        assert row["commission"] == pytest.approx(costs.commission)
        assert row["tax"] == pytest.approx(costs.tax)


# ---------------------------------------------------------------------------
# US2（T014）：margin sizer 完整 e2e——SC-004 完整 + FR-011 爆倉
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("contract", [TXC, MTXC], ids=["TXF", "MTX"])
def test_futures_e2e_margin_sizer_full(contract):
    df = make_klines(300, freq="5min")
    cfg = FuturesCostConfig()
    sizer = FuturesSizer(contract, cfg)
    res = _run_futures(df, contract, sizer)
    trades, curve = res["trades"], res["equity_curve"]
    assert not trades.empty
    # 全程口數為非負整數
    lots = trades["shares"]
    assert ((lots >= 0) & (lots == np.floor(lots))).all()
    # 權益曲線無 NaN
    assert not curve["equity"].isna().any()
    # 進場紀錄含佔用保證金（>0），且口數×每口保證金 = 佔用保證金
    for _, buy in trades[trades["action"] == "BUY"].iterrows():
        assert buy["margin_used"] > 0.0
        assert buy["margin_used"] == pytest.approx(
            sizer.margin_per_lot(buy["sizing_price"]) * buy["shares"])
    # summary 含爆倉旗標且此情境未爆倉
    assert res["summary"].get("blown_up") is False


def _crash_after_entry_klines():
    """極端下跌 fixture（analyze M2）：以 make_klines 觸發首筆進場，
    進場根之後改寫為連續 −10%/根 崩跌——高槓桿下權益必然歸零。確定性。"""
    df = make_klines(300, freq="5min")
    # 探測首筆進場位置（確定性：同資料同參數必同結果）
    cfg = FuturesCostConfig(margin_utilization=1.0)
    probe = BacktestEngine(initial_capital=3_000_000.0).run_backtest(
        df, asset_class="futures",
        cost_model=FuturesCostModel(TXC, cfg),
        sizer=FuturesSizer(TXC, cfg),
        point_value=TXC.point_value, verbose=False,
    )
    buys = probe["trades"]
    assert not buys.empty, "make_klines 應觸發進場（既有 fixture 已驗證會產生交易）"
    entry_time = buys[buys["action"] == "BUY"].iloc[0]["datetime"]
    k = df.index.get_loc(entry_time)
    # 進場根之後（k+1 起）改寫為崩跌：訊號只用過去資料 → 進場行為不變
    crashed = df.copy()
    price = float(df["close"].iloc[k])
    for j in range(k + 1, len(df)):
        price *= 0.90
        crashed.iloc[j, crashed.columns.get_loc("open")] = price / 0.90 * 0.95
        crashed.iloc[j, crashed.columns.get_loc("close")] = price
        crashed.iloc[j, crashed.columns.get_loc("high")] = price * 1.01
        crashed.iloc[j, crashed.columns.get_loc("low")] = price * 0.99
    return crashed, k


def test_futures_blowup_terminates_and_flags():
    """FR-011：權益 ≤ 0 當根強制結清、權益曲線截止、summary 標記爆倉。"""
    df, entry_k = _crash_after_entry_klines()
    # 使用率 100% ≈ 18 倍槓桿：單根 −10% → 權益必 < 0
    cfg = FuturesCostConfig(margin_utilization=1.0)
    res = BacktestEngine(initial_capital=3_000_000.0).run_backtest(
        df, asset_class="futures",
        cost_model=FuturesCostModel(TXC, cfg),
        sizer=FuturesSizer(TXC, cfg),
        point_value=TXC.point_value, verbose=False,
    )
    assert res["summary"]["blown_up"] is True
    curve = res["equity_curve"]
    # 權益曲線截止於爆倉當根（早於資料尾端）、末根權益 ≤ 0（強制結清後現金）
    assert len(curve) < len(df) - 1
    assert curve["equity"].iloc[-1] <= 0.0
    # 強制結清：最後動作為 SELL_ALL（爆倉事件）且無殘留持倉
    trades = res["trades"]
    last = trades.iloc[-1]
    assert last["action"] == "SELL_ALL" and "爆倉" in last["event"]
