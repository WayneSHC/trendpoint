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


def with_unadj(df: pd.DataFrame) -> pd.DataFrame:
    """補上未調整參考價欄位（spec 011 FR-009）：合成序列無 back-adjust，故 unadj_* = 原值。

    期貨回測路徑要求資料攜帶 unadj_open/high/low/close（FR-008 缺欄硬失敗）。
    真實 TXF 連續序列由 rollover.build_continuous 於平移前擷取；合成／mock 序列
    沒有轉倉調整，兩組價格恆等——本 helper 即表述此等價退化。

    刻意不併入 make_klines：新寫的期貨測試若忘了帶欄位，應該撞上硬失敗並讀到
    錯誤訊息（從而知道有兩組價格基準），而不是默默通過。

    **只補缺欄、不覆寫既有值**：這讓它能安全掛在各測試檔的執行 funnel 上，
    而不會破壞刻意構造「調整後 ≠ 未調整」情境的測試。
    """
    out = df.copy()
    for col in ("open", "high", "low", "close"):
        if f"unadj_{col}" not in out.columns:
            out[f"unadj_{col}"] = out[col]
    return out


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
