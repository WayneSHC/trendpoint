# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 盤中快照與累積歷史（spec 016）。

**本模組是研究路徑，永不進入訊號鏈。** 它持有的是跨執行累積的歷史資料，
與 `ladder_system.py` / `backtester.py` 沒有任何呼叫關係（方向為單向：
本案可讀既有模組，既有模組不得 import 本案）——由
`tests/test_intraday_isolation.py` 的靜態零引用檢查焊死。

## 三個設計決定，改動前請先讀 specs/016-intraday-evaluation-protocol/research.md

1. **價格正規化至 4 位小數**（R1）。這不是美觀考量：yfinance 對台股回傳的
   價格本質是 2 位小數，float64 的 1e-13 級雜訊若不截斷，每週合併都會報出
   數百筆假衝突，真正的資料修正就被淹沒。同一個決定順帶解掉 CSV 浮點往返的
   確定性與指紋穩定性。

2. **合併採「先到者為準」**（R3）。既有累積值不被後續取數覆寫。表面理由是
   可重現性優先於新鮮度；更根本的理由是**看前偏誤**——資料源對已收盤資料的
   事後修正本質是「日後才知道的資訊」，讓它覆寫早先的評估窗，等於把未來
   資訊回填進過去的判定。衝突次數仍完整記錄，否則資料源異常會被這條規則掩蓋。

3. **切分只回傳窗口邊界，不跑回測、不做尋優**（R4）。`walk_forward.py` 的
   `WalkForwardAnalyzer` 把切分與網格尋優綁在一起，而策略調參明列於 spec 016
   範圍外；且它假設資料連續，本案的累積歷史必然帶斷裂。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 契約：specs/016-intraday-evaluation-protocol/contracts/accumulated-history.md
PRICE_COLUMNS = ("open", "high", "low", "close")
REQUIRED_COLUMNS = PRICE_COLUMNS + ("volume",)
PRICE_DECIMALS = 4
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
CHAIN_STATE_FILENAME = "chain_state.json"

# Gap.kind 的三個列舉值。下游一律以 kind 判斷，**不得**以人類可讀標籤字串比對
# （沿用 run_b_segment.py 情境表的既有教訓）。
GAP_WEEKEND_OR_HOLIDAY = "weekend_or_holiday"
GAP_SCHEDULE_LAPSE = "schedule_lapse"
GAP_CHAIN_RESTART = "chain_restart"
GAP_KINDS = (GAP_WEEKEND_OR_HOLIDAY, GAP_SCHEDULE_LAPSE, GAP_CHAIN_RESTART)


class SnapshotError(ValueError):
    """快照或累積歷史違反契約。一律硬失敗，不靜默修正。"""


# ---------------------------------------------------------------------------
# 正規化與指紋
# ---------------------------------------------------------------------------


def normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """把任意來源的盤中 OHLCV 正規化為 canonical 形式。

    正規化規則即契約的一部分（決定指紋），改動任一步都會使既有累積歷史的
    指紋全面失效：

    1. 欄名小寫、欄序固定為 open/high/low/close/volume
    2. 價格四捨五入至 4 位小數；volume 轉 int64
    3. 索引轉 datetime、排序遞增、重複時間戳保留首筆

    前置條件違反時硬失敗（空表、缺欄、負價、NaN、high < low）——
    這些是「看起來能跑、實際上資料是壞的」的失效模式。
    """
    if df is None or len(df) == 0:
        raise SnapshotError("空快照不得進入累積歷史")

    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]

    missing = set(REQUIRED_COLUMNS) - set(out.columns)
    if missing:
        raise SnapshotError(
            f"缺少必要欄位 {sorted(missing)}；實際欄位：{sorted(out.columns)}"
        )
    out = out[list(REQUIRED_COLUMNS)]

    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    out.index.name = "datetime"
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="first")]

    for col in PRICE_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce").round(PRICE_DECIMALS)
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce")

    if out[list(REQUIRED_COLUMNS)].isna().any().any():
        bad = out[out[list(REQUIRED_COLUMNS)].isna().any(axis=1)].index[:5]
        raise SnapshotError(f"存在 NaN 值，前幾筆時間戳：{list(bad)}")
    if (out[list(PRICE_COLUMNS)] <= 0).any().any():
        raise SnapshotError("存在非正價格")
    if (out["volume"] < 0).any():
        raise SnapshotError("存在負成交量")
    if (out["high"] < out["low"]).any():
        bad = out[out["high"] < out["low"]].index[:5]
        raise SnapshotError(f"high < low，前幾筆時間戳：{list(bad)}")

    out["volume"] = out["volume"].round().astype(np.int64)
    return out


