# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
逐筆報酬率（`trade_returns`）的分母基準 —— spec 011 FR-004 的第三個落點。

## 本檔守什麼
`trade_returns` 的分母是「價位 × 乘數」型名目值，期貨必須用**未調整**價。
先前它用調整後的連續價，在 TXF 早年（back-adjust 後偏離真實市價約 45 倍、
穿零至 -5,312）令分母趨近 0 或變號，報酬率隨之爆量或翻號。

## 為什麼分子不在守備範圍
back-adjust 是平移，保留點差，`shares × Δ調整後價 × 乘數` 的 NT$ 損益正確。
壞的只有「拿單一絕對價位當分母」這一步——故本檔的判別式全部設計成
**分子固定、只有分母變**，若有人把分母改回調整後價，比例關係立刻不成立。
"""

import math

import numpy as np
import pandas as pd
import pytest

from acceptance_fixtures import make_klines, with_unadj
from backtester import BacktestEngine, _return_basis_price
from config.config import FuturesCostConfig
from instruments import ContractSpec
from monte_carlo import bootstrap_trades
from trading_costs import FuturesCostModel, PositionSizer

TXC = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)

# 模擬 back-adjust 的位移量：真實近月價 = 調整後價 + OFFSET。
# 取 5000 是為了讓兩者相差約 51 倍——真實 TXF 早年的量級（約 45 倍）。
BACKADJUST_OFFSET = 5000.0


class OneLotSizer(PositionSizer):
    """固定 1 口。sizing 對價格中立是本檔的關鍵前提：兩次執行的口數、
    進出場時點、點差損益全部相同，**只有分母的基準價不同**。"""

    def size(self, equity: float, price: float) -> float:
        return 1.0

    def partial_units(self, held: float, fraction: float) -> float:
        return float(math.floor(held * fraction))


def _run_futures(df):
    eng = BacktestEngine(initial_capital=10_000_000.0)
    return eng.run_backtest(
        df,
        asset_class="futures",
        cost_model=FuturesCostModel(TXC, FuturesCostConfig()),
        sizer=OneLotSizer(),
        point_value=TXC.point_value,
        verbose=False,
    )


def _paired_buy_basis(res):
    """取實際被配對的 BUY 列之分母（shares × sizing_price × 乘數 + 手續費）。

    末筆未平倉的 BUY 不進 trade_returns，故只取前 N 列。
    """
    trades = res["trades"]
    buys = trades[trades["action"] == "BUY"]
    n = len(res["summary"]["trade_returns"])
    buys = buys.iloc[:n]
    return (buys["shares"] * buys["sizing_price"] * TXC.point_value
            + buys["commission"]).to_numpy()


def _paired_buy_shares(res):
    trades = res["trades"]
    buys = trades[trades["action"] == "BUY"]
    return buys.iloc[:len(res["summary"]["trade_returns"])]["shares"].to_numpy()


# ---------------------------------------------------------------------------
# 判別式主體：分子相同、分母不同
# ---------------------------------------------------------------------------

def test_futures_trade_returns_scale_with_unadjusted_basis():
    """調整後價相同、未調整價平移 5000 → 報酬率必須按分母比例縮小。

    這是本檔的判別測試：若分母改回 `row['price']`（調整後價），兩次執行的
    報酬率會**完全相同**，比例斷言即失敗。
    """
    adj = make_klines(400, freq="5min")

    # A：無 back-adjust（unadj = adj），分母基準 ≈ 100
    res_a = _run_futures(with_unadj(adj))

    # B：真實近月價高 5000（unadj = adj + OFFSET），分母基準 ≈ 5100
    shifted = adj.copy()
    for col in ("open", "high", "low", "close"):
        shifted[f"unadj_{col}"] = shifted[col] + BACKADJUST_OFFSET
    res_b = _run_futures(shifted)

    ret_a = np.asarray(res_a["summary"]["trade_returns"], dtype=float)
    ret_b = np.asarray(res_b["summary"]["trade_returns"], dtype=float)
    assert len(ret_a) > 0, "fixture 應產生至少一筆完整配對"
    assert len(ret_a) == len(ret_b), "兩次執行的交易序列應一致（sizer 對價格中立）"

    basis_a = _paired_buy_basis(res_a)
    basis_b = _paired_buy_basis(res_b)
    # 分母確實隨未調整價放大（若讀的是調整後價，這裡就會是 1 倍）
    assert np.all(basis_b / basis_a > 40.0)

    # 分子（NT$ 損益）只差在期交稅：稅是 ad-valorem 且以**未調整**價為基礎
    #（spec 011 既有行為），故 B 的名目值高 OFFSET × 乘數，進出兩邊各多繳一次。
    # 手續費（每口定額）與滑價（tick 計）皆與價位無關，不入此式。
    shares = _paired_buy_shares(res_a)
    tax_delta = 2.0 * shares * BACKADJUST_OFFSET * TXC.point_value * FuturesCostConfig().tax_rate
    profit_a = ret_a * basis_a
    profit_b = ret_b * basis_b
    np.testing.assert_allclose(profit_b, profit_a - tax_delta, rtol=0, atol=1e-6)

    # 主張本體：分子固定下，報酬率完全由分母的基準價決定。
    np.testing.assert_allclose(ret_b, (profit_a - tax_delta) / basis_b, rtol=0, atol=1e-12)


def test_futures_trade_returns_stay_above_minus_one():
    """逐筆報酬率不得 <= -100%：單筆虧掉超過名目值需要價位翻負。

    這是 -567% 回撤深尾的上游不變量。
    """
    shifted = make_klines(400, freq="5min")
    for col in ("open", "high", "low", "close"):
        shifted[f"unadj_{col}"] = shifted[col] + BACKADJUST_OFFSET
    res = _run_futures(shifted)

    ret = np.asarray(res["summary"]["trade_returns"], dtype=float)
    assert len(ret) > 0
    assert ret.min() > -1.0, f"最小逐筆報酬率 {ret.min():.4f} 已穿破 -100%"

    # 下游：重抽不再拋輸入契約錯誤，且回撤深尾落在 [-100%, 0]
    mc = bootstrap_trades(ret.tolist(), n_sims=200, seed=42)
    assert -1.0 <= mc["max_drawdown"][5] <= 0.0


def test_equity_return_basis_unchanged():
    """現貨紀錄無 sizing_price 欄 → 分母退回成交價，與修正前逐位元相同。"""
    df = make_klines(400, freq="5min")
    eng = BacktestEngine(initial_capital=1_000_000.0)
    res = eng.run_backtest(df, verbose=False)

    trades = res["trades"]
    assert "sizing_price" not in trades.columns, "現貨紀錄不應出現期貨欄位"

    ret = res["summary"]["trade_returns"]
    assert len(ret) > 0
    buys = trades[trades["action"] == "BUY"].iloc[:len(ret)]
    basis = (buys["shares"] * buys["price"] + buys["commission"]).to_numpy()
    # 逐位元：×1.0 的 point_value 對正浮點恆等
    recomputed = np.asarray(ret, dtype=float) * basis
    assert np.all(np.isfinite(recomputed))
    assert np.all(np.asarray(ret, dtype=float) > -1.0)


# ---------------------------------------------------------------------------
# _return_basis_price 單元行為
# ---------------------------------------------------------------------------

def test_return_basis_price_prefers_unadjusted():
    row = pd.Series({"price": 3.5, "sizing_price": 5100.0})
    assert _return_basis_price(row) == 5100.0


def test_return_basis_price_falls_back_for_equity():
    row = pd.Series({"price": 3.5})
    assert _return_basis_price(row) == 3.5
    # 欄位存在但為 NaN（混合紀錄的防禦）亦退回成交價
    assert _return_basis_price(pd.Series({"price": 3.5, "sizing_price": np.nan})) == 3.5


@pytest.mark.parametrize("bad", [0.0, -5312.0])
def test_return_basis_price_rejects_nonpositive(bad):
    """未調整價恆為正；非正即資料層已壞 → 硬失敗而非靜默回退。"""
    with pytest.raises(ValueError, match="非正"):
        _return_basis_price(pd.Series({"price": 1.0, "sizing_price": bad}))


# ---------------------------------------------------------------------------
# monte_carlo 的輸入契約
# ---------------------------------------------------------------------------

def test_bootstrap_rejects_returns_at_or_below_minus_one():
    """複利路徑穿零會產出 -567% 這種不可能的回撤並照常回傳；改為硬失敗。"""
    with pytest.raises(ValueError, match="-100%"):
        bootstrap_trades([0.02, -0.03, -1.4, 0.05], n_sims=10)
    # 邊界：恰為 -100%（權益歸零）亦不可複利下去
    with pytest.raises(ValueError, match="-100%"):
        bootstrap_trades([0.02, -1.0], n_sims=10)


def test_bootstrap_accepts_normal_returns():
    res = bootstrap_trades([0.03] * 47 + [-0.15], n_sims=200, seed=42)
    assert res["n_source_trades"] == 48
    assert res["max_drawdown"][5] <= 0.0
