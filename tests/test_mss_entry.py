# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 007 US2 — MSS 反轉進場的閘門機制單元測試。

驗證 research D6：反轉進場複用 `check_entry_signal` 的 `disabled_filters`，以
「反轉 profile」放寬 {'trend','global'}（順勢確認 + 三關價/regime），使逆勢的
反轉能進場；但 structure/momentum/volatility 仍為必要條件（不無條件放行）。
"""

from ladder_system import PositionManager


# 情境：看漲反轉——收紅 K（動能 OK）、振幅大（波動 OK）、結構 +1，
# 但價格在 daily_open/VWAP 之下（順勢 trend 不過）且全域濾網不利（均線下/三關價下）。
_REVERSAL_BAR = dict(
    close=105.0, open_val=100.0,          # 收紅：momentum OK
    daily_open=110.0, vwap=110.0,         # close < daily_open/vwap：trend 不過
    atr=1.0, candle_high=110.0, candle_low=95.0,  # 振幅 15 > 1.2×ATR：volatility OK
    structure_sig=1,
    global_filter_ok=False,               # 200MA/三關價 不利（反轉本質逆勢）
    is_daily=True,
)


def test_bos_profile_blocks_reversal_bar():
    """全維度（BOS）profile：逆勢 + 全域不利 → 不進場。"""
    pm = PositionManager()
    assert pm.check_entry_signal(**_REVERSAL_BAR, disabled_filters=frozenset()) is False


def test_reversal_profile_enters_reversal_bar():
    """反轉 profile 放寬 {'trend','global'} → 進場（其餘維度仍須通過）。"""
    pm = PositionManager()
    assert pm.check_entry_signal(
        **_REVERSAL_BAR, disabled_filters=frozenset({"trend", "global"})
    ) is True


def test_reversal_profile_still_requires_momentum():
    """即使放寬 trend/global，動能（收陰線）不過仍不進場——反轉非無條件放行。"""
    bar = dict(_REVERSAL_BAR)
    bar["close"] = 99.0  # close < open：momentum 不過
    pm = PositionManager()
    assert pm.check_entry_signal(
        **bar, disabled_filters=frozenset({"trend", "global"})
    ) is False


def test_reversal_profile_still_requires_volatility():
    """位移不足（振幅 <= 1.2×ATR）仍不進場。"""
    bar = dict(_REVERSAL_BAR)
    bar["candle_high"], bar["candle_low"], bar["atr"] = 100.5, 100.0, 5.0  # 振幅 0.5 < 1.2×5
    pm = PositionManager()
    assert pm.check_entry_signal(
        **bar, disabled_filters=frozenset({"trend", "global"})
    ) is False
