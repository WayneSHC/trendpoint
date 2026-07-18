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
    from acceptance_fixtures import with_unadj
    df = with_unadj(make_klines(300, freq="5min"))   # spec 011：需未調整參考價欄位
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


# ---------------------------------------------------------------------------
# spec 011（SC-007 / FR-008）：期貨路徑缺未調整參考價 → 硬失敗，無沉默 fallback
# ---------------------------------------------------------------------------

def test_futures_without_unadjusted_columns_raises():
    """缺 unadj_* 的期貨資料必須明確拋錯並提示重建，不得退回使用調整後價。

    這是 spec 010 遺留舊資料表的樣態（僅 6 欄）。若允許 fallback，保證金
    低估的 bug 會無聲重現，而其症狀（爆倉）極易被誤判為策略問題。
    """
    from acceptance_fixtures import make_klines
    from config.config import FuturesCostConfig
    from instruments import ContractSpec
    from trading_costs import FuturesCostModel, FuturesSizer

    df = make_klines(120, freq="5min")            # 無 unadj_* 欄位
    txc = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)
    cfg = FuturesCostConfig()

    with pytest.raises(ValueError, match="unadj"):
        BacktestEngine(initial_capital=10_000_000.0).run_backtest(
            df, asset_class="futures",
            cost_model=FuturesCostModel(txc, cfg), sizer=FuturesSizer(txc, cfg),
            point_value=200.0, verbose=False,
        )


def test_futures_error_message_names_missing_columns_and_remedy():
    """錯誤訊息須可據以行動：指出缺哪些欄位、如何重建。"""
    from acceptance_fixtures import make_klines
    from config.config import FuturesCostConfig
    from instruments import ContractSpec
    from trading_costs import FuturesCostModel, FuturesSizer

    df = make_klines(120, freq="5min")
    txc = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)
    with pytest.raises(ValueError) as exc:
        BacktestEngine(initial_capital=10_000_000.0).run_backtest(
            df, asset_class="futures",
            cost_model=FuturesCostModel(txc, FuturesCostConfig()),
            sizer=FuturesSizer(txc, FuturesCostConfig()),
            point_value=200.0, verbose=False,
        )
    msg = str(exc.value)
    assert "unadj_open" in msg and "unadj_close" in msg
    assert "run_ingestion" in msg, "訊息應告知重建方式"


def test_equity_without_unadjusted_columns_unaffected():
    """FR-008 作用域：現貨不消費此欄位，其資料框不得被要求具備（不受牽連）。"""
    from acceptance_fixtures import make_klines

    df = make_klines(120, freq="5min")
    res = BacktestEngine(initial_capital=1_000_000.0).run_backtest(
        df, asset_class="equity", verbose=False)
    assert "trades" in res, "現貨路徑應照常執行"
