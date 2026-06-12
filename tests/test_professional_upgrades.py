"""
Range Navigator - 專業化升級功能測試 (pytest)

涵蓋:
1. 整股單位限制 (round lot)
2. 績效指標模組 (Sharpe / Sortino / Calmar / MDD / 曝險)
3. ADX / Kaufman ER / 市況濾網
4. Walk-Forward 切分與參數高原檢查
5. 波動率倒數加權
6. 蒙地卡羅交易重抽（可重現性與分布合理性）
7. 還原股價抓取參數
"""

import numpy as np
import pandas as pd
import pytest

from backtester import BacktestEngine
from ladder_system import (
    calculate_adx,
    calculate_efficiency_ratio,
    calculate_regime_filter,
    PositionManager,
)
from performance import compute_performance_metrics, infer_periods_per_year, max_drawdown_stats
from monte_carlo import bootstrap_trades
from walk_forward import WalkForwardAnalyzer


# =========================================================================
# 測試數據生成
# =========================================================================

def _make_trending_df(n_bars: int = 400, seed: int = 7, freq: str = "1D", base: float = 100.0) -> pd.DataFrame:
    """
    產生帶上升趨勢的模擬 OHLCV 日線數據
    """
    np.random.seed(seed)
    dates = pd.date_range(start="2024-01-01", periods=n_bars, freq=freq)
    changes = np.random.normal(0.15, 1.0, n_bars)
    prices = base + np.cumsum(changes)
    prices = np.maximum(prices, 1.0)

    df = pd.DataFrame(index=dates)
    df.index.name = "datetime"
    df['close'] = prices
    df['open'] = df['close'].shift(1).fillna(base)
    df['high'] = df[['open', 'close']].max(axis=1) * 1.005
    df['low'] = df[['open', 'close']].min(axis=1) * 0.995
    df['volume'] = np.random.uniform(1000, 5000, n_bars).round()
    return df


# =========================================================================
# 1. 整股單位限制
# =========================================================================

def test_round_to_lot():
    engine = BacktestEngine(lot_size=1000)
    assert engine.round_to_lot(10237.98) == 10000.0
    assert engine.round_to_lot(999.9) == 0.0
    assert engine.round_to_lot(1000.0) == 1000.0
    assert engine.round_to_lot(2999.99) == 2000.0

    # lot_size=1 等同零股，無取整
    engine_odd = BacktestEngine(lot_size=1)
    assert engine_odd.round_to_lot(10237.98) == 10237.98


def test_backtest_shares_are_lot_multiples():
    """
    回測產生的所有買賣股數必須為整股單位之倍數
    """
    df = _make_trending_df(400)
    engine = BacktestEngine(initial_capital=1000000.0, lot_size=1000)
    res = engine.run_backtest(df, use_adx_filter=False, use_ma_filter=False, verbose=False)
    trades = res["trades"]

    if trades.empty:
        pytest.skip("模擬數據未產生交易")

    for _, row in trades.iterrows():
        assert row["shares"] % 1000 == 0, f"{row['action']} 股數 {row['shares']} 非整股倍數"
        assert row["shares"] > 0, "交易日誌不應記錄零股數交易"


def test_insufficient_capital_skips_entry():
    """
    資金買不起一張時不得進場（防止無限分割股數的高估）
    """
    df = _make_trending_df(400, base=20000.0)  # 一張 2000 萬
    engine = BacktestEngine(initial_capital=1000000.0, lot_size=1000)
    res = engine.run_backtest(df, use_adx_filter=False, use_ma_filter=False, verbose=False)
    assert len(res["trades"]) == 0, "資金不足一張仍產生了交易"
    # 淨值應全程持平於初始資金
    assert abs(res["equity_curve"]["equity"].iloc[-1] - 1000000.0) < 1e-6


# =========================================================================
# 2. 績效指標模組
# =========================================================================

