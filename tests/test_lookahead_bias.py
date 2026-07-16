# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 防看前偏誤（Look-Ahead Bias）規格驗證模組 (pytest)

本測試專門驗證回測引擎的時序嚴格度，透過「歷史資料末梢篡改法」：
1. 傳入原始模擬時序數據 D1 執行回測，記錄交易紀錄 L1。
2. 傳入經修改末端數據（未來數據）的 D2 執行回測，記錄交易紀錄 L2。
3. 斷言在數據被篡改的時間戳 t 之前的交易訊號、進出場時間與價格，在 L1 與 L2 中必須完全一致。
以確保系統不具備未來函數，保證量化決策之誠實性。
"""

import pytest
import numpy as np
import pandas as pd
from backtester import BacktestEngine
from ladder_system import detect_market_structure

def _generate_mock_data(n_bars: int = 100) -> pd.DataFrame:
    """
    產生一組可重現的模擬 OHLCV 時序數據
    """
    np.random.seed(42)
    dates = pd.date_range(start="2026-05-25 09:00:00", periods=n_bars, freq="1min")
    
    # 產生多頭價格軌跡
    base_price = 20000.0
    price_changes = np.random.normal(5.0, 10.0, n_bars)
    prices = base_price + np.cumsum(price_changes)
    
    df = pd.DataFrame(index=dates)
    df['close'] = prices
    df['open'] = df['close'].shift(1).fillna(base_price - 5.0)
    df['high'] = df[['open', 'close']].max(axis=1) + 8.0
    df['low'] = df[['open', 'close']].min(axis=1) - 8.0
    df['volume'] = np.random.uniform(500, 2000, n_bars).round()
    
    return df

def test_no_lookahead_bias():
    """
    驗證回測引擎無看前偏誤
    """
    n_bars = 100
    df_original = _generate_mock_data(n_bars)
    
    # 建立回測引擎
    # lot_size=1：本測試驗證的是時序誠實性而非執行真實性，
    # 使用零股單位與停用市況濾網，確保 mock 數據能產生足夠的交易樣本。
    engine = BacktestEngine(
        initial_capital=1000000.0,
        commission_rate=0.001425,
        tax_rate=0.003,
        slippage_rate=0.0005,
        lot_size=1
    )

    common_kwargs = dict(
        atr_period=14,
        k=2.0,
        ch_period=22,
        ch_multiplier=3.0,
        time_limit=15,
        use_adx_filter=False,
        use_ma_filter=False
    )

    # 執行原始回測
    res_orig = engine.run_backtest(df_original, **common_kwargs)
    trades_orig = res_orig["trades"]

    # 建立篡改版 DataFrame：將第 80 根 K 線之後的所有價格大幅修改（未來巨變）
    df_modified = df_original.copy()
    split_idx = 80
    split_time = df_original.index[split_idx]

    df_modified.iloc[split_idx:, df_modified.columns.get_loc('close')] *= 2.0
    df_modified.iloc[split_idx:, df_modified.columns.get_loc('open')] *= 2.0
    df_modified.iloc[split_idx:, df_modified.columns.get_loc('high')] *= 2.0
    df_modified.iloc[split_idx:, df_modified.columns.get_loc('low')] *= 2.0

    # 執行篡改版回測
    res_mod = engine.run_backtest(df_modified, **common_kwargs)
    trades_mod = res_mod["trades"]

    # 防呆：本測試必須有交易樣本才有驗證意義
    assert len(trades_orig) > 0, "mock 數據未產生任何交易，測試失去驗證意義"
    
    # 篩選在第 80 根 K 線（t < split_time）之前的交易紀錄
    trades_orig_before = trades_orig[trades_orig["datetime"] < split_time]
    trades_mod_before = trades_mod[trades_mod["datetime"] < split_time]
    
    # 驗證筆數一致
    assert len(trades_orig_before) == len(trades_mod_before), "未來數據修改影響了歷史交易筆數！存在看前偏誤。"
    
    # 驗證每一筆交易的屬性（時間、價格、動作、股數）完全相同
    for i in range(len(trades_orig_before)):
        t_orig = trades_orig_before.iloc[i]
        t_mod = trades_mod_before.iloc[i]
        
        assert t_orig["datetime"] == t_mod["datetime"], f"第 {i} 筆交易的時間戳不一致"
        assert t_orig["action"] == t_mod["action"], f"第 {i} 筆交易的動作不一致"
        assert abs(t_orig["price"] - t_mod["price"]) < 1e-5, f"第 {i} 筆交易的執行價格不一致"
        assert abs(t_orig["shares"] - t_mod["shares"]) < 1e-5, f"第 {i} 筆交易的股數不一致"
        
    print("時序偏誤驗證通過：未來的價格變動不影響歷史既成之交易決策。")


def test_no_lookahead_bias_with_fvg():
    """
    spec 002：FVG 確認開啟（use_fvg=True）時，同樣不得有看前偏誤。

    FVG 偵測用 bar t 與 t-2（皆已收盤）、其「近 M 根存在」以 rolling 回看實作，
    理論上全因果。此測試以末梢篡改法端到端驗證：篡改第 80 根之後的 OHLC 不得
    改變篡改點之前的任何交易，證明 FVG 遮罩沒有把未來資料洩回過去。
    """
    n_bars = 100
    df_original = _generate_mock_data(n_bars)

    engine = BacktestEngine(
        initial_capital=1000000.0,
        commission_rate=0.001425,
        tax_rate=0.003,
        slippage_rate=0.0005,
        lot_size=1
    )

    common_kwargs = dict(
        atr_period=14,
        k=2.0,
        ch_period=22,
        ch_multiplier=3.0,
        time_limit=15,
        use_adx_filter=False,
        use_ma_filter=False,
        use_fvg=True,
        fvg_lookback=3
    )

    res_orig = engine.run_backtest(df_original, **common_kwargs)
    trades_orig = res_orig["trades"]

    df_modified = df_original.copy()
    split_idx = 80
    split_time = df_original.index[split_idx]

    df_modified.iloc[split_idx:, df_modified.columns.get_loc('close')] *= 2.0
    df_modified.iloc[split_idx:, df_modified.columns.get_loc('open')] *= 2.0
    df_modified.iloc[split_idx:, df_modified.columns.get_loc('high')] *= 2.0
    df_modified.iloc[split_idx:, df_modified.columns.get_loc('low')] *= 2.0

    res_mod = engine.run_backtest(df_modified, **common_kwargs)
    trades_mod = res_mod["trades"]

    assert len(trades_orig) > 0, "FVG 開啟下 mock 數據未產生任何交易，測試失去驗證意義"

    trades_orig_before = trades_orig[trades_orig["datetime"] < split_time]
    trades_mod_before = trades_mod[trades_mod["datetime"] < split_time]

    assert len(trades_orig_before) == len(trades_mod_before), \
        "FVG 開啟下未來數據修改影響了歷史交易筆數！FVG 遮罩存在看前偏誤。"

    for i in range(len(trades_orig_before)):
        t_orig = trades_orig_before.iloc[i]
        t_mod = trades_mod_before.iloc[i]

        assert t_orig["datetime"] == t_mod["datetime"], f"第 {i} 筆交易的時間戳不一致（FVG）"
        assert t_orig["action"] == t_mod["action"], f"第 {i} 筆交易的動作不一致（FVG）"
        assert abs(t_orig["price"] - t_mod["price"]) < 1e-5, f"第 {i} 筆交易的執行價格不一致（FVG）"
        assert abs(t_orig["shares"] - t_mod["shares"]) < 1e-5, f"第 {i} 筆交易的股數不一致（FVG）"

    print("FVG 時序偏誤驗證通過：FVG 確認未把未來資料洩回歷史決策。")


# ---------------------------------------------------------------------------
# 投資組合回測：晚上市標的之看前偏誤防禦
# 背景：portfolio_backtester 曾以 .bfill() 對齊多標的時間軸，導致晚上市標的
# （如 00919，2022-10 掛牌）掛牌前的 K 線被「未來第一根真實資料」回填，
# 進而可能於掛牌前進場、並以未來波動率參與 inverse_vol 權重計算。
# ---------------------------------------------------------------------------

def _make_portfolio_frame(dates: pd.DatetimeIndex) -> pd.DataFrame:
    """
    產生一組「每根 K 線都滿足多重確認進場條件」的合成指標資料：
    紅 K（close>open）、高於當日開盤、振幅 > 1.2*ATR、MSS=1、
    高於三關價中關且市況濾網通過。若此資料出現在掛牌前的時間軸上，
    回測引擎「一定」會進場——因此可用來偵測 bfill 造成的掛牌前交易。
    """
    n = len(dates)
    return pd.DataFrame({
        "open": np.full(n, 100.0),
        "close": np.full(n, 110.0),
        "high": np.full(n, 120.0),
        "low": np.full(n, 95.0),
        "atr": np.full(n, 1.0),
        "vwap": np.full(n, 105.0),
        "daily_open": np.full(n, 100.0),
        "mid_price": np.full(n, 50.0),
        "regime_ok": np.full(n, True),
        # spec 007：進場分流後，通用進場觸發改用 BOS 續勢（本測試驗證成交時序，
        # 與 MSS 反轉語意無關）。struct_sig=1 路徑與濾網與原本一致。
        "mss_signal": np.full(n, 0),
        "bos_signal": np.full(n, 1),
        "chandelier_long": np.full(n, 90.0),
        "realized_vol": np.full(n, 0.2),
        "param_time_limit": np.full(n, 10),
    }, index=dates)


def test_portfolio_alignment_keeps_nan_before_listing():
    """
    _align_frames 對齊後：晚上市標的在掛牌前必須保留 NaN（不得被未來資料回填）；
    掛牌後的停牌缺漏仍以 ffill 前值補齊。
    """
    from portfolio_backtester import PortfolioBacktester

    dates_early = pd.bdate_range("2025-01-01", periods=10)
    # 晚上市：只有最後 4 個交易日，且中間故意挖掉一天模擬停牌
    dates_late = dates_early[-4:].delete(1)

    early = _make_portfolio_frame(dates_early)
    late = _make_portfolio_frame(dates_late)
    late["close"] = [200.0, 202.0, 203.0]

    aligned = PortfolioBacktester._align_frames({"EARLY": early, "LATE": late})

    listing_date = dates_late[0]
    pre_listing = aligned["LATE"].loc[aligned["LATE"].index < listing_date]

    # 掛牌前一律 NaN——若此處出現數值即代表存在 bfill 看前偏誤
    assert pre_listing["close"].isna().all(), \
        "晚上市標的掛牌前被未來資料回填，違反憲法第 I 條看前偏誤防禦"
    assert pre_listing["realized_vol"].isna().all(), \
        "掛牌前的波動率被未來資料回填，將污染 inverse_vol 權重計算"

    # 掛牌後的停牌日（被挖掉的那天）仍應以前值補齊
    suspended_day = dates_early[-3]
    assert aligned["LATE"].loc[suspended_day, "close"] == 200.0, \
        "掛牌後的停牌缺漏應以 ffill 前值補齊"


def test_portfolio_no_trades_before_listing():
    """
    行為層防禦：即使晚上市標的的資料「每根 K 線都會觸發進場」，
    回測引擎也絕不能在其掛牌日之前對它成交任何一筆交易。
    （在舊的 .bfill() 實作下，本測試會失敗。）
    """
    from portfolio_backtester import PortfolioBacktester

    dates_early = pd.bdate_range("2025-01-01", periods=60)
    dates_late = dates_early[-20:]
    listing_date = dates_late[0]

    frames = {
        "EARLY.TW": _make_portfolio_frame(dates_early),
        "LATE.TW": _make_portfolio_frame(dates_late),
    }

    pb = PortfolioBacktester()
    pb.tickers = list(frames.keys())
    pb._load_and_calculate_indicators = lambda: frames

    results = pb.run_portfolio_backtest()
    trades = results["trades"]

    assert not trades.empty, "合成資料設計為必定進場，卻無任何交易——測試前提失效"

    late_trades = trades[trades["ticker"] == "LATE.TW"]
    phantom = late_trades[late_trades["datetime"] < listing_date]
    assert phantom.empty, (
        f"偵測到晚上市標的於掛牌日 {listing_date.date()} 前的幽靈交易 "
        f"{len(phantom)} 筆——時間軸對齊存在 bfill 看前偏誤"
    )

    # 交叉驗證：早上市標的在同一期間應正常交易（確保防護沒有誤殺）
    early_trades = trades[trades["ticker"] == "EARLY.TW"]
    assert not early_trades.empty, "掛牌前防護誤殺了正常標的的進場"

# ---------------------------------------------------------------------------
# 憲法 I 成交規則：訊號於第 N 根（收盤後）判定、第 N+1 根開盤價成交
# 背景：引擎曾以「訊號當根的收盤價」成交——收盤價確定的那一刻已無法以該價
# 成交，屬樂觀偏誤。以下測試鎖定「成交價必須來自成交當根的開盤價」。
# ---------------------------------------------------------------------------

def test_fills_use_next_bar_open():
    """
    單標的引擎：每一筆交易的成交價必須等於「成交當根開盤價 × (1 ± 滑價)」，
    而非任何一根的收盤價。（在舊的「當根收盤成交」實作下，本測試會失敗。）
    """
    df = _generate_mock_data(100)

    engine = BacktestEngine(
        initial_capital=1000000.0,
        commission_rate=0.001425,
        tax_rate=0.003,
        slippage_rate=0.0005,
        lot_size=1
    )
    res = engine.run_backtest(
        df,
        atr_period=14, k=2.0, ch_period=22, ch_multiplier=3.0,
        time_limit=15, use_adx_filter=False, use_ma_filter=False
    )
    trades = res["trades"]
    assert len(trades) > 0, "mock 數據未產生任何交易，測試失去驗證意義"

    slip = engine.slippage_rate
    for _, tr in trades.iterrows():
        bar_open = df.loc[tr["datetime"], "open"]
        if tr["action"] == "BUY":
            expected = bar_open * (1 + slip)
        else:  # SELL_HALF / SELL_ALL
            expected = bar_open * (1 - slip)
        assert abs(tr["price"] - expected) < 1e-9, (
            f"{tr['datetime']} {tr['action']} 成交價 {tr['price']:.4f} "
            f"不等於當根開盤 {bar_open:.4f} × (1±滑價) —— 違反憲法 I 成交規則"
        )


def test_portfolio_fills_use_next_bar_open():
    """
    投資組合引擎：合成資料 open=100、close=110 恆定，
    買入成交價必須是 100 ×(1+滑價)（次根開盤），而非 110（訊號根收盤）。
    """
    from portfolio_backtester import PortfolioBacktester

    frames = {"AAA.TW": _make_portfolio_frame(pd.bdate_range("2025-01-01", periods=30))}

    pb = PortfolioBacktester()
    pb.tickers = list(frames.keys())
    pb._load_and_calculate_indicators = lambda: frames

    trades = pb.run_portfolio_backtest()["trades"]
    buys = trades[trades["action"] == "BUY"]
    assert not buys.empty, "合成資料設計為必定進場，卻無任何買入——測試前提失效"

    expected = 100.0 * (1 + pb.slippage_rate)
    assert (abs(buys["price"] - expected) < 1e-9).all(), (
        "買入成交價不是次根開盤價——若接近 110（訊號根收盤）即為當根收盤成交偏誤"
    )


# ===========================================================================
# spec 007 — MSS fractal 反轉訊號的看前偏誤防禦（碎形確認延遲 N 根）
# ===========================================================================

def _ohlcv_seq(highs, lows, closes, volumes):
    idx = pd.date_range("2026-02-02 09:00:00", periods=len(highs), freq="1min")
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def _uptrend_then_breakdown():
    highs = [11, 14, 13, 16, 15, 18, 17, 20, 19, 17]
    lows = [10, 13, 12, 15, 14, 17, 16, 19, 18, 13]
    closes = [10.5, 13.5, 12.5, 15.5, 14.5, 17.5, 16.5, 19.5, 18.5, 14.0]
    vols = [1000] * 9 + [3000]
    return _ohlcv_seq(highs, lows, closes, vols)


def test_mss_fractal_prefix_consistency():
    """對任一截斷點 t，前綴計算的 mss[t] 必與全量計算的 mss[t] 相等
    （mss 僅依賴 <= t 的資料；碎形樞紐經 shift(n) 確認延遲，故不偷看未來）。"""
    from acceptance_fixtures import make_klines
    df = make_klines(300, freq="5min")
    full_mss, _ = detect_market_structure(df, period=10, swing_n=2, volume_mult=1.5)
    for t in (30, 60, 90, 150, 210, 270, len(df) - 1):
        pre_mss, _ = detect_market_structure(df.iloc[: t + 1], period=10, swing_n=2, volume_mult=1.5)
        assert pre_mss.iloc[t] == full_mss.iloc[t], f"bar {t} 的 MSS 隨未來資料改變——看前偏誤"


def test_mss_future_bars_do_not_leak_into_signal():
    """反轉 MSS 觸發根（bar 9）的訊號，不因其後追加未來 K 線而改變。"""
    df = _uptrend_then_breakdown()
    mss_now, _ = detect_market_structure(df, period=5, swing_n=1, volume_mult=1.5)
    assert mss_now.iloc[9] == -1  # 前提：bar9 為看跌反轉

    # 以單一連續索引追加 2 根「未來」K 線（含會使 bar9 事後成為樞紐的走勢），
    # bar9 當下的訊號必須不變（連續遞增索引，貼合實際時序契約）。
    highs = [11, 14, 13, 16, 15, 18, 17, 20, 19, 17, 25, 8]
    lows = [10, 13, 12, 15, 14, 17, 16, 19, 18, 13, 7, 4]
    closes = [10.5, 13.5, 12.5, 15.5, 14.5, 17.5, 16.5, 19.5, 18.5, 14.0, 24, 5]
    vols = [1000] * 9 + [3000, 5000, 5000]
    extended = _ohlcv_seq(highs, lows, closes, vols)
    mss_ext, _ = detect_market_structure(extended, period=5, swing_n=1, volume_mult=1.5)
    assert mss_ext.iloc[9] == mss_now.iloc[9], "bar9 的 MSS 因未來 K 線而改變——看前偏誤"


# ---------------------------------------------------------------------------
# spec 008b（SC-005 / FR-007）：期貨 sizing 與成交之看前偏誤防線
# ---------------------------------------------------------------------------

def _futures_run(df, initial_capital=10_000_000.0):
    from acceptance_fixtures import make_klines  # noqa: F401（fixture 來源見 e2e）
    from config.config import FuturesCostConfig
    from instruments import ContractSpec
    from trading_costs import FuturesCostModel, FuturesSizer
    txc = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)
    cfg = FuturesCostConfig()
    return BacktestEngine(initial_capital=initial_capital).run_backtest(
        df, asset_class="futures",
        cost_model=FuturesCostModel(txc, cfg),
        sizer=FuturesSizer(txc, cfg),
        point_value=200.0, verbose=False,
    )


def test_futures_sizing_and_execution_no_lookahead():
    """截斷第 N（進場）根之後的資料，不改變該根的口數決策與成交價；
    成交發生於訊號次根開盤 + 滑價 tick（FR-007／憲章 I）。"""
    from acceptance_fixtures import make_klines
    df = make_klines(300, freq="5min")
    full = _futures_run(df)
    buys = full["trades"]
    buys = buys[buys["action"] == "BUY"]
    assert not buys.empty, "fixture 應觸發期貨進場"
    first = buys.iloc[0]
    k = df.index.get_loc(first["datetime"])  # 進場（成交）根位置

    # (1) 成交於進場根開盤 + 1 tick 滑價（不利方向）
    assert first["price"] == pytest.approx(float(df["open"].iloc[k]) + 1.0)

    # (2) sizing 用訊號根（k−1）收盤價（保證金以訊號根名目值計）
    assert first["sizing_price"] == pytest.approx(float(df["close"].iloc[k - 1]))

    # (3) 末梢截斷不變性：只留到進場根為止的資料，進場決策（時間/口數/價格）完全相同
    truncated = _futures_run(df.iloc[: k + 1])
    tbuys = truncated["trades"]
    tbuys = tbuys[tbuys["action"] == "BUY"]
    assert not tbuys.empty, "截斷後進場應仍存在（決策只依賴過去資料）"
    tfirst = tbuys.iloc[0]
    assert tfirst["datetime"] == first["datetime"]
    assert tfirst["shares"] == first["shares"]
    assert tfirst["price"] == first["price"]
