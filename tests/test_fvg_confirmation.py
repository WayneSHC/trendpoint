# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 002 — FVG（公平價值缺口）偵測與其對 MSS 的閘門性質。

> spec 007 註記：MSS 語意已由「高量同向 BOS」校正為 fractal 反轉訊號，故原
> 「flat + 同向突破 → MSS」的真值表與「use_fvg=False 與 spec 001 位元一致」的
> 迴歸錨點已失效並移除。校正後的 MSS/FVG 反轉真值表見 `test_mss_reversal.py`。
> 本檔保留仍然成立的性質：

  (a) `_detect_fvg` 對手工三根缺口 df 的偵測正確（含前 2 根 False）。
  (b) BOS 續勢語意不受 spec 007 影響——BOS 仍等於 spec 001 raw 公式（迴歸錨點）。
  (c) FVG 閘門對 BOS 無影響；且只把 MSS 的 ±1 收斂為 0，絕不新增訊號。
"""

import pandas as pd

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
# (a) _detect_fvg：三根缺口偵測（spec 007 未更動）
# ---------------------------------------------------------------------------

def test_detect_fvg_up_and_down_gaps():
    """向上缺口 low(t)>high(t-2)、向下缺口 high(t)<low(t-2)，前 2 根恆 False。"""
    #      bar:   0    1    2     3    4    5
    highs = [10, 11, 20, 12, 6, 7]
    lows = [8, 9, 18, 5, 4, 5]
    closes = [9, 10, 19, 8, 5, 6]
    vols = [1000] * 6
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
    try:
        _detect_fvg(df, "sideways")
    except ValueError:
        return
    raise AssertionError("_detect_fvg 應對非法 direction 拋出 ValueError")


# ---------------------------------------------------------------------------
# (b) BOS 續勢語意不受 spec 007 影響——仍等於 spec 001 raw 公式
# ---------------------------------------------------------------------------

def _raw_bos(df, period):
    """獨立重算 BOS（spec 001 公式），作為 BOS 未變的迴歸錨點。"""
    rolling_high = df["high"].rolling(window=period).max().shift(1)
    rolling_low = df["low"].rolling(window=period).min().shift(1)
    bos = pd.Series(0, index=df.index)
    bos[df["close"] > rolling_high] = 1
    bos[df["close"] < rolling_low] = -1
    return bos


def test_bos_matches_spec001_formula():
    df = make_klines(400, freq="5min")
    _, bos = detect_market_structure(df, period=10, use_fvg=False)
    pd.testing.assert_series_equal(bos, _raw_bos(df, 10), check_exact=True, check_names=False)


# ---------------------------------------------------------------------------
# (c) FVG 閘門對 BOS 無影響；且只把 MSS ±1 收斂為 0，絕不新增訊號
# ---------------------------------------------------------------------------

def test_bos_invariant_to_fvg():
    df = make_klines(400, freq="5min")
    _, bos_off = detect_market_structure(df, period=10, use_fvg=False)
    _, bos_on = detect_market_structure(df, period=10, use_fvg=True, fvg_lookback=3)
    pd.testing.assert_series_equal(bos_off, bos_on, check_exact=True, check_names=False)


def test_fvg_only_zeroes_mss_never_adds():
    """use_fvg=True 只把 ±1 收斂為 0：不改號、不新增訊號（套於校正後 MSS）。"""
    df = make_klines(400, freq="5min")
    mss_off, _ = detect_market_structure(df, period=10, use_fvg=False)
    mss_on, _ = detect_market_structure(df, period=10, use_fvg=True, fvg_lookback=3)

    changed = mss_on != mss_off
    assert (mss_on[changed] == 0).all(), "FVG 閘門改變了非零訊號的方向或新增了訊號"
    assert ((mss_off == 0) & (mss_on != 0)).sum() == 0, "FVG 憑空新增了 MSS 訊號"
