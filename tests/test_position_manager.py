# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 部位管理器與指標暖機期防禦測試（健檢 1.6/1.7/1.8/1.10）

1.6：ATR 暖機期回傳 NaN，且波動濾網在 ATR 未成熟時不得放行進場。
1.7：時間止盈僅約束 stage 1 為「預期行為」——本檔將該行為固定為規格。
1.8：吊燈止損函式不再內部 shift（時基統一由呼叫端管理）。
1.10：manage_position 回傳 ExitEvent enum；引擎以 enum 身分比對驅動資金流，
      修改顯示文案不得影響任何邏輯分支。
"""

import numpy as np
import pandas as pd
import pytest

from ladder_system import (
    PositionManager,
    ExitEvent,
    FULL_EXIT_EVENTS,
    calculate_atr,
    calculate_chandelier_exit,
)


def _open_long(entry: float = 100.0, atr: float = 2.0) -> PositionManager:
    pm = PositionManager()
    pm.is_active = True
    pm.entry_price = entry
    pm.position_size = 1.0
    pm.stop_loss = entry - 2.0 * atr
    pm.stage = 1
    pm.direction = 1
    return pm


# ---------------------------------------------------------------------------
# 1.10 ExitEvent enum
# ---------------------------------------------------------------------------

def test_manage_position_returns_enum_not_string():
    pm = _open_long()
    event = pm.manage_position(current_close=100.0, current_atr=2.0,
                               chandelier_long=90.0, bar_count=1, time_limit=10)
    assert isinstance(event, ExitEvent), \
        "manage_position 必須回傳 ExitEvent——字串比對會因文案修改而靜默失效"
    assert event is ExitEvent.HOLDING


def test_full_exit_events_cover_all_liquidations():
    """全數平倉事件集合必須恰好涵蓋三種出場，缺一都會讓引擎漏賣股票。"""
    assert FULL_EXIT_EVENTS == {ExitEvent.STOP_LOSS, ExitEvent.TIME_LIMIT, ExitEvent.CHANDELIER}


def test_stop_loss_is_close_based():
    """止損以「收盤價跌破」判定（設計上的樂觀假設，見 manage_position docstring）。"""
    pm = _open_long(entry=100.0, atr=2.0)  # 止損 96
    assert pm.manage_position(95.9, 2.0, 90.0, 1, 10) is ExitEvent.STOP_LOSS
    assert not pm.is_active


# ---------------------------------------------------------------------------
# 1.7 時間止盈僅約束 stage 1（預期行為，固定為規格）
# ---------------------------------------------------------------------------

def test_time_limit_closes_stale_stage1_position():
    pm = _open_long()
    event = pm.manage_position(current_close=101.0, current_atr=2.0,
                               chandelier_long=90.0, bar_count=10, time_limit=10)
    assert event is ExitEvent.TIME_LIMIT


def test_time_limit_does_not_apply_after_stage1_half():
    """
    完成階段 1 減半後（stage 2、止損已移至保本位），部位讓利潤奔跑，
    「不受」時間上限約束——只由吊燈止損或保本止損出場。
    """
    pm = _open_long(entry=100.0, atr=2.0)
    # 觸發階段 1：獲利達 1.5 * ATR = 103
    assert pm.manage_position(103.5, 2.0, 90.0, 3, 10) is ExitEvent.STAGE1_HALF
    assert pm.stage == 2 and pm.stop_loss == pytest.approx(100.0)

    # bar_count 遠超 time_limit，但 stage 2 不得被時間止盈平倉
    event = pm.manage_position(current_close=104.0, current_atr=2.0,
                               chandelier_long=101.0, bar_count=99, time_limit=10)
    assert event is ExitEvent.HOLDING, "stage 2 部位不受時間上限約束（設計行為）"
    # 出場只能來自止損／吊燈（絕非 TIME_LIMIT）：
    # 上一呼叫已把止損上移至 101，收盤跌破走頂部 STOP_LOSS 分支；
    # 同一根內吊燈上移越過收盤則走 CHANDELIER 分支——兩者皆屬全數平倉
    exit_event = pm.manage_position(100.5, 2.0, 101.0, 100, 10)
    assert exit_event in FULL_EXIT_EVENTS and exit_event is not ExitEvent.TIME_LIMIT


# ---------------------------------------------------------------------------
# 1.6 ATR 暖機期
# ---------------------------------------------------------------------------

def test_atr_warmup_is_nan_not_zero():
    tr = pd.Series(np.full(30, 1.0))
    atr = calculate_atr(tr, period=14)
    assert atr.iloc[:13].isna().all(), "暖機期 ATR 必須為 NaN——回傳 0 會停用波動濾網並讓止損貼進場價"
    assert atr.iloc[13] == pytest.approx(1.0)
    assert atr.iloc[13:].notna().all()

    # 序列長度不足 period：全部 NaN
    short = calculate_atr(pd.Series(np.full(5, 1.0)), period=14)
    assert short.isna().all()


def test_entry_rejected_while_atr_immature():
    """波動濾網在 ATR 為 NaN 或 0 時不得放行（其餘條件全滿足也一樣）。"""
    pm = PositionManager()
    kwargs = dict(close=110.0, open_val=100.0, daily_open=100.0, vwap=105.0,
                  candle_high=120.0, candle_low=95.0, structure_sig=1,
                  global_filter_ok=True, is_daily=True)
    assert pm.check_entry_signal(atr=1.0, **kwargs), "測試前提：ATR 成熟時應可進場"
    assert not pm.check_entry_signal(atr=float("nan"), **kwargs), "ATR=NaN 不得進場"
    assert not pm.check_entry_signal(atr=0.0, **kwargs), "ATR=0（未成熟）不得進場"


# ---------------------------------------------------------------------------
# 1.8 吊燈止損不再雙重移位
# ---------------------------------------------------------------------------

def test_chandelier_exit_is_not_shifted():
    """
    函式回傳「當根」吊燈線（rolling 含當根 high），時基由呼叫端統一管理
    （引擎取判定根的前一根）。舊版函式內 shift(1) + 引擎再取前根 = 慢兩根。
    """
    n = 30
    df = pd.DataFrame({
        "high": np.linspace(100.0, 129.0, n),
        "low": np.linspace(95.0, 124.0, n),
    })
    atr = pd.Series(np.full(n, 2.0))
    ch_long, ch_short = calculate_chandelier_exit(df, atr, period=5, multiplier=3.0)

    j = 20
    expected_long = df["high"].iloc[j - 4:j + 1].max() - 2.0 * 3.0
    assert ch_long.iloc[j] == pytest.approx(expected_long), \
        "吊燈多頭線必須等於『含當根』的 rolling max - ATR*mult（不得再 shift）"
    expected_short = df["low"].iloc[j - 4:j + 1].min() + 2.0 * 3.0
    assert ch_short.iloc[j] == pytest.approx(expected_short)