def to_canonical_csv(df: pd.DataFrame) -> str:
    """序列化為 canonical CSV 字串（契約見 contracts/accumulated-history.md）。

    價格一律格式化為固定 4 位小數——**這是指紋穩定的前提**。
    交給 pandas 預設的 repr 格式化會讓 100.0 與 100.00000000000001 寫出不同
    字串，指紋隨之漂移。
    """
    out = df.copy()
    buf = io.StringIO()
    out.index = out.index.strftime(DATETIME_FORMAT)
    out.index.name = "datetime"
    for col in PRICE_COLUMNS:
        out[col] = out[col].map(lambda v: f"{v:.{PRICE_DECIMALS}f}")
    out["volume"] = out["volume"].astype(np.int64)
    out.to_csv(buf, lineterminator="\n")
    return buf.getvalue()


def fingerprint(df: pd.DataFrame) -> str:
    """正規化後 CSV 位元組的 SHA-256。

    指紋是「結論綁定到資料版本」的機制（FR-004）：資料源本身不可重現
    （同一請求相隔 56 分鐘得 7 → 5 個來回），故可重現性只能定義為
    「對固定快照可重現」，而指紋就是那個「固定」的識別。
    """
    return hashlib.sha256(to_canonical_csv(df).encode("utf-8")).hexdigest()


def describe(ticker: str, df: pd.DataFrame) -> dict:
    """快照的中繼資料。`trading_days` 以相異日期計，非以根數推估。"""
    return {
        "ticker": ticker,
        "fingerprint": fingerprint(df),
        "bars": int(len(df)),
        "trading_days": int(pd.Series(df.index.date).nunique()),
        "first_ts": df.index[0].strftime(DATETIME_FORMAT),
        "last_ts": df.index[-1].strftime(DATETIME_FORMAT),
    }


# ---------------------------------------------------------------------------
# CSV 落地
# ---------------------------------------------------------------------------


def _safe_name(ticker: str) -> str:
    return ticker.replace(".", "_")


def history_path(state_dir: str, ticker: str) -> str:
    return os.path.join(state_dir, f"{_safe_name(ticker)}.csv")


def write_history(state_dir: str, ticker: str, df: pd.DataFrame) -> str:
    """寫出 canonical CSV。已正規化的輸入寫出後再讀入必得同一張表。"""
    os.makedirs(state_dir, exist_ok=True)
    path = history_path(state_dir, ticker)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(to_canonical_csv(df))
    return path


def read_history(state_dir: str, ticker: str) -> Optional[pd.DataFrame]:
    """讀入 canonical CSV；檔案不存在回傳 None（呼叫端據此判定鏈結起點）。

    索引非嚴格遞增時硬失敗——那代表檔案被外部改壞，靜默排序會讓後續的
    合併與切分建立在一份「看起來正常」的壞資料上。
    """
    path = history_path(state_dir, ticker)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    if "datetime" not in df.columns:
        raise SnapshotError(f"{path} 缺少 datetime 欄")
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime")
    if not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise SnapshotError(f"{path} 的索引非嚴格遞增或含重複時間戳")
    return normalize_frame(df)


def list_tickers(state_dir: str) -> List[str]:
    """列出 state_dir 內的標的，依字串排序（確定性所需）。"""
    if not os.path.isdir(state_dir):
        return []
    names = []
    for fn in os.listdir(state_dir):
        if fn.endswith(".csv"):
            names.append(fn[: -len(".csv")].replace("_", "."))
    return sorted(names)


