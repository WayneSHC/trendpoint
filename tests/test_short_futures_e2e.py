# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 003 — 空方期貨端到端回測（SC-001 + SC-006）。

fixture = 翻轉之 make_klines（analyze M2：鏡像對稱成立則必觸發空方進場，自足確定）。
成本/口數 = 008b 期貨模型原樣（margin sizer）；空方爆倉由急漲觸發。
"""

import numpy as np
import pandas as pd
import pytest

from acceptance_fixtures import make_klines
from backtester import BacktestEngine
from config.config import FuturesCostConfig
from instruments import ContractSpec
from trading_costs import FuturesCostModel, FuturesSizer
from test_short_side import mirror_klines

TXC = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)


def _run_short(df, cfg=None, init_capital=10_000_000.0):
    cfg = cfg or FuturesCostConfig()
    eng = BacktestEngine(initial_capital=init_capital)
    return eng.run_backtest(
        df, asset_class="futures",
        cost_model=FuturesCostModel(TXC, cfg),
        sizer=FuturesSizer(TXC, cfg),
        point_value=TXC.point_value,
        enable_short=True, verbose=False,
    )


def test_short_e2e_margin_sizer():
    df = mirror_klines(make_klines(300, freq="5min"))
    res = _run_short(df)
    trades = res["trades"]
    shorts = trades[trades["action"] == "SELL_SHORT"]
    assert not shorts.empty, "翻轉序列應觸發空方進場（SC-001）"
    # 最終回補離場（無殘留持倉）
    assert (trades["action"] == "COVER_ALL").any()
    # 成本非零兩邊（進場邊亦含期交稅）
    assert (shorts["tax"] > 0.0).all() and (shorts["commission"] > 0.0).all()
    covers = trades[trades["action"].isin(["COVER_HALF", "COVER_ALL"])]
    assert (covers["tax"] > 0.0).all() and (covers["commission"] > 0.0).all()
    # 口數全程非負整數
    lots = trades["shares"]
    assert ((lots >= 0) & (lots == np.floor(lots))).all()
    # 無借券費欄位（期貨原生做空）
    assert not any("borrow" in c or "借券" in c for c in trades.columns)
    # 權益曲線無 NaN
    assert not res["equity_curve"]["equity"].isna().any()


def test_short_e2e_deterministic():
    df = mirror_klines(make_klines(300, freq="5min"))
    r1, r2 = _run_short(df), _run_short(df)
    pd.testing.assert_frame_equal(r1["trades"], r2["trades"])
    assert r1["summary"]["total_return"] == r2["summary"]["total_return"]


def test_short_blowup_on_rally_terminates_and_flags():
    """SC-006：空頭持倉遇急漲 → 權益 ≤ 0 當根強制回補、曲線截止、標記爆倉。"""
    df = mirror_klines(make_klines(300, freq="5min"))
    cfg = FuturesCostConfig(margin_utilization=1.0)   # 高槓桿使爆倉可達
    probe = _run_short(df)
    shorts = probe["trades"][probe["trades"]["action"] == "SELL_SHORT"]
    assert not shorts.empty
    entry_time = shorts.iloc[0]["datetime"]
    k = df.index.get_loc(entry_time)
    # 進場根之後嫁接 +10%/根急漲（訊號只用過去 → 進場行為不變）
    rallied = df.copy()
    price = float(df["close"].iloc[k])
    for j in range(k + 1, len(df)):
        price *= 1.10
        rallied.iloc[j, rallied.columns.get_loc("open")] = price / 1.10 * 1.05
        rallied.iloc[j, rallied.columns.get_loc("close")] = price
        rallied.iloc[j, rallied.columns.get_loc("high")] = price * 1.01
        rallied.iloc[j, rallied.columns.get_loc("low")] = price * 0.99

    res = _run_short(rallied, cfg=cfg, init_capital=3_000_000.0)
    assert res["summary"]["blown_up"] is True
    curve = res["equity_curve"]
    assert len(curve) < len(df) - 1          # 曲線截止（提前終止）
    assert curve["equity"].iloc[-1] <= 0.0
    last = res["trades"].iloc[-1]
    assert last["action"] == "COVER_ALL" and "爆倉" in last["event"]
