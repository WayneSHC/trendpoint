# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 003 — 空方鏡像對稱（SC-002）與裁決/硬邊界（SC-004）測試。

SC-002a：數值鏡像變換全鏈（常數 1 口 sizer 隔離價格水位效應，analyze H1）。
SC-002b：原語手工情境對（check_entry 真值表、manage_position 空方行為）。
"""

import math

import pandas as pd
import pytest

from acceptance_fixtures import make_klines
from backtester import BacktestEngine
from config.config import FuturesCostConfig
from instruments import ContractSpec
from ladder_system import ExitEvent, PositionManager
from trading_costs import FuturesCostModel, PositionSizer

TXC = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)


class OneLotSizer(PositionSizer):
    """常數 1 口：隔離 sizing 的價格水位效應（保證金口數依價格而變，非對稱標的）。"""

    def size(self, equity: float, price: float) -> float:
        return 1.0

    def partial_units(self, held: float, fraction: float) -> float:
        return float(math.floor(held * fraction))


# ---------------------------------------------------------------------------
# SC-002b（T006）：check_entry_signal 方向鏡像真值表
# ---------------------------------------------------------------------------

_BASE_LONG = dict(close=105.0, open_val=100.0, daily_open=101.0, vwap=102.0,
                  atr=2.0, candle_high=106.0, candle_low=99.0,
                  structure_sig=1, global_filter_ok=True)
# 完全鏡像的空方輸入（收陰、價低於當日開盤與 VWAP、看跌結構）
_BASE_SHORT = dict(close=95.0, open_val=100.0, daily_open=99.0, vwap=98.0,
                   atr=2.0, candle_high=101.0, candle_low=94.0,
                   structure_sig=-1, global_filter_ok=True)


def test_check_entry_long_default_unchanged():
    """direction 預設 1：不傳 direction 與明傳 1 輸出一致（back-compat parity）。"""
    pm = PositionManager()
    assert pm.check_entry_signal(**_BASE_LONG) is True
    assert pm.check_entry_signal(**_BASE_LONG, direction=1) is True


def test_check_entry_short_mirror_truth_table():
    pm = PositionManager()
    # 完整鏡像輸入 → 空方通過
    assert pm.check_entry_signal(**_BASE_SHORT, direction=-1) is True
    # 逐維度破壞 → 空方拒絕
    bad_structure = {**_BASE_SHORT, 'structure_sig': 1}        # 看漲結構
    assert pm.check_entry_signal(**bad_structure, direction=-1) is False
    # 收陽但仍低於當日開盤/VWAP（只違反動能維度）
    bad_momentum = {**_BASE_SHORT, 'close': 97.0, 'open_val': 96.0}
    assert pm.check_entry_signal(**bad_momentum, direction=-1) is False
    bad_trend = {**_BASE_SHORT, 'close': 99.5}                 # 高於當日開盤 99
    assert pm.check_entry_signal(**bad_trend, direction=-1) is False
    bad_vol = {**_BASE_SHORT, 'candle_high': 95.5, 'candle_low': 94.5}  # 振幅 < 1.2×ATR
    assert pm.check_entry_signal(**bad_vol, direction=-1) is False
    bad_global = {**_BASE_SHORT, 'global_filter_ok': False}
    assert pm.check_entry_signal(**bad_global, direction=-1) is False
    # 消融：破壞的維度被停用 → 恢復通過（語意同多方）
    assert pm.check_entry_signal(**bad_momentum, direction=-1,
                                 disabled_filters=frozenset({'momentum'})) is True


def test_check_entry_short_daily_semantics():
    """日線：趨勢端僅看 daily_open（鏡像多方 is_daily 語意）。"""
    pm = PositionManager()
    kw = {**_BASE_SHORT, 'vwap': 90.0}   # 價高於 vwap——非日線會 fail、日線應忽略 vwap
    assert pm.check_entry_signal(**kw, direction=-1, is_daily=True) is True
    assert pm.check_entry_signal(**kw, direction=-1, is_daily=False) is False


# ---------------------------------------------------------------------------
# SC-002b（T006）：manage_position 空方手工情境對
# ---------------------------------------------------------------------------

def _short_pm(entry=100.0, atr=2.0):
    pm = PositionManager()
    pm.is_active = True
    pm.entry_price = entry
    pm.position_size = 1.0
    pm.stop_loss = entry + 2.0 * atr   # 空方止損在上方
    pm.stage = 1
    pm.direction = -1
    return pm


def test_short_stop_loss_on_rise():
    pm = _short_pm()
    assert pm.manage_position(103.9, 2.0, chandelier_long=0.0, bar_count=1) is ExitEvent.HOLDING
    assert pm.manage_position(104.0, 2.0, chandelier_long=0.0, bar_count=2) is ExitEvent.STOP_LOSS


def test_short_stage1_target_and_breakeven():
    pm = _short_pm()
    # 目標 = 100 − 1.5×2 = 97
    assert pm.manage_position(97.1, 2.0, chandelier_long=0.0, bar_count=1) is ExitEvent.HOLDING
    ev = pm.manage_position(97.0, 2.0, chandelier_long=0.0, bar_count=2)
    assert ev is ExitEvent.STAGE1_HALF
    assert pm.stage == 2 and pm.stop_loss == 100.0   # 保本位


def test_short_chandelier_only_moves_down():
    pm = _short_pm()
    pm.stage = 2
    pm.stop_loss = 100.0
    # 吊燈 98 → 止損下移至 98
    assert pm.manage_position(96.0, 2.0, chandelier_long=0.0, bar_count=5,
                              chandelier_short=98.0) is ExitEvent.HOLDING
    assert pm.stop_loss == 98.0
    # 吊燈反彈到 99（較高）→ 不上移
    assert pm.manage_position(96.5, 2.0, chandelier_long=0.0, bar_count=6,
                              chandelier_short=99.0) is ExitEvent.HOLDING
    assert pm.stop_loss == 98.0
    # 收盤上穿「昨日已下移之止損」→ 頂部止損檢查攔截（STOP_LOSS，多方同構行為）
    assert pm.manage_position(98.1, 2.0, chandelier_long=0.0, bar_count=7,
                              chandelier_short=98.0) is ExitEvent.STOP_LOSS


def test_short_chandelier_cross_same_bar_as_update():
    """同根內吊燈下移後被收盤上穿 → CHANDELIER 事件（鏡像多方語意）。"""
    pm = _short_pm()
    pm.stage = 2
    pm.stop_loss = 100.0
    assert pm.manage_position(98.5, 2.0, chandelier_long=0.0, bar_count=5,
                              chandelier_short=98.0) is ExitEvent.CHANDELIER


def test_short_stage2_missing_chandelier_failfast():
    pm = _short_pm()
    pm.stage = 2
    with pytest.raises(ValueError):
        pm.manage_position(96.0, 2.0, chandelier_long=0.0, bar_count=5)


def test_short_time_limit_stage1_only():
    pm = _short_pm()
    assert pm.manage_position(99.0, 2.0, chandelier_long=0.0, bar_count=15,
                              time_limit=15) is ExitEvent.TIME_LIMIT


# ---------------------------------------------------------------------------
# SC-002a（T007）：數值鏡像變換全鏈（常數 1 口）
# ---------------------------------------------------------------------------

def mirror_klines(df: pd.DataFrame) -> pd.DataFrame:
    """價格繞常數 C 翻轉：p' = 2C − p；high↔low 對調；量能不變（data-model 映射）。"""
    C = (float(df['high'].max()) + float(df['low'].min())) / 2.0
    return pd.DataFrame({
        'open': 2 * C - df['open'],
        'high': 2 * C - df['low'],
        'low': 2 * C - df['high'],
        'close': 2 * C - df['close'],
        'volume': df['volume'],
    }, index=df.index)


