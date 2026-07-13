# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 007 — MSS fractal 反轉語意的單元/真值表測試 (US1)。

涵蓋：
  (a) detect_swing_points：對稱碎形 swing 高/低點，含邊界不足恆 False。
  (b) classify_structure：由已確認樞紐序列判 HH/HL/LH/LL 與 trend_bias。
  (c) detect_market_structure 校正後 MSS：上升結構跌破最近 HL→看跌反轉、
      下降結構突破最近 LH→看漲反轉；位移（量能）為必要條件；FVG 閘門套於新 MSS。
  (d) SC-001：存在 mss==±1 而同向 bos 不成立（mss ⊄ bos）。

樞紐/結構皆為「已確認」序列（碎形確認延遲 n 根，看前偏誤防禦見 test_lookahead_bias.py）。
"""

import numpy as np
import pandas as pd

from ladder_system import (
    detect_swing_points,
    classify_structure,
    detect_market_structure,
)


def _ohlcv(highs, lows, closes, volumes):
    idx = pd.date_range("2026-01-01 09:00:00", periods=len(highs), freq="1min")
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


# ---------------------------------------------------------------------------
# 共用 fixture：上升 zigzag（HH/HL）+ 第 9 根看跌反轉；及其鏡像下降版
# ---------------------------------------------------------------------------

def _uptrend_then_breakdown(*, high_at_break, close_at_break, vol_at_break):
    #  bar:     0     1     2     3     4     5     6     7     8      9
    highs = [11, 14, 13, 16, 15, 18, 17, 20, 19, high_at_break]
    lows = [10, 13, 12, 15, 14, 17, 16, 19, 18, 13]
    closes = [10.5, 13.5, 12.5, 15.5, 14.5, 17.5, 16.5, 19.5, 18.5, close_at_break]
    vols = [1000] * 9 + [vol_at_break]
    return _ohlcv(highs, lows, closes, vols)


def _downtrend_then_breakup(*, low_at_break, close_at_break, vol_at_break):
    highs = [20, 17, 18, 15, 16, 13, 14, 11, 12, 16]
    lows = [19, 16, 17, 14, 15, 12, 13, 10, 11, low_at_break]
    closes = [19.5, 16.5, 17.5, 14.5, 15.5, 12.5, 13.5, 10.5, 11.5, close_at_break]
    vols = [1000] * 9 + [vol_at_break]
    return _ohlcv(highs, lows, closes, vols)


# ---------------------------------------------------------------------------
# (a) detect_swing_points
# ---------------------------------------------------------------------------

def test_detect_swing_points_n1():
    highs = [10, 12, 11, 9, 13, 8]
    lows = [8, 9, 7, 5, 6, 4]
    closes = [9, 10, 9, 7, 9, 6]
    sp = detect_swing_points(_ohlcv(highs, lows, closes, [1000] * 6), n=1)
    assert list(sp["is_swing_high"]) == [False, True, False, False, True, False]
    assert list(sp["is_swing_low"]) == [False, False, False, True, False, False]
    assert sp["swing_high_val"].iloc[1] == 12
    assert sp["swing_low_val"].iloc[3] == 5
    assert pd.isna(sp["swing_high_val"].iloc[2])


def test_swing_points_edges_false():
    # 單調遞增：無內部極值；且前 n / 後 n 根窗口不足恆 False
    sp = detect_swing_points(_ohlcv([1, 2, 3, 4, 5], [0, 1, 2, 3, 4], [1, 2, 3, 4, 5], [1000] * 5), n=2)
    assert not sp["is_swing_high"].iloc[:2].any()
    assert not sp["is_swing_high"].iloc[-2:].any()
    assert not sp["is_swing_low"].iloc[:2].any()


# ---------------------------------------------------------------------------
# (b) classify_structure：趨勢偏向由已確認樞紐序列判定
# ---------------------------------------------------------------------------

def test_structure_trend_bias_uptrend():
    df = _uptrend_then_breakdown(high_at_break=17, close_at_break=14, vol_at_break=1000)
    st = classify_structure(df, n=1)
    assert st["trend_bias"].iloc[0] == 0          # 起點：結構不明
    assert st["trend_bias"].iloc[8] == 1          # 上升結構已確立
    assert st["conf_swing_low"].iloc[9] == 16     # 最近已確認 HL(=swing low)


def test_structure_trend_bias_downtrend():
    df = _downtrend_then_breakup(low_at_break=12, close_at_break=13, vol_at_break=1000)
    st = classify_structure(df, n=1)
    assert st["trend_bias"].iloc[8] == -1
    assert st["conf_swing_high"].iloc[9] == 14    # 最近已確認 LH(=swing high)


# ---------------------------------------------------------------------------
# (c) 校正後 MSS + (d) SC-001（mss ⊄ bos）
# ---------------------------------------------------------------------------

def test_mss_bearish_reversal_and_not_subset_of_bos():
    df = _uptrend_then_breakdown(high_at_break=17, close_at_break=14, vol_at_break=3000)
    mss, bos = detect_market_structure(df, period=5, swing_n=1, volume_mult=1.5, use_fvg=False)
    # 上升結構中跌破最近 HL(16) + 爆量 → 看跌反轉 MSS
    assert mss.iloc[9] == -1
    # 同 bar 同向 BOS 不成立（close=14 未跌破 rolling_low）→ mss ⊄ bos（SC-001）
    assert bos.iloc[9] == 0


def test_mss_bullish_reversal_in_downtrend():
    # 下降結構中收盤突破最近已確認 LH(14) + 爆量 → 看漲反轉
    df = _downtrend_then_breakup(low_at_break=12, close_at_break=15, vol_at_break=3000)
    mss, bos = detect_market_structure(df, period=5, swing_n=1, volume_mult=1.5, use_fvg=False)
    assert mss.iloc[9] == 1
    assert bos.iloc[9] == 0


def test_mss_requires_displacement():
    # 同樣的反向突破，但無爆量 → 位移不成立 → 不觸發 MSS
    df = _uptrend_then_breakdown(high_at_break=17, close_at_break=14, vol_at_break=1000)
    mss, _ = detect_market_structure(df, period=5, swing_n=1, volume_mult=1.5, use_fvg=False)
    assert mss.iloc[9] == 0


def test_mss_reversal_fvg_gate():
    # 向下 FVG 需 high(9) < low(7)=19
    with_fvg = _uptrend_then_breakdown(high_at_break=17, close_at_break=14, vol_at_break=3000)  # 17<19 → 有 FVG
    no_fvg = _uptrend_then_breakdown(high_at_break=20, close_at_break=14, vol_at_break=3000)     # 20≮19 → 無 FVG
    m_with, _ = detect_market_structure(with_fvg, period=5, swing_n=1, volume_mult=1.5, use_fvg=True, fvg_lookback=1)
    m_wo, _ = detect_market_structure(no_fvg, period=5, swing_n=1, volume_mult=1.5, use_fvg=True, fvg_lookback=1)
    assert m_with.iloc[9] == -1   # 有同向 FVG → 保留
    assert m_wo.iloc[9] == 0       # 無同向 FVG → 歸零


def test_mss_only_zeroes_never_adds_under_fvg():
    # FVG 閘門只把 ±1 收斂為 0，不新增訊號（延續 spec 002 性質，套於新 MSS）
    df = _uptrend_then_breakdown(high_at_break=20, close_at_break=14, vol_at_break=3000)
    off, _ = detect_market_structure(df, period=5, swing_n=1, volume_mult=1.5, use_fvg=False)
    on, _ = detect_market_structure(df, period=5, swing_n=1, volume_mult=1.5, use_fvg=True, fvg_lookback=1)
    assert ((off == 0) & (on != 0)).sum() == 0
