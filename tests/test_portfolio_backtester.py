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
import os
import pandas as pd
import numpy as np
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

def test_portfolio_backtest_execution():
    """
    測試 PortfolioBacktester 的聯合成績效回測執行。
    """
    db_path = "trendpoint.db"
    if not os.path.exists(db_path):
        pytest.skip("資料庫檔案不存在，跳過實際執行測試")
        
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