def test_performance_metrics_basic():
    """
    以已知特性的淨值曲線驗證指標方向正確性
    """
    dates = pd.date_range("2024-01-01", periods=252, freq="1D")
    # 穩定上漲 20%、無回撤的淨值
    equity = pd.Series(np.linspace(1000000, 1200000, 252), index=dates)

    m = compute_performance_metrics(equity, initial_capital=1000000.0)
    assert abs(m["total_return"] - 0.2) < 1e-9
    assert m["max_drawdown"] == 0.0
    assert m["sharpe_ratio"] > 0
    assert m["cagr"] > 0.15


def test_max_drawdown_stats():
    dates = pd.date_range("2024-01-01", periods=5, freq="1D")
    equity = pd.Series([100.0, 120.0, 90.0, 95.0, 130.0], index=dates)
    stats = max_drawdown_stats(equity)
    # 從 120 跌至 90 → -25%
    assert abs(stats["max_drawdown"] - (-0.25)) < 1e-9
    assert stats["max_underwater_bars"] == 2


def test_infer_periods_per_year():
    daily_idx = pd.date_range("2024-01-01", periods=10, freq="1D")
    assert infer_periods_per_year(daily_idx) == 252.0

    intraday_idx = pd.date_range("2024-01-01 09:00", periods=10, freq="5min")
    assert infer_periods_per_year(intraday_idx) > 252.0


def test_exposure_calculation():
    dates = pd.date_range("2024-01-01", periods=4, freq="1D")
    equity = pd.Series([100.0, 101.0, 102.0, 103.0], index=dates)
    position = pd.Series([0.0, 50.0, 50.0, 0.0], index=dates)
    m = compute_performance_metrics(equity, 100.0, position_value=position)
    assert abs(m["exposure"] - 0.5) < 1e-9


# =========================================================================
# 3. ADX / ER / 市況濾網
# =========================================================================

def test_adx_distinguishes_trend_from_chop():
    """
    單邊趨勢數據的 ADX 必須顯著高於來回震盪數據
    """
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="1D")

    # 強趨勢：每日上漲 1
    trend = pd.DataFrame(index=dates)
    trend['close'] = 100.0 + np.arange(n)
    trend['open'] = trend['close'] - 0.5
    trend['high'] = trend['close'] + 0.5
    trend['low'] = trend['open'] - 0.5
    trend['volume'] = 1000.0

    # 盤整：在 100 附近鋸齒震盪
    chop = pd.DataFrame(index=dates)
    chop['close'] = 100.0 + np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    chop['open'] = 100.0
    chop['high'] = chop[['open', 'close']].max(axis=1) + 0.5
    chop['low'] = chop[['open', 'close']].min(axis=1) - 0.5
    chop['volume'] = 1000.0

    adx_trend = calculate_adx(trend).iloc[-1]
    adx_chop = calculate_adx(chop).iloc[-1]

    assert adx_trend > 25, f"強趨勢 ADX 應 > 25，實際 {adx_trend:.1f}"
    assert adx_chop < 20, f"盤整 ADX 應 < 20，實際 {adx_chop:.1f}"
    assert adx_trend > adx_chop


def test_efficiency_ratio_bounds_and_direction():
    n = 100
    dates = pd.date_range("2024-01-01", periods=n, freq="1D")

    # 完美單邊：ER 應接近 1
    straight = pd.Series(100.0 + np.arange(n, dtype=float), index=dates)
    er_straight = calculate_efficiency_ratio(straight).iloc[-1]
    assert er_straight > 0.99

    # 完美鋸齒：ER 應接近 0
    zigzag = pd.Series(100.0 + np.where(np.arange(n) % 2 == 0, 1.0, 0.0), index=dates)
    er_zigzag = calculate_efficiency_ratio(zigzag).iloc[-1]
    assert er_zigzag < 0.2

    # 邊界
    er_all = calculate_efficiency_ratio(straight)
    assert (er_all >= 0.0).all() and (er_all <= 1.0).all()


