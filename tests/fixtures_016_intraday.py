# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - spec 016 盤中評估協定的合成資料產生器（T004）。

**為什麼需要合成資料**：本案要驗收的邊界——重疊快照的衝突、鏈結中斷、
時間斷裂、窗口切分不足、零交易的四種成因——真實市場資料無法保證它們
同時出現在某個 60 天窗口內；且本開發容器的 agent proxy 對 yfinance 回 403，
測試不能依賴取數。故全部驗收走合成序列，與 spec 013 的
`tests/gate_fixtures.py` 同一理由。

全部產生器皆為決定性：固定 seed、無隨機呼叫以外的不確定性、無網路 I/O。
價格序列沿用 repo 既有的 `acceptance_fixtures.make_klines`（固定 seed 隨機漫步），
本模組只做**索引重排、切片與擾動**——這三件事才是本案的變因。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# repo root：直接以腳本執行時（產生示範累積歷史）需要它才 import 得到
# intraday_snapshot；經 pytest 執行時通常已在路徑上，重複插入無害。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acceptance_fixtures import make_klines  # noqa: E402

# 台股一般交易時段 09:00–13:30，5 分線每日 54 根（含 13:30 收盤棒）。
BARS_PER_DAY = 54


def intraday_frame(
    trading_days: int = 20,
    seed: int = 42,
    start: str = "2026-01-05",
    bars_per_day: int = BARS_PER_DAY,
) -> pd.DataFrame:
    """產生 `trading_days` 個交易日的 5 分線序列。

    刻意以「交易日數」而非「根數」為參數：本案幾乎所有門檻
    （lookback_days、shortfall_trading_days、每日根數變異係數）都以交易日計，
    以根數為介面會讓每個呼叫端各自做一次除法，遲早除錯。
    """
    n = trading_days * bars_per_day
    df = make_klines(n, freq="5min", seed=seed)
    idx = _session_index(trading_days, start=start, bars_per_day=bars_per_day)
    df = df.iloc[: len(idx)].copy()
    df.index = idx
    df.index.name = "datetime"
    return df


def _session_index(
    trading_days: int, start: str, bars_per_day: int = BARS_PER_DAY
) -> pd.DatetimeIndex:
    """台股交易時段索引：每個營業日 09:00 起、5 分一根。"""
    days = pd.bdate_range(start=start, periods=trading_days)
    stamps = []
    for day in days:
        stamps.extend(
            pd.date_range(
                start=day + pd.Timedelta(hours=9), periods=bars_per_day, freq="5min"
            )
        )
    return pd.DatetimeIndex(stamps, name="datetime")


def overlapping_pair(
    total_days: int = 30, overlap_days: int = 20, seed: int = 42
) -> tuple:
    """切出兩份**時間重疊**的快照，模擬相隔一週的兩次取數。

    回傳 (earlier, later)：earlier 覆蓋前段，later 覆蓋後段，兩者中間重疊
    `overlap_days` 個交易日且**數值完全相同**（無衝突的基準情境）。
    """
    if not 0 < overlap_days < total_days:
        raise ValueError("overlap_days 必須落在 (0, total_days) 之間")
    full = intraday_frame(total_days, seed=seed)
    days = sorted({ts.date() for ts in full.index})
    new_days = total_days - overlap_days

    # earlier 取前 overlap_days 天；later 自第 new_days 天起到尾。
    # 兩者的交集恰為 overlap_days 天，且該段在兩份中數值完全相同。
    earlier_days = set(days[:overlap_days])
    later_days = set(days[new_days:])

    earlier = full[[ts.date() in earlier_days for ts in full.index]].copy()
    later = full[[ts.date() in later_days for ts in full.index]].copy()
    return earlier, later


def with_conflicts(
    df: pd.DataFrame, n_conflicts: int = 3, delta: float = 0.5
) -> pd.DataFrame:
    """在序列**開頭**注入 `n_conflicts` 根數值不同的 K 線（模擬資料源事後修正）。

    刻意改在開頭：合併時開頭必然落在重疊區，衝突才一定會被觸發；
    若改在結尾，重疊範圍變動就可能讓衝突落到重疊區外而測不到。

    `delta` 取 0.5 而非 1e-9——正規化會把 1e-13 級雜訊抹平（research.md R1），
    要測「真正的資料修正」就得給一個抹不掉的差距。
    """
    if n_conflicts <= 0:
        return df.copy()
    out = df.copy()
    idx = out.index[:n_conflicts]
    for col in ("open", "high", "low", "close"):
        out.loc[idx, col] = out.loc[idx, col] + delta
    return out


def with_gap(df: pd.DataFrame, skip_days: int = 7) -> pd.DataFrame:
    """自序列中段整段抽掉 `skip_days` 個交易日，製造排程中斷型的斷裂。"""
    days = sorted({ts.date() for ts in df.index})
    if skip_days >= len(days) - 2:
        raise ValueError("skip_days 過大，抽掉後不剩兩段")
    mid = len(days) // 2
    dropped = set(days[mid : mid + skip_days])
    return df[[ts.date() not in dropped for ts in df.index]].copy()


