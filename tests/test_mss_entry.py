# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 007 US2 — MSS 反轉進場的閘門機制單元測試（research D6 修訂版）。

反轉 profile：複用 `check_entry_signal` 的 `disabled_filters`，只放寬順勢確認
`{'trend'}`（並由呼叫端在 global_filter_ok 中去掉 200MA regime），但**保留三關價**
（close>mid_price，spec 003 強調的空頭防線）。structure/momentum/volatility 仍為
必要條件。回測層以 `global_filter_ok = (close > mid_price)` 傳入反轉路徑。
"""

from ladder_system import PositionManager


# 看漲反轉根：收紅 K（動能 OK）、振幅大（波動 OK）、結構 +1，
# 但價格在 daily_open/VWAP 之下（順勢 trend 不過）。global_filter_ok 由各測試控制，
# 代表回測層傳入的「三關價是否通過」。
_REVERSAL_BAR = dict(
    close=105.0, open_val=100.0,          # 收紅：momentum OK
    daily_open=110.0, vwap=110.0,         # close < daily_open/vwap：trend 不過
    atr=1.0, candle_high=110.0, candle_low=95.0,  # 振幅 15 > 1.2×ATR：volatility OK
    structure_sig=1,
    global_filter_ok=True,                # 三關價通過（反轉 profile 保留此關）
    is_daily=True,
)


def test_bos_profile_blocks_reversal_bar():
    """全維度（BOS）profile：逆勢 trend 不過 → 不進場。"""
    pm = PositionManager()
    assert pm.check_entry_signal(**_REVERSAL_BAR, disabled_filters=frozenset()) is False


def test_reversal_profile_enters_when_three_gate_ok():
    """反轉 profile 放寬 {'trend'}、三關價通過 → 進場。"""
    pm = PositionManager()
    assert pm.check_entry_signal(
        **_REVERSAL_BAR, disabled_filters=frozenset({"trend"})
    ) is True


def test_reversal_still_blocked_below_three_gate():
    """三關價保留：global_filter_ok=False（價在中關價之下）→ 反轉仍不進場。"""
    bar = {**_REVERSAL_BAR, "global_filter_ok": False}
    pm = PositionManager()
    assert pm.check_entry_signal(**bar, disabled_filters=frozenset({"trend"})) is False


def test_reversal_profile_still_requires_momentum():
    """即使放寬 trend，動能（收陰線）不過仍不進場。"""
    bar = {**_REVERSAL_BAR, "close": 99.0}  # close < open：momentum 不過
    pm = PositionManager()
    assert pm.check_entry_signal(**bar, disabled_filters=frozenset({"trend"})) is False


def test_reversal_profile_still_requires_volatility():
    """位移不足（振幅 <= 1.2×ATR）仍不進場。"""
    bar = {**_REVERSAL_BAR, "candle_high": 100.5, "candle_low": 100.0, "atr": 5.0}
    pm = PositionManager()
    assert pm.check_entry_signal(**bar, disabled_filters=frozenset({"trend"})) is False