def write_chain_state(
    state_dir: str, chain_origin: str, chain_broken: bool, tickers: dict,
    criteria_version: str = "",
) -> str:
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, CHAIN_STATE_FILENAME)
    payload = {
        "chain_origin": chain_origin,
        "chain_broken": bool(chain_broken),
        "criteria_version": criteria_version,
        "tickers": tickers,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return path


def read_chain_state(state_dir: str) -> Optional[dict]:
    path = os.path.join(state_dir, CHAIN_STATE_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 合併
# ---------------------------------------------------------------------------


@dataclass
class MergeEvent:
    """一次併入的完整記錄。衝突計數存在的理由：先到者為準會**吞掉**新值，
    若不計數，資料源異常（某週衝突暴增）就會被這條規則掩蓋成靜默。"""

    merged_at_fingerprint: str
    bars_before: int
    bars_after: int
    bars_added: int
    overlap_bars: int
    conflicts: int
    conflict_first_ts: Optional[str] = None
    conflict_last_ts: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "merged_at_fingerprint": self.merged_at_fingerprint,
            "bars_before": self.bars_before,
            "bars_after": self.bars_after,
            "bars_added": self.bars_added,
            "overlap_bars": self.overlap_bars,
            "conflicts": self.conflicts,
            "conflict_first_ts": self.conflict_first_ts,
            "conflict_last_ts": self.conflict_last_ts,
        }


def merge_history(
    existing: Optional[pd.DataFrame], incoming: pd.DataFrame
) -> Tuple[pd.DataFrame, MergeEvent]:
    """把 `incoming` 併入 `existing`，回傳 (合併後序列, MergeEvent)。

    **先到者為準**：重疊時間戳保留 `existing` 的值，`incoming` 的值捨棄。
    捨棄前逐欄比較，任一欄不同即計為一次衝突。

    後置條件：索引嚴格遞增、無重複、根數不少於任一輸入。
    """
    incoming = normalize_frame(incoming)
    if existing is None or len(existing) == 0:
        return incoming, MergeEvent(
            merged_at_fingerprint=fingerprint(incoming),
            bars_before=0,
            bars_after=len(incoming),
            bars_added=len(incoming),
            overlap_bars=0,
            conflicts=0,
        )

    existing = normalize_frame(existing)
    overlap_idx = existing.index.intersection(incoming.index)

    conflicts = 0
    conflict_first = conflict_last = None
    if len(overlap_idx) > 0:
        left = existing.loc[overlap_idx, list(REQUIRED_COLUMNS)]
        right = incoming.loc[overlap_idx, list(REQUIRED_COLUMNS)]
        differing = (left != right).any(axis=1)
        conflicts = int(differing.sum())
        if conflicts:
            ts = overlap_idx[differing.values]
            conflict_first = ts[0].strftime(DATETIME_FORMAT)
            conflict_last = ts[-1].strftime(DATETIME_FORMAT)

    # 先到者為準：只取 incoming 中 existing 沒有的時間戳。
    fresh = incoming.loc[incoming.index.difference(existing.index)]
    merged = pd.concat([existing, fresh]).sort_index()

    if not merged.index.is_monotonic_increasing or merged.index.has_duplicates:
        raise SnapshotError("合併後索引非嚴格遞增或含重複——合併邏輯有誤")
    if len(merged) < max(len(existing), len(incoming)):
        raise SnapshotError("合併後根數少於任一輸入——合併邏輯有誤")

    return merged, MergeEvent(
        merged_at_fingerprint=fingerprint(incoming),
        bars_before=len(existing),
        bars_after=len(merged),
        bars_added=len(merged) - len(existing),
        overlap_bars=int(len(overlap_idx)),
        conflicts=conflicts,
        conflict_first_ts=conflict_first,
        conflict_last_ts=conflict_last,
    )


# ---------------------------------------------------------------------------
# 斷裂偵測
# ---------------------------------------------------------------------------


@dataclass
class Gap:
    start_ts: str
    end_ts: str
    missing_trading_days: int
    kind: str

    def to_dict(self) -> dict:
        return {
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "missing_trading_days": self.missing_trading_days,
            "kind": self.kind,
        }


def detect_gaps(df: pd.DataFrame, max_normal_gap_days: int = 4) -> List[Gap]:
    """偵測時間斷裂。

    `max_normal_gap_days` 預設 4：連假（含補班調整）在台股最長約 4 個日曆日
    的營業日缺口；超過即視為排程中斷。此值刻意寬鬆——把排程中斷誤判為連假
    的代價（測試窗跨越真實斷裂）遠大於反向誤判的代價（多切一刀）。
    """
    if len(df) == 0:
        return []
    days = sorted({ts.date() for ts in df.index})
    gaps: List[Gap] = []
    for prev, cur in zip(days, days[1:]):
        business_days = int(np.busday_count(prev, cur)) - 1
        if business_days <= 0:
            continue
        kind = (
            GAP_WEEKEND_OR_HOLIDAY
            if business_days <= max_normal_gap_days
            else GAP_SCHEDULE_LAPSE
        )
        if kind == GAP_WEEKEND_OR_HOLIDAY:
            continue
        gaps.append(
            Gap(
                start_ts=pd.Timestamp(prev).strftime(DATETIME_FORMAT),
                end_ts=pd.Timestamp(cur).strftime(DATETIME_FORMAT),
                missing_trading_days=business_days,
                kind=kind,
            )
        )
    return gaps


def chain_restart_gap(chain_origin: str) -> Gap:
    """鏈結重起所造成的斷裂。與排程中斷分開標示——兩者的補救方式不同。"""
    return Gap(
        start_ts=chain_origin,
        end_ts=chain_origin,
        missing_trading_days=0,
        kind=GAP_CHAIN_RESTART,
    )


# ---------------------------------------------------------------------------
# 窗口切分
# ---------------------------------------------------------------------------


@dataclass
class WindowSplit:
    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    test_bars: int

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "test_bars": self.test_bars,
        }