_EVENT_MIRROR = {"BUY": "SELL_SHORT", "SELL_HALF": "COVER_HALF", "SELL_ALL": "COVER_ALL"}


def _run(df, enable_short):
    eng = BacktestEngine(initial_capital=10_000_000.0)
    return eng.run_backtest(
        df, asset_class="futures",
        cost_model=FuturesCostModel(TXC, FuturesCostConfig()),
        sizer=OneLotSizer(), point_value=TXC.point_value,
        enable_short=enable_short, verbose=False,
    )


def test_mirror_transform_full_chain_symmetry():
    df = make_klines(300, freq="5min")
    mirrored = mirror_klines(df)

    long_res = _run(df, enable_short=False)             # 原序列：多方（long-only）
    short_res = _run(mirrored, enable_short=True)       # 翻轉序列：空方應鏡像出現

    lt = long_res["trades"]
    st = short_res["trades"]
    assert not lt.empty, "原序列應產生多方交易（make_klines 既有性質）"

    long_seq = [(r["datetime"], r["action"]) for _, r in lt.iterrows()]
    short_actions = {"SELL_SHORT", "COVER_HALF", "COVER_ALL"}
    short_seq = [(r["datetime"], r["action"]) for _, r in st.iterrows()
                 if r["action"] in short_actions]

    # 翻轉序列上不應出現多方交易（原序列之看跌時刻在翻轉後成看漲，但原序列
    # 為上升趨勢、其空方情境不過濾網——若此斷言失敗代表存在未預期的方向洩漏）
    long_in_mirror = [a for _, a in st.iterrows() if a["action"] in {"BUY", "SELL_HALF", "SELL_ALL"}]
    assert not long_in_mirror, f"翻轉序列出現多方交易 {len(long_in_mirror)} 筆——鏡像不純"

    # 根位相同、事件類型鏡像
    assert len(short_seq) == len(long_seq), (
        f"多方 {len(long_seq)} 筆 vs 鏡像空方 {len(short_seq)} 筆——事件數不對應")
    for (lt_time, lt_act), (st_time, st_act) in zip(long_seq, short_seq):
        assert st_time == lt_time, f"根位不對應：多 {lt_time} vs 空 {st_time}"
        assert st_act == _EVENT_MIRROR[lt_act], (
            f"事件不鏡像：{lt_act}@{lt_time} → {st_act}")
