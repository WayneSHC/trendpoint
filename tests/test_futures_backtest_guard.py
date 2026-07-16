# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 008b US1 — 護欄語意反轉（SC-006，原 008a US3 護欄之退役）。

單標的路徑（BacktestEngine.run_backtest）對 futures **不再拋錯**（008b 已有成本/口數模型）；
**組合路徑護欄保留**（008b analyze H1：組合的期貨元件接入不在範圍，放行會使期貨
落入現股成本路徑、違反憲章 II）；equity 預設路徑不受影響。
"""

import pytest

from acceptance_fixtures import make_klines
from backtester import BacktestEngine, assert_backtestable, FuturesBacktestNotSupportedError
from config.config import FuturesCostConfig
from instruments import AssetClass, ContractSpec
from trading_costs import FuturesCostModel, FuturesSizer

_TXC = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)


def test_engine_no_longer_rejects_futures():
    """008b：引擎對 futures 正常回測（提供期貨元件時）。"""
    df = make_klines(300, freq="5min")
    cfg = FuturesCostConfig()
    res = BacktestEngine().run_backtest(
        df, asset_class="futures",
        cost_model=FuturesCostModel(_TXC, cfg),
        sizer=FuturesSizer(_TXC, cfg),
        point_value=_TXC.point_value, verbose=False,
    )
    assert "summary" in res and "trades" in res


def test_engine_equity_default_unaffected():
    df = make_klines(300, freq="5min")
    res = BacktestEngine().run_backtest(df, verbose=False)  # 預設 equity
    assert "summary" in res and "trades" in res


def test_assert_backtestable_still_rejects_for_portfolio_boundary():
    """assert_backtestable 函式保留拒絕語意——組合入口仍靠它擋期貨（H1 範圍護欄）。"""
    with pytest.raises(FuturesBacktestNotSupportedError):
        assert_backtestable("futures")
    with pytest.raises(FuturesBacktestNotSupportedError):
        assert_backtestable(AssetClass.FUTURES)
    assert_backtestable("equity")
    assert_backtestable(AssetClass.EQUITY)


def test_portfolio_engine_still_rejects_futures():
    """組合引擎層護欄保留：期貨進組合 → 拋錯（期貨組合接入待後續 spec）。"""
    from portfolio_backtester import PortfolioBacktester
    pb = PortfolioBacktester()
    pb.tickers = ["TXF"]  # config.instruments 內的 futures instrument
    with pytest.raises(FuturesBacktestNotSupportedError):
        pb.run_portfolio_backtest()