@dataclass
class SplitResult:
    splits: List[WindowSplit] = field(default_factory=list)
    sufficient: bool = False
    shortfall_trading_days: int = 0

    def to_dict(self) -> dict:
        return {
            "splits": [s.to_dict() for s in self.splits],
            "sufficient": self.sufficient,
            "shortfall_trading_days": self.shortfall_trading_days,
        }


def split_windows(
    df: pd.DataFrame,
    n_windows: int,
    train_ratio: float,
    min_trading_days_per_window: int = 10,
) -> SplitResult:
    """gap-aware 的樣本外切分。**只回傳邊界，不跑回測、不做尋優**。

    切分限於**同一連續段內**：任何 `schedule_lapse` / `chain_restart` 斷裂
    都會把序列切成獨立段，窗口不得跨越（FR-016）。取最長的一段來切——
    跨段拼接會讓「測試窗」橫跨一個沒有資料的月份，那不是樣本外，是幻覺。

    長度不足時**不回傳部分結果**：回傳空 splits 加量化差距，由報告層轉為
    FR-015 的明示訊息。回半套結果會讓下游以為切分成功了。
    """
    if n_windows < 1:
        raise SnapshotError("n_windows 必須 >= 1")
    if not 0.0 < train_ratio < 1.0:
        raise SnapshotError("train_ratio 必須落在 (0, 1)")

    segments = _continuous_segments(df)
    needed_days = int(np.ceil(n_windows * min_trading_days_per_window / (1.0 - train_ratio)))

    if not segments:
        return SplitResult([], False, needed_days)

    best = max(segments, key=lambda s: pd.Series(s.index.date).nunique())
    best_days = sorted({ts.date() for ts in best.index})

    if len(best_days) < needed_days:
        return SplitResult([], False, needed_days - len(best_days))

    # 錨定切分：測試段等分於序列尾端，訓練段為該測試段之前的全部歷史。
    test_total_days = int(len(best_days) * (1.0 - train_ratio))
    per_window = test_total_days // n_windows
    if per_window < min_trading_days_per_window:
        return SplitResult(
            [], False, int(np.ceil(n_windows * min_trading_days_per_window
                                   / (1.0 - train_ratio))) - len(best_days)
        )

    splits: List[WindowSplit] = []
    first_test_day_pos = len(best_days) - test_total_days
    for i in range(n_windows):
        t0 = first_test_day_pos + i * per_window
        t1 = t0 + per_window - 1
        train_days = best_days[:t0]
        test_days = best_days[t0 : t1 + 1]
        if not train_days or not test_days:
            return SplitResult([], False, needed_days - len(best_days))
        test_mask = [ts.date() in set(test_days) for ts in best.index]
        splits.append(
            WindowSplit(
                index=i,
                train_start=_day_str(best_days[0]),
                train_end=_day_str(train_days[-1]),
                test_start=_day_str(test_days[0]),
                test_end=_day_str(test_days[-1]),
                test_bars=int(sum(test_mask)),
            )
        )

    # 後置條件：測試窗兩兩不重疊、訓練嚴格早於測試。
    for a, b in zip(splits, splits[1:]):
        if not b.test_start > a.test_end:
            raise SnapshotError("測試窗重疊——切分邏輯有誤")
    for s in splits:
        if not s.train_end < s.test_start:
            raise SnapshotError("訓練窗未嚴格早於測試窗——切分邏輯有誤")

    return SplitResult(splits, True, 0)


def _day_str(d) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")


def _continuous_segments(df: pd.DataFrame) -> List[pd.DataFrame]:
    """依非假日型斷裂把序列切成連續段。"""
    if len(df) == 0:
        return []
    gaps = detect_gaps(df)
    if not gaps:
        return [df]
    segments = []
    cursor = df.index[0]
    for g in gaps:
        end = pd.Timestamp(g.start_ts)
        seg = df.loc[(df.index >= cursor) & (df.index <= end + pd.Timedelta(days=1))]
        seg = seg.loc[seg.index.normalize() <= end.normalize()]
        if len(seg):
            segments.append(seg)
        cursor = pd.Timestamp(g.end_ts)
    tail = df.loc[df.index >= cursor]
    if len(tail):
        segments.append(tail)
    return segments