def flat_frame(trading_days: int = 20, price: float = 100.0) -> pd.DataFrame:
    """完全無波動的序列：每根 OHLC 相同。

    用於零交易成因 `no_structure_signal`——沒有高低點就沒有結構訊號。
    """
    idx = _session_index(trading_days, start="2026-01-05")
    n = len(idx)
    return pd.DataFrame(
        {
            "open": np.full(n, price),
            "high": np.full(n, price),
            "low": np.full(n, price),
            "close": np.full(n, price),
            "volume": np.full(n, 1_000_000, dtype=np.int64),
        },
        index=idx,
    )


def quiet_frame(trading_days: int = 60, price: float = 100.0) -> pd.DataFrame:
    """**通過納入準則但跑不出交易**的序列：價格在兩個相鄰檔位間交替。

    `flat_frame` 不能拿來測完整管線的零交易路徑——它的價格完全不動，
    唯一價差為 0，`tick_ratio` 回傳 1.0，會在**納入準則**階段就被排除，
    根本走不到評估。本 fixture 保留一個微小但真實的檔位（0.01 / 100 = 1e-4，
    低於 max_tick_ratio），量能拉高使其通過所有準則，
    但結構上仍平坦到產不出可成交的訊號。
    """
    idx = _session_index(trading_days, start="2026-01-05")
    n = len(idx)
    close = np.where(np.arange(n) % 2 == 0, price, price + 0.01)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.01,
            "low": close - 0.01,
            "close": close,
            "volume": np.full(n, 5_000_000, dtype=np.int64),
        },
        index=idx,
    )


def low_volume_frame(trading_days: int = 20, seed: int = 42) -> pd.DataFrame:
    """量能極低的序列——用於納入準則的日均量門檻排除路徑。"""
    df = intraday_frame(trading_days, seed=seed)
    df["volume"] = 1_000
    return df


def ragged_frame(trading_days: int = 20, seed: int = 42) -> pd.DataFrame:
    """每日根數極不穩定的序列——用於每日根數變異係數門檻的排除路徑。"""
    df = intraday_frame(trading_days, seed=seed)
    days = sorted({ts.date() for ts in df.index})
    keep = []
    for i, ts in enumerate(df.index):
        day_pos = days.index(ts.date())
        # 偶數日只留前 10 根，奇數日全留 → 變異係數必然超標
        if day_pos % 2 == 0:
            day_mask = [t for t in df.index if t.date() == ts.date()]
            if day_mask.index(ts) >= 10:
                continue
        keep.append(ts)
    return df.loc[keep].copy()


def coarse_tick_frame(trading_days: int = 20, price: float = 10.0) -> pd.DataFrame:
    """價格檔位極粗的序列——用於檔位粒度門檻的排除路徑。"""
    idx = _session_index(trading_days, start="2026-01-05")
    n = len(idx)
    steps = np.arange(n) % 5
    close = price + steps * 1.0        # 1.0 / 10.0 = 0.1 遠超 max_tick_ratio
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 5_000_000, dtype=np.int64),
        },
        index=idx,
    )


def write_state_dir(
    path: str, frames: Optional[dict] = None, chain_broken: bool = True
) -> str:
    """把若干標的的序列寫成一個可餵給 CLI 的 `--state-dir`。

    僅供測試使用；正式寫入一律走 `intraday_snapshot.write_history`
    （此處亦轉呼叫它，避免測試自己造一套與契約不同的格式）。
    """
    import intraday_snapshot as isnap

    frames = frames or {"2330.TW": intraday_frame(30, seed=42)}
    os.makedirs(path, exist_ok=True)
    state = {}
    for ticker, df in sorted(frames.items()):
        norm = isnap.normalize_frame(df)
        isnap.write_history(path, ticker, norm)
        state[ticker] = isnap.describe(ticker, norm)
    isnap.write_chain_state(
        path,
        chain_origin=min(s["first_ts"] for s in state.values()),
        chain_broken=chain_broken,
        tickers={
            t: {
                "fingerprint": s["fingerprint"],
                "bars": s["bars"],
                "first_ts": s["first_ts"],
                "last_ts": s["last_ts"],
                "merge_events": [],
                "gaps": [],
            }
            for t, s in state.items()
        },
    )
    return path


def main() -> None:
    """產生一份可供 quickstart 直接使用的示範累積歷史。

    刻意**不**把這些 CSV 提交進版本庫：它們是合成的、隨時可重新產生，
    依憲章原則 VI 屬「可再生成的產物」。真正不可再生成的是 Actions
    artifact 上那條真實累積鏈——兩者不該混淆。

        python tests/fixtures_016_intraday.py /tmp/demo_state
    """
    import argparse

    ap = argparse.ArgumentParser(description="產生 spec 016 的示範累積歷史")
    ap.add_argument("state_dir", help="輸出目錄")
    ap.add_argument("--trading-days", type=int, default=200)
    args = ap.parse_args()

    write_state_dir(
        args.state_dir,
        {
            "2330.TW": intraday_frame(args.trading_days, seed=42),
            "2454.TW": intraday_frame(args.trading_days, seed=7),
        },
        chain_broken=False,
    )
    print(f"已產生示範累積歷史 → {args.state_dir}（每檔 {args.trading_days} 交易日）")


if __name__ == "__main__":
    main()
