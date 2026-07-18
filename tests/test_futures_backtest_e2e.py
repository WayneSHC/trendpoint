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
    # spec 011：期貨路徑要求未調整參考價欄位；合成序列無 back-adjust，
    # 故 unadj_* = 原價（with_unadj 只補缺欄，不覆寫刻意構造的情境）
    from acceptance_fixtures import with_unadj
    df = with_unadj(df)
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
    from acceptance_fixtures import with_unadj
    df = with_unadj(make_klines(300, freq="5min"))   # spec 011：合成序列 unadj_* = 原價
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
    # 崩跌改寫後同步未調整欄位（合成序列無 back-adjust，兩組價格恆等）
    for _c in ("open", "high", "low", "close"):
        crashed[f"unadj_{_c}"] = crashed[_c]
    return crashed, k


def test_futures_blowup_terminates_and_flags():
    """FR-011：權益 ≤ 0 當根強制結清、權益曲線截止、summary 標記爆倉。"""
    from acceptance_fixtures import with_unadj
    df, entry_k = _crash_after_entry_klines()
    df = with_unadj(df)          # spec 011：合成序列無 back-adjust，unadj_* = 原價
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


# ---------------------------------------------------------------------------
# spec 011（SC-001 / FR-004）：sizing 以未調整價計名目值
#
# 複刻 spec 010 T017 的真實故障：back-adjust 使早年調整後價位遠低於當年真實價
# （TXF 實測 1999-06-21 調整後 188 vs 未調整 8,439，約 45 倍），保證金因而被
# 低估數十倍、口數暴增至 463 口，正常波動即觸發爆倉護欄。
# ---------------------------------------------------------------------------

def _backadjusted_like(n=300, ratio=45.0):
    """未調整價維持真實水準，調整後價被壓縮至 1/ratio（模擬早年 back-adjust 位移）。"""
    from acceptance_fixtures import with_unadj
    base = make_klines(n, freq="5min")
    df = with_unadj(base)
    shift = float(base["close"].mean()) * (1.0 - 1.0 / ratio)
    for col in ("open", "high", "low", "close"):
        df[col] = base[col] - shift          # 等量平移：訊號形狀不變、水位大降
    return df


def test_sizing_uses_unadjusted_price_not_backadjusted_level():
    """FR-004：口數以未調整收盤價手算可驗，不隨 back-adjust 位移暴增。"""
    df = _backadjusted_like()
    cfg = FuturesCostConfig()
    res = _run_futures(df, TXC, FuturesSizer(TXC, cfg))
    buys = res["trades"]
    buys = buys[buys["action"] == "BUY"]
    assert not buys.empty, "fixture 應觸發進場"
    first = buys.iloc[0]
    k = df.index.get_loc(first["datetime"])

    # sizing 基準取訊號根未調整收盤
    assert first["sizing_price"] == pytest.approx(float(df["unadj_close"].iloc[k - 1]))

    # 口數 = floor(權益 × 使用率 ÷ 每口保證金)，以未調整價計
    per_lot = first["sizing_price"] * TXC.point_value * cfg.margin_rate
    expected = float(int(10_000_000.0 * cfg.margin_utilization / per_lot))
    assert first["shares"] == pytest.approx(expected)

    # 若誤用調整後價，口數會是約 ratio 倍——確認沒有落入該分支
    wrong_per_lot = float(df["close"].iloc[k - 1]) * TXC.point_value * cfg.margin_rate
    wrong = float(int(10_000_000.0 * cfg.margin_utilization / wrong_per_lot))
    assert first["shares"] < wrong / 10.0, (
        f"口數 {first['shares']} 接近以調整後價計得的 {wrong}——sizing 仍用錯基準")


def test_negative_backadjusted_price_does_not_break_sizing():
    """Edge case：調整後價穿零（TXF 實測 2,259 根 ≤ 0）時，sizing/保證金仍健全。"""
    from acceptance_fixtures import with_unadj
    base = make_klines(300, freq="5min")
    df = with_unadj(base)
    for col in ("open", "high", "low", "close"):
        df[col] = base[col] - float(base["high"].max()) - 10.0   # 全序列為負

    assert (df["close"] < 0).all(), "fixture 應使調整後價全為負"
    res = _run_futures(df, TXC, FuturesSizer(TXC, FuturesCostConfig()))
    buys = res["trades"]
    buys = buys[buys["action"] == "BUY"]
    if not buys.empty:
        assert (buys["shares"] > 0).all(), "口數不得為 0 或負"
        assert (buys["margin_used"] > 0).all(), "保證金不得為負（負價位曾導致負保證金）"
        assert np.isfinite(buys["shares"]).all()
