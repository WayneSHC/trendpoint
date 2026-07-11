# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 訊號誠實性防禦測試（健檢 1.3 與 1.5）

1.3：即時監控不得以「進行中、尚未收盤」的 K 線判定訊號（repaint 防禦）。
1.5：參數尋優不得在全樣本上尋優後直接固化——網格搜尋只能看訓練段，
     最佳參數須通過 hold-out 段（樣本外）驗證才允許寫回 config。
"""

import numpy as np
import pandas as pd
import pytest

from monitor_signals import select_closed_bar_indices
import optimizer as optimizer_module
from optimizer import ParameterOptimizer


# ---------------------------------------------------------------------------
# 1.3 即時監控：已收盤 K 線選擇
# ---------------------------------------------------------------------------

def test_monitor_skips_in_progress_bar():
    """
    盤中（now 未達末根時間 + K 線間隔）：末根仍在跳動，
    必須改用倒數第二根作為「最新已收盤 K 線」。
    """
    times = pd.date_range("2026-07-10 09:00", periods=10, freq="5min")  # 末根 09:45
    now = pd.Timestamp("2026-07-10 09:47")  # 09:45 的 bar 要到 09:50 才收盤
    assert select_closed_bar_indices(times, now, pd.Timedelta(minutes=5)) == (-2, -3)


def test_monitor_uses_last_bar_once_closed():
    """
    末根已收盤（now >= 末根時間 + 間隔）：末根即為最新已收盤 K 線。
    邊界：恰於收盤時刻視為已收盤。
    """
    times = pd.date_range("2026-07-10 09:00", periods=10, freq="5min")  # 末根 09:45
    interval = pd.Timedelta(minutes=5)
    assert select_closed_bar_indices(times, pd.Timestamp("2026-07-10 09:50"), interval) == (-1, -2)
    assert select_closed_bar_indices(times, pd.Timestamp("2026-07-10 09:53"), interval) == (-1, -2)


# ---------------------------------------------------------------------------
# 1.5 參數尋優：train / hold-out 切分與寫回閘門
# ---------------------------------------------------------------------------

def _make_price_df(n: int) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({"close": np.linspace(100.0, 110.0, n)}, index=idx)


def _stub_engine_factory(calls, holdout_return):
    """
    回傳一個記錄每次回測 df 長度的假引擎類別。
    訓練段：(atr_period=14, k=2.0) 給最高報酬，必為最佳參數。
    hold-out 段（以 df 長度區分）：回傳 holdout_return。
    """
    class StubEngine:
        def __init__(self, **kwargs):
            pass

        def run_backtest(self, df, atr_period, k, **kwargs):
            calls.append(len(df))
            if len(df) == 100:  # hold-out 段（400 根 × 0.25）
                ret = holdout_return
            else:
                ret = 0.5 if (atr_period == 14 and k == 2.0) else 0.1
            return {"summary": {"total_return": ret, "max_drawdown": -0.1}}

    return StubEngine


def test_optimizer_grid_search_never_sees_holdout(monkeypatch):
    """
    網格搜尋的每一次回測只能看到訓練段（前 75%）；
    hold-out 段只在最後驗證時跑一次。全樣本尋優＝看過答案，即為不通過。
    """
    calls = []
    monkeypatch.setattr(optimizer_module, "BacktestEngine",
                        _stub_engine_factory(calls, holdout_return=0.2))

    opt = ParameterOptimizer()
    monkeypatch.setattr(opt, "_load_data", lambda ticker: _make_price_df(400))

    best_params, train_calmar, holdout_summary = opt.optimize_ticker("FAKE.TW")

    # 6 個 atr_period × 5 個 k = 30 次訓練段回測 + 1 次 hold-out 驗證
    assert len(calls) == 31
    assert all(n == 300 for n in calls[:30]), "網格搜尋看到了訓練段以外的資料"
    assert calls[30] == 100, "hold-out 驗證未在保留段上執行"

    assert best_params["atr_period"] == 14 and best_params["ladder_k"] == 2.0
    assert holdout_summary["total_return"] == pytest.approx(0.2)
    assert ParameterOptimizer.holdout_passes(holdout_summary)


def test_optimizer_holdout_gate_blocks_overfit(monkeypatch):
    """
    hold-out 段報酬為負 → 閘門必須擋下寫回（視為過擬合）。
    """
    calls = []
    monkeypatch.setattr(optimizer_module, "BacktestEngine",
                        _stub_engine_factory(calls, holdout_return=-0.05))

    opt = ParameterOptimizer()
    monkeypatch.setattr(opt, "_load_data", lambda ticker: _make_price_df(400))

    _, _, holdout_summary = opt.optimize_ticker("FAKE.TW")
    assert not ParameterOptimizer.holdout_passes(holdout_summary), \
        "樣本外虧損的參數不得寫回 config"


def test_optimizer_rejects_too_short_holdout(monkeypatch):
    """
    hold-out 段不足 60 根 K 線：資料太短無法誠實驗證，應直接報錯。
    """
    opt = ParameterOptimizer()
    monkeypatch.setattr(opt, "_load_data", lambda ticker: _make_price_df(100))

    with pytest.raises(ValueError, match="hold-out"):
        opt.optimize_ticker("FAKE.TW")  # 100 × 0.25 = 25 < 60