def test_regime_filter_blocks_below_ma():
    """
    價格跌破長均線時，市況濾網必須擋下做多
    """
    n = 300
    dates = pd.date_range("2024-01-01", periods=n, freq="1D")
    # 前半上漲、後半崩跌至遠低於均線
    closes = np.concatenate([100.0 + np.arange(150), 250.0 - np.arange(150) * 1.5])
    df = pd.DataFrame(index=dates)
    df['close'] = closes
    df['open'] = df['close']
    df['high'] = df['close'] + 1.0
    df['low'] = df['close'] - 1.0
    df['volume'] = 1000.0

    ok = calculate_regime_filter(df, use_adx=False, use_ma=True, ma_period=100)
    # 末段價格深陷均線之下，濾網必須為 False
    assert not ok.iloc[-1]
    # 上漲段中濾網應放行
    assert ok.iloc[140]


def test_entry_signal_disabled_filters():
    """
    消融開關：停用的維度直接視為通過
    """
    pm = PositionManager()
    base_kwargs = dict(
        close=105.0, open_val=100.0, daily_open=99.0, vwap=98.0,
        atr=1.0, candle_high=106.0, candle_low=99.0,
        structure_sig=0,  # 結構不通過
        global_filter_ok=True, is_daily=True
    )
    assert pm.check_entry_signal(**base_kwargs) is False
    assert pm.check_entry_signal(**base_kwargs, disabled_filters=frozenset(['structure'])) is True


# =========================================================================
# 4. Walk-Forward
# =========================================================================

def test_walk_forward_runs_and_reports():
    df = _make_trending_df(500)
    engine = BacktestEngine(initial_capital=1000000.0, lot_size=1)
    analyzer = WalkForwardAnalyzer(
        engine=engine,
        atr_periods=[10, 14],
        ladder_ks=[1.5, 2.0],
        backtest_kwargs=dict(use_adx_filter=False, use_ma_filter=False)
    )
    result = analyzer.run(df, n_folds=3)

    assert len(result["folds"]) > 0, "Walk-Forward 未產生任何有效折"
    for f in result["folds"]:
        assert "test_return" in f
        assert f["best_params"]["atr_period"] in [10, 14]
        assert f["best_params"]["ladder_k"] in [1.5, 2.0]

    # 樣本外淨值必須存在且以 1.0 起步
    assert result["oos_equity"] is not None
    assert abs(result["oos_equity"].iloc[0] - 1.0) < 0.05

    # 參數穩定度結構
    ps = result["param_stability"]
    assert ps["n_folds"] == len(result["folds"])


def test_walk_forward_rejects_short_data():
    df = _make_trending_df(50)
    analyzer = WalkForwardAnalyzer(engine=BacktestEngine(lot_size=1))
    with pytest.raises(ValueError):
        analyzer.run(df, n_folds=4)


def test_parameter_plateau_detection():
    """
    孤峰（鄰居 Calmar 遠低於最佳值）必須被標記為過擬合警訊
    """
    grid = []
    for ap in [10, 14, 18]:
        for k in [1.5, 2.0, 2.5]:
            calmar = 10.0 if (ap == 14 and k == 2.0) else 0.1  # 孤峰
            grid.append({"atr_period": ap, "ladder_k": k, "calmar": calmar,
                         "total_return": 0.1, "max_drawdown": -0.1, "total_trades": 10})
    best = [c for c in grid if c["calmar"] == 10.0][0]
    report = WalkForwardAnalyzer.check_parameter_plateau(grid, best)
    assert report["is_plateau"] is False, "孤峰未被偵測為過擬合警訊"

    # 健康高原：鄰居與最佳值相近
    grid_flat = [dict(c, calmar=9.0) for c in grid]
    best_flat = grid_flat[4]
    report_flat = WalkForwardAnalyzer.check_parameter_plateau(grid_flat, best_flat)
    assert report_flat["is_plateau"] is True


# =========================================================================
# 5. 波動率倒數加權
# =========================================================================

