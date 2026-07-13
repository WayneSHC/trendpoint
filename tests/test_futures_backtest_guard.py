# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 008a US3 — 期貨回測雙層 fail-fast 護欄（SC-004）。

引擎層（BacktestEngine.run_backtest 的 asset_class 護欄）與入口/引擎方法對
futures instrument 皆拋明確錯誤、零績效數字；equity 正常回測不受影響。
"""

import pytest

from acceptance_fixtures import make_klines
from backtester import BacktestEngine, assert_backtestable, FuturesBacktestNotSupportedError
from instruments import AssetClass


def test_assert_backtestable_rejects_futures_and_allows_equity():
    with pytest.raises(FuturesBacktestNotSupportedError):
        assert_backtestable("futures")
    with pytest.raises(FuturesBacktestNotSupportedError):
        assert_backtestable(AssetClass.FUTURES)
    # equity 不拋（字串與 enum 皆可）
    assert_backtestable("equity")
    assert_backtestable(AssetClass.EQUITY)


def test_engine_guard_rejects_futures():
    df = make_klines(300, freq="5min")
    eng = BacktestEngine()
    with pytest.raises(FuturesBacktestNotSupportedError):
        eng.run_backtest(df, asset_class="futures", verbose=False)


def test_engine_equity_default_unaffected():
    df = make_klines(300, freq="5min")
    res = BacktestEngine().run_backtest(df, verbose=False)  # 預設 equity
    assert "summary" in res and "trades" in res


def test_portfolio_engine_guard_rejects_futures():
    from portfolio_backtester import PortfolioBacktester
    pb = PortfolioBacktester()
    pb.tickers = ["TXF"]  # config.instruments 內的 futures instrument
    with pytest.raises(FuturesBacktestNotSupportedError):
        pb.run_portfolio_backtest()
