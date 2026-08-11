# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
買進持有對照基準（`benchmark.py`）。

本檔守三件事：
1. **成本不得為零**（憲章原則 II）——一個零成本的對照組會系統性高估機會成本。
2. **期貨的名目值走未調整價**（spec 011 FR-004），缺欄硬失敗不 fallback。
3. **會計語意與回測引擎對齊**——第 1 根開盤進場、末根收盤出場，兩邊都付摩擦。
"""

import math

import numpy as np
import pandas as pd
import pytest

from acceptance_fixtures import make_klines, with_unadj
from benchmark import buy_and_hold, compare_to_benchmark, format_benchmark_line
from config.config import FuturesCostConfig
from instruments import ContractSpec
from trading_costs import (EquityCostModel, EquitySizer, FuturesCostModel,
                           PositionSizer)

TXC = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)
BACKADJUST_OFFSET = 5000.0


class OneLotSizer(PositionSizer):
    """固定 1 口——使 sizing 對價格中立，隔離出「分母/稅基用哪組價」這一個變因。"""

    def size(self, equity: float, price: float) -> float:
        return 1.0

    def partial_units(self, held: float, fraction: float) -> float:
        return float(math.floor(held * fraction))


def _equity_components():
    # 費率取 config/config.yaml 的 trading_cost 現值（憲章 V：費率唯一來源）
    return (EquityCostModel(commission_rate=0.001425, tax_rate=0.003, slippage_rate=0.0005),
            EquitySizer(commission_rate=0.001425, lot_size=1000))


def _futures_components():
    return FuturesCostModel(TXC, FuturesCostConfig()), OneLotSizer()


# ---------------------------------------------------------------------------
# 現貨
# ---------------------------------------------------------------------------

def test_equity_buy_and_hold_is_available_and_costed():
    df = make_klines(400, freq="5min")
    cm, sz = _equity_components()
    bm = buy_and_hold(df, 1_000_000.0, cm, sz)

    assert bm["available"] is True
    assert bm["shares"] > 0
    # 憲章原則 II：績效數字必含摩擦成本
    assert bm["total_costs"] > 0.0, "對照組零成本會系統性高估機會成本"
    assert np.isfinite(bm["total_return"])
    assert bm["max_drawdown"] <= 0.0


def test_equity_buy_and_hold_tracks_price_change_net_of_costs():
    """總報酬應約等於（出場價/進場價 − 1）× 部位佔比，再扣成本。

    不追求逐分逐毫相等——sizing 取整張、部分資金留現金——但方向與量級必須對得上，
    否則就不是「買進持有」而是別的東西。
    """
    df = make_klines(400, freq="5min")
    cm, sz = _equity_components()
    bm = buy_and_hold(df, 1_000_000.0, cm, sz)

    gross = bm["exit_price"] / bm["entry_price"] - 1.0
    invested = bm["shares"] * bm["entry_price"]
    expected = (gross * invested - bm["total_costs"]) / 1_000_000.0
    assert bm["total_return"] == pytest.approx(expected, abs=1e-6)


def test_equity_buy_and_hold_exposure_is_near_full():
    """曝險近 100%——這正是它不可與低曝險策略直接比總報酬的原因。"""
    df = make_klines(400, freq="5min")
    cm, sz = _equity_components()
    bm = buy_and_hold(df, 1_000_000.0, cm, sz)
    assert bm["exposure"] > 0.99


def test_unaffordable_capital_reports_unavailable_not_zero():
    """資金不足要**明說不可用**，不得回傳 0% 報酬——那會被讀成「對照組打平」。"""
    df = make_klines(400, freq="5min")
    cm, sz = _equity_components()
    bm = buy_and_hold(df, 1.0, cm, sz)
    assert bm["available"] is False
    assert "不足" in bm["reason"]


@pytest.mark.parametrize("n", [0, 1, 2])
def test_too_short_series_reports_unavailable(n):
    df = make_klines(400, freq="5min").iloc[:n]
    cm, sz = _equity_components()
    assert buy_and_hold(df, 1_000_000.0, cm, sz)["available"] is False


# ---------------------------------------------------------------------------
# 期貨：spec 011 的名目值紅線
# ---------------------------------------------------------------------------

def test_futures_requires_unadjusted_columns():
    """缺 unadj_* 時硬失敗——以調整後價當名目值會讓口數與稅基錯得離譜。"""
    df = make_klines(400, freq="5min")          # 刻意不呼叫 with_unadj
    cm, sz = _futures_components()
    with pytest.raises(ValueError, match="unadj"):
        buy_and_hold(df, 10_000_000.0, cm, sz, point_value=200.0, is_futures=True)


def test_futures_tax_base_uses_unadjusted_price():
    """調整後價固定、只平移未調整價 → 摩擦成本必須隨之改變（稅是 ad-valorem）。

    判別測試：若成本改用調整後價，兩次執行的 total_costs 會完全相同。
    """
    adj = make_klines(400, freq="5min")
    cm, sz = _futures_components()

    a = buy_and_hold(with_unadj(adj), 10_000_000.0, cm, sz,
                     point_value=200.0, is_futures=True)

    shifted = adj.copy()
    for col in ("open", "high", "low", "close"):
        shifted[f"unadj_{col}"] = shifted[col] + BACKADJUST_OFFSET
    b = buy_and_hold(shifted, 10_000_000.0, cm, sz,
                     point_value=200.0, is_futures=True)

    assert b["total_costs"] > a["total_costs"], "稅基未隨未調整價變動——疑似用了調整後價"
    # 損益本身不受影響（back-adjust 是平移、保留點差），故差異僅來自成本
    delta_costs = b["total_costs"] - a["total_costs"]
    delta_equity = (a["total_return"] - b["total_return"]) * 10_000_000.0
    assert delta_equity == pytest.approx(delta_costs, abs=1e-6)


def test_futures_pnl_uses_point_value():
    """期貨損益 = 口數 × Δ調整後價 × 乘數，不是股數 × 價格。"""
    df = with_unadj(make_klines(400, freq="5min"))
    cm, sz = _futures_components()
    bm = buy_and_hold(df, 10_000_000.0, cm, sz, point_value=200.0, is_futures=True)

    gross = (bm["exit_price"] - bm["entry_price"]) * bm["shares"] * 200.0
    expected = (gross - bm["total_costs"]) / 10_000_000.0
    assert bm["total_return"] == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# 呈現層
# ---------------------------------------------------------------------------

def test_format_line_always_shows_exposure():
    """曝險必須出現在同一行——只印報酬會誘導出不公平的比較。"""
    df = make_klines(400, freq="5min")
    cm, sz = _equity_components()
    line = format_benchmark_line(buy_and_hold(df, 1_000_000.0, cm, sz))
    assert "曝險" in line and "Sharpe" in line


def test_format_line_states_reason_when_unavailable():
    line = format_benchmark_line({"available": False, "reason": "測試原因"})
    assert "測試原因" in line


def test_compare_returns_deltas_without_verdict():
    """只給差額、不下判定——孰優孰劣取決於曝險與資金用途，那是使用者的決定。"""
    bm = {"available": True, "total_return": 0.5, "max_drawdown": -0.3,
          "sharpe_ratio": 0.4, "calmar_ratio": 0.2}
    d = compare_to_benchmark(
        {"total_return": 0.1, "max_drawdown": -0.1, "sharpe_ratio": 0.2, "calmar_ratio": 0.1},
        bm)
    assert d["d_total_return"] == pytest.approx(-0.4)
    assert d["d_max_drawdown"] == pytest.approx(0.2)   # 正值 = 回撤較淺
    assert set(d) == {"d_total_return", "d_max_drawdown", "d_sharpe", "d_calmar"}


def test_compare_returns_none_when_benchmark_unavailable():
    assert compare_to_benchmark({"total_return": 0.1}, {"available": False}) is None
