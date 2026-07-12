# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 002 — MSS 之 FVG（公平價值缺口）確認的單元與迴歸測試。

四塊：
  (a) `_detect_fvg` 對手工三根缺口 df 的偵測正確（含前 2 根 False）。
  (b) mss 閘門真值表五種情形（data-model.md §2），以四個孤立小 df 各釘一列。
  (c) 基準重現——同一 df 上 `detect_market_structure(use_fvg=False)` 與獨立重算的
      raw MSS/BOS 逐位元相同（迴歸閘門的單元層錨點）。
  (d) `bos_signal` 在 use_fvg True/False 皆不變。
"""

import pandas as pd
import pytest

from acceptance_fixtures import make_klines
from ladder_system import _detect_fvg, detect_market_structure


def _ohlcv(highs, lows, closes, volumes):
    """由等長清單組出最小 OHLCV df（open 補 close，僅供結構偵測用）。"""
    idx = pd.date_range("2026-01-01 09:00:00", periods=len(highs), freq="1min")
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# (a) _detect_fvg：三根缺口偵測
# ---------------------------------------------------------------------------

def test_detect_fvg_up_and_down_gaps():
    """向上缺口 low(t)>high(t-2)、向下缺口 high(t)<low(t-2)，前 2 根恆 False。"""
    #      bar:   0    1    2     3    4    5
    highs =     [10,  11,  20,   12,  6,   7]
    lows =      [8,   9,   18,   5,   4,   5]
    closes =    [9,   10,  19,   8,   5,   6]
    vols =      [1000]*6
    df = _ohlcv(highs, lows, closes, vols)

    up = _detect_fvg(df, "up")
    down = _detect_fvg(df, "down")

    # 向上缺口只在 bar2：low(2)=18 > high(0)=10
    assert list(up) == [False, False, True, False, False, False]
    # 向下缺口只在 bar4：high(4)=6 < low(2)=18
    assert list(down) == [False, False, False, False, True, False]

    # 契約：dtype bool、前 2 根 False（shift(2) NaN）
    assert up.dtype == bool and down.dtype == bool
    assert not up.iloc[0] and not up.iloc[1]
    assert not down.iloc[0] and not down.iloc[1]


def test_detect_fvg_rejects_bad_direction():
    df = _ohlcv([10, 11, 12], [8, 9, 10], [9, 10, 11], [1000, 1000, 1000])
    with pytest.raises(ValueError):
        _detect_fvg(df, "sideways")


# ---------------------------------------------------------------------------
# (b) mss 閘門真值表（data-model.md §2），period=3、fvg_lookback=1 隔離每列
# ---------------------------------------------------------------------------
# 前 5 根平盤（high=100/low=98/close=99/vol=1000）不觸發任何 MSS；
# 第 5 根（index 5）為唯一突破 + 爆量根，僅以 high/low 控制是否伴隨同向 FVG。

_PERIOD = 3
_FLAT_H, _FLAT_L, _FLAT_C, _FLAT_V = 100, 98, 99, 1000
_BREAK_V = 2000  # > 1.5 * 1000


def _flat_prefix():
    return (
        [_FLAT_H] * 5, [_FLAT_L] * 5, [_FLAT_C] * 5, [_FLAT_V] * 5,
    )


def _bull_break_df(low_at_break):
    """第 5 根看漲突破（close=105>100、爆量）；low 決定是否有向上 FVG。"""
    h, l, c, v = _flat_prefix()
    h, l, c, v = list(h), list(l), list(c), list(v)
    h.append(110); l.append(low_at_break); c.append(105); v.append(_BREAK_V)
    return _ohlcv(h, l, c, v)


def _bear_break_df(high_at_break):
    """第 5 根看跌突破（close=90<98、爆量）；high 決定是否有向下 FVG。"""
    h, l, c, v = _flat_prefix()
    h, l, c, v = list(h), list(l), list(c), list(v)
    h.append(high_at_break); l.append(85); c.append(90); v.append(_BREAK_V)
    return _ohlcv(h, l, c, v)


def _mss(df, use_fvg):
    mss, _ = detect_market_structure(df, period=_PERIOD, use_fvg=use_fvg, fvg_lookback=1)
    return mss


def test_truthtable_bull_with_fvg_kept():
    # low(5)=101 > high(3)=100 → 向上 FVG 存在 → +1 保留
    df = _bull_break_df(low_at_break=101)
    assert _mss(df, use_fvg=False).iloc[5] == 1, "前提失效：raw MSS 應為 +1"
    assert _mss(df, use_fvg=True).iloc[5] == 1


def test_truthtable_bull_without_fvg_zeroed():
    # low(5)=99 !> high(3)=100 → 無向上 FVG → 假訊號歸零
    df = _bull_break_df(low_at_break=99)
    assert _mss(df, use_fvg=False).iloc[5] == 1, "前提失效：raw MSS 應為 +1"
    assert _mss(df, use_fvg=True).iloc[5] == 0


def test_truthtable_bear_with_fvg_kept():
    # high(5)=95 < low(3)=98 → 向下 FVG 存在 → -1 保留
    df = _bear_break_df(high_at_break=95)
    assert _mss(df, use_fvg=False).iloc[5] == -1, "前提失效：raw MSS 應為 -1"
    assert _mss(df, use_fvg=True).iloc[5] == -1


def test_truthtable_bear_without_fvg_zeroed():
    # high(5)=99 !< low(3)=98 → 無向下 FVG → 假訊號歸零
    df = _bear_break_df(high_at_break=99)
    assert _mss(df, use_fvg=False).iloc[5] == -1, "前提失效：raw MSS 應為 -1"
    assert _mss(df, use_fvg=True).iloc[5] == 0


def test_truthtable_raw_zero_stays_zero():
    # 平盤根（raw_mss==0）在 use_fvg=True 下仍為 0（第 5 列）
    df = _bull_break_df(low_at_break=101)
    gated = _mss(df, use_fvg=True)
    raw = _mss(df, use_fvg=False)
    # 凡 raw==0 之列，gated 必為 0（FVG 只會收斂 ±1，不新增訊號）
    assert ((raw == 0) & (gated != 0)).sum() == 0


# ---------------------------------------------------------------------------
# (c) 基準重現：use_fvg=False 與獨立重算的 raw MSS/BOS 逐位元相同
# ---------------------------------------------------------------------------

def _raw_reference(df, period):
    """獨立重算 FVG 前的 MSS/BOS（spec 001 公式），作為迴歸錨點。"""
    rolling_high = df["high"].rolling(window=period).max().shift(1)
    rolling_low = df["low"].rolling(window=period).min().shift(1)
    vol_ma = df["volume"].rolling(window=period).mean().shift(1)
    strong = df["volume"] > (vol_ma * 1.5)

    bull_bos = df["close"] > rolling_high
    bear_bos = df["close"] < rolling_low
    bull_mss = bull_bos & strong
    bear_mss = bear_bos & strong

    bos = pd.Series(0, index=df.index)
    bos[bull_bos] = 1
    bos[bear_bos] = -1
    mss = pd.Series(0, index=df.index)
    mss[bull_mss] = 1
    mss[bear_mss] = -1
    return mss, bos


def test_baseline_reproduction_use_fvg_false():
    df = make_klines(400, freq="5min")
    ref_mss, ref_bos = _raw_reference(df, period=10)
    mss, bos = detect_market_structure(df, period=10, use_fvg=False)

    pd.testing.assert_series_equal(mss, ref_mss, check_exact=True, check_names=False)
    pd.testing.assert_series_equal(bos, ref_bos, check_exact=True, check_names=False)


# ---------------------------------------------------------------------------
# (d) bos_signal 在 use_fvg True/False 皆不變
# ---------------------------------------------------------------------------

def test_bos_invariant_to_fvg():
    df = make_klines(400, freq="5min")
    _, bos_off = detect_market_structure(df, period=10, use_fvg=False)
    _, bos_on = detect_market_structure(df, period=10, use_fvg=True, fvg_lookback=3)
    pd.testing.assert_series_equal(bos_off, bos_on, check_exact=True, check_names=False)


def test_fvg_only_zeroes_mss_never_adds():
    """use_fvg=True 只把 ±1 收斂為 0：不改號、不新增訊號。"""
    df = make_klines(400, freq="5min")
    mss_off, _ = detect_market_structure(df, period=10, use_fvg=False)
    mss_on, _ = detect_market_structure(df, period=10, use_fvg=True, fvg_lookback=3)

    # 每一列：on 要嘛等於 off，要嘛被歸零；絕不出現 off==0 卻 on!=0
    changed = mss_on != mss_off
    assert (mss_on[changed] == 0).all(), "FVG 閘門改變了非零訊號的方向或新增了訊號"
    assert ((mss_off == 0) & (mss_on != 0)).sum() == 0, "FVG 憑空新增了 MSS 訊號"