def test_inverse_vol_weights():
    """
    高波動標的的權重必須低於低波動標的，且權重總和接近 1、受上限約束
    """
    from portfolio_backtester import PortfolioBacktester

    pb = PortfolioBacktester.__new__(PortfolioBacktester)
    pb.tickers = ["LOW_VOL", "HIGH_VOL"]
    pb.allocation = "inverse_vol"
    pb.max_weight = 0.8

    n = 10
    dates = pd.date_range("2024-01-01", periods=n, freq="1D")
    low = pd.DataFrame({"realized_vol": [0.01] * n}, index=dates)
    high = pd.DataFrame({"realized_vol": [0.04] * n}, index=dates)

    weights = pb._compute_weights({"LOW_VOL": low, "HIGH_VOL": high}, i=5)

    assert weights["LOW_VOL"] > weights["HIGH_VOL"], "低波動標的權重應較高"
    # 1/0.01 : 1/0.04 = 4 : 1 → 0.8 : 0.2
    assert abs(weights["LOW_VOL"] - 0.8) < 1e-6
    assert abs(weights["HIGH_VOL"] - 0.2) < 1e-6


def test_inverse_vol_falls_back_to_equal_when_missing():
    from portfolio_backtester import PortfolioBacktester

    pb = PortfolioBacktester.__new__(PortfolioBacktester)
    pb.tickers = ["A", "B"]
    pb.allocation = "inverse_vol"
    pb.max_weight = 0.5

    n = 10
    dates = pd.date_range("2024-01-01", periods=n, freq="1D")
    a = pd.DataFrame({"realized_vol": [np.nan] * n}, index=dates)  # 數據不足
    b = pd.DataFrame({"realized_vol": [0.02] * n}, index=dates)

    weights = pb._compute_weights({"A": a, "B": b}, i=5)
    assert abs(weights["A"] - 0.5) < 1e-9
    assert abs(weights["B"] - 0.5) < 1e-9


def test_equal_allocation_mode():
    from portfolio_backtester import PortfolioBacktester

    pb = PortfolioBacktester.__new__(PortfolioBacktester)
    pb.tickers = ["A", "B", "C", "D"]
    pb.allocation = "equal"
    pb.max_weight = 0.5

    weights = pb._compute_weights({}, i=0)
    for t in pb.tickers:
        assert abs(weights[t] - 0.25) < 1e-9


# =========================================================================
# 6. 蒙地卡羅交易重抽
# =========================================================================

def test_monte_carlo_reproducible_and_sane():
    trades = [0.02, -0.01, 0.03, -0.02, 0.05, -0.015, 0.01, 0.04, -0.03, 0.02] * 4

    r1 = bootstrap_trades(trades, n_sims=500, seed=42)
    r2 = bootstrap_trades(trades, n_sims=500, seed=42)
    assert r1["total_return"] == r2["total_return"], "相同種子的結果必須可重現"

    # 分布單調性：5 百分位 <= 中位 <= 95 百分位
    tr = r1["total_return"]
    assert tr[5] <= tr[50] <= tr[95]
    # 回撤必為非正值
    assert r1["max_drawdown"][95] <= 0.0 or abs(r1["max_drawdown"][95]) < 1e-12
    assert r1["max_drawdown"][5] <= r1["max_drawdown"][50]


def test_monte_carlo_warns_on_small_sample():
    r = bootstrap_trades([0.01, -0.02, 0.03], n_sims=100, seed=1)
    assert "warning" in r, "交易樣本 < 30 筆必須給出統計可靠性警告"


def test_monte_carlo_empty_trades():
    r = bootstrap_trades([], n_sims=100)
    assert r["n_source_trades"] == 0
    assert "warning" in r


# =========================================================================
# 7. 還原股價
# =========================================================================

def test_fetch_uses_adjusted_prices_by_default(monkeypatch):
    """
    fetch_stock_data 預設必須以 auto_adjust=True 呼叫 yfinance
    """
    import data_ingestion

    captured = {}

    def fake_download(tickers, period, interval, progress, auto_adjust):
        captured["auto_adjust"] = auto_adjust
        return pd.DataFrame()  # 空值會讓函式提前返回 None

    monkeypatch.setattr(data_ingestion.yf, "download", fake_download)
    data_ingestion.fetch_stock_data("0050.TW")
    assert captured["auto_adjust"] is True
