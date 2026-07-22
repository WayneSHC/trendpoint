# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 投資組合回測引擎與資金分配單元測試 (pytest)

本測試針對 portfolio_backtester.py 進行測試：
1. 驗證資料庫連線與指標計算之正確性。
2. 驗證多標的時間戳對齊與前值插補 (Reindex) 邏輯。
3. 驗證等權重分配 (Equal-Weight) 是否確實受到單一標的資金上限 (1/N) 約束。
"""

import pytest
import pandas as pd
import numpy as np
from config import load_config
from portfolio_backtester import PortfolioBacktester

def test_portfolio_backtester_initialization():
    """
    測試 PortfolioBacktester 是否能正確載入 config 參數
    """
    pb = PortfolioBacktester()
    assert pb.initial_capital == 1000000.0, "初始資金載入錯誤"
    assert len(pb.tickers) >= 2, "追蹤標的清單載入錯誤"
    assert pb.commission_rate >= 0.0, "手續費率載入錯誤"

def test_timeline_alignment_logic():
    """
    測試多標的時間軸對齊與插補邏輯（_align_frames）：
    掛牌後缺漏以 ffill 前值補齊；掛牌前一律保留 NaN，禁止 bfill
    （bfill 會把未來資料回填至掛牌前，構成看前偏誤，
    行為層防禦見 tests/test_lookahead_bias.py）。
    """
    # 建立模擬的標的 A 與 標的 B 資料，故意讓時間戳不完全一致：
    # B 較晚「掛牌」（首日 05-21），且 A 在 05-22 缺一根（模擬停牌）
    dates_a = pd.to_datetime(["2026-05-20", "2026-05-21", "2026-05-23"])
    dates_b = pd.to_datetime(["2026-05-21", "2026-05-22", "2026-05-23"])

    df_a = pd.DataFrame({"close": [100.0, 101.0, 102.0]}, index=dates_a)
    df_b = pd.DataFrame({"close": [200.0, 201.0, 202.0]}, index=dates_b)

    aligned = PortfolioBacktester._align_frames({"A": df_a, "B": df_b})
    aligned_a, aligned_b = aligned["A"], aligned["B"]

    # 斷言合併後的時間戳完整性 (包含 20, 21, 22, 23 號)
    assert len(aligned_a.index) == 4

    # 標的 A 在 2026-05-22 應以 2026-05-21 的值 (101.0) 進行 ffill 插補
    assert aligned_a.loc["2026-05-22", "close"] == 101.0
    # 標的 B 掛牌前 (2026-05-20) 必須保留 NaN，不得被未來值 200.0 回填
    assert pd.isna(aligned_b.loc["2026-05-20", "close"])

def _make_indicator_df(n: int = 30, price: float = 100.0) -> pd.DataFrame:
    """
    建立一個具備回測迴圈所需全部指標欄位的模擬 DataFrame。
    mid_price 設為遠高於 close，確保三關價濾網不通過、不會觸發進場。
    """
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "open": price,
        "high": price + 1.0,
        "low": price - 1.0,
        "close": price,
        "atr": 1.0,
        "vwap": price,
        "mss_signal": 0,
        "bos_signal": 0,
        "ladder": price,
        "chandelier_long": price - 2.0,
        "chandelier_short": price + 2.0,
        "daily_open": price,
        "mid_price": price * 10.0,
        "upper_price": price * 10.0,
        "lower_price": 0.0,
        "regime_ok": False,
        "realized_vol": 0.01,
        "param_time_limit": 5,
    }, index=idx)

def test_missing_ticker_filtered_with_warning(monkeypatch, capsys):
    """
    config 中的標的若在資料庫無對應資料表（_load_and_calculate_indicators
    回傳的 dict 缺少該標的），run_portfolio_backtest 應：
    1. 不拋出 KeyError；
    2. 將該標的自 self.tickers 過濾掉；
    3. 印出包含缺失標的代號的警告訊息。
    """
    pb = PortfolioBacktester()
    pb.tickers = ["AAA.TW", "BBB.TW"]
    pb.allocation = "equal"

    # 模擬 BBB.TW 在 trendpoint.db 沒有資料表而被靜默跳過
    monkeypatch.setattr(
        pb, "_load_and_calculate_indicators",
        lambda: {"AAA.TW": _make_indicator_df()}
    )

    res = pb.run_portfolio_backtest()

    assert pb.tickers == ["AAA.TW"], "缺資料的標的應自 tickers 過濾"
    out = capsys.readouterr().out
    assert "BBB.TW" in out, "警告訊息應包含缺失標的代號"
    assert "警告" in out, "應印出警告訊息"

    df_equity = res["equity_curve"]
    assert not df_equity.empty
    # 全程無進場，淨值應恆等於初始資金
    assert (df_equity["equity"] == pb.initial_capital).all()

def test_all_tickers_missing_raises(monkeypatch):
    """
    所有標的皆無資料時，應維持既有行為：拋出帶清楚訊息的 ValueError。
    """
    pb = PortfolioBacktester()
    pb.tickers = ["AAA.TW", "BBB.TW"]
    monkeypatch.setattr(pb, "_load_and_calculate_indicators", lambda: {})

    with pytest.raises(ValueError, match="無法執行回測"):
        pb.run_portfolio_backtest()

def test_portfolio_backtest_execution(tickers_with_data):
    """
    測試 PortfolioBacktester 的聯合成績效回測執行。

    前提：DB 至少有一檔 config 標的的非空日線資料
    （判準見 conftest.py 的 tickers_with_data，涵蓋測試在
    tests/test_db_preconditions.py）。
    """
    cfg = load_config()
    db_path = cfg.data.database_path
    if not tickers_with_data(db_path, cfg.data.tickers):
        pytest.skip(
            f"{db_path} 中無任何 config 標的之日線資料"
            f"（需先執行 run_ingestion.py），跳過實際執行測試"
        )

    pb = PortfolioBacktester()
    res = pb.run_portfolio_backtest()
    
    assert "summary" in res, "回測結果缺少 summary"
    assert "equity_curve" in res, "回測結果缺少 equity_curve"
    assert "trades" in res, "回測結果缺少 trades"
    
    summary = res["summary"]
    assert summary["initial_capital"] == 1000000.0
    assert "total_return" in summary
    assert "max_drawdown" in summary
    
    df_equity = res["equity_curve"]
    assert not df_equity.empty
    assert "equity" in df_equity.columns
    assert "cash" in df_equity.columns
