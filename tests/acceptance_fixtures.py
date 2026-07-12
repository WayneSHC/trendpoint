# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 004 驗收測試共用合成 K 線建構器。

所有建構器固定 seed、離線、生成 < 1s。OHLC 滿足
low <= min(open, close) <= max(open, close) <= high（離群變體除外）。
5 分鐘線模擬台股盤（09:00–13:25，每日 54 根，跨多個交易日），
讓依日分組的三關價/daily_open 邏輯有真實的日界可用。
"""

import numpy as np
import pandas as pd

_TW_SESSION_BARS = 54  # 09:00 起每 5 分鐘一根至 13:25


def _session_index(n: int, freq: str) -> pd.DatetimeIndex:
    if freq == "1D":
        return pd.bdate_range(start="2024-01-02", periods=n)
    if freq != "5min":
        raise ValueError(f"不支援的 freq: {freq}")
    days = pd.bdate_range(start="2024-01-02", periods=(n // _TW_SESSION_BARS) + 2)
    stamps = []
    for day in days:
        session = pd.date_range(
            start=day + pd.Timedelta(hours=9), periods=_TW_SESSION_BARS, freq="5min"
        )
        stamps.extend(session)
        if len(stamps) >= n:
            break
    return pd.DatetimeIndex(stamps[:n], name="datetime")


def make_klines(n: int, freq: str = "5min", seed: int = 42) -> pd.DataFrame:
    """固定 seed 的隨機漫步 K 線。"""
    rng = np.random.default_rng(seed)
    idx = _session_index(n, freq)

    log_returns = rng.normal(loc=0.0001, scale=0.005, size=n)
    close = 100.0 * np.exp(np.cumsum(log_returns))
    open_ = np.empty(n)
    open_[0] = 100.0
    open_[1:] = close[:-1]

    span = rng.uniform(0.0, 0.004, size=n)
    body_high = np.maximum(open_, close)
    body_low = np.minimum(open_, close)
    high = body_high * (1.0 + span)
    low = body_low * (1.0 - span)
    volume = np.round(rng.lognormal(mean=13.0, sigma=0.4, size=n)).astype(np.int64)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def make_klines_with_gap(
    n: int, gap_at: int, gap_len: int = 3, freq: str = "5min", seed: int = 42
) -> pd.DataFrame:
    """中段 gap_len 根整列 NaN（模擬爬蟲漏抓），索引不變。"""
    if not 0 < gap_at < gap_at + gap_len < n:
        raise ValueError("gap 區間必須完整落在序列中段")
    df = make_klines(n, freq=freq, seed=seed)
    df.iloc[gap_at : gap_at + gap_len] = np.nan
    return df


def make_klines_with_outlier(
    n: int, at: int, kind: str, freq: str = "5min", seed: int = 42
) -> pd.DataFrame:
    """單根離群值：kind="zero" 該根價格歸零；kind="spike" 該根價格 ×1000。"""
    if not 0 < at < n:
        raise ValueError("離群位置必須落在序列內（且非首根）")
    df = make_klines(n, freq=freq, seed=seed)
    price_cols = ["open", "high", "low", "close"]
    if kind == "zero":
        df.iloc[at, [df.columns.get_loc(c) for c in price_cols]] = 0.0
    elif kind == "spike":
        df.iloc[at, [df.columns.get_loc(c) for c in price_cols]] *= 1000.0
    else:
        raise ValueError(f"不支援的 kind: {kind}")
    return df
