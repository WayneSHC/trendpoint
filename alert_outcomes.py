# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 推播訊號的事後表現追蹤 (Alert Outcome Tracking，spec 015 A 段)

推播出去的訊號，系統原本不記得後來發生了什麼——`sent_alerts` 只有四欄去重鍵，
推播當下的價格與指標狀態只進訊息字串就丟掉。本模組建立**前瞻性的觀察樣本累積**：
偵測到訊號時落一列紀錄，事後回填 T+1／T+3／T+5 的日線收盤報酬。

## 三件必須先講清楚的事

1. **產出不是策略績效。** 無成本模型、無出場規則、未經樣本外驗證，
   且基準價（告警當下收盤）與衡量價（日線收盤）時基不同。呈現端必須標示，
   **不得與回測 KPI 並列**（spec FR-017）。

2. **本模組永不進入訊號鏈。** 它持有告警**發生之後**的價格——任何被
   `ladder_system` / `backtester` / 回測入口 import 的路徑都是未來函數的入口。
   由 `tests/test_alert_outcomes.py` 的靜態零引用檢查守門（SC-019）。

3. **JSONL 是單一真實來源，不新增 SQLite 表。** 排程環境的 `trendpoint.db`
   存活於 `actions/cache`（有逐出機制）；紀錄一旦遺失不可再生成。
   讓它從一開始就不住在快取裡，比事後搬出來簡單（research.md D1）。

架構：純函式核心（可獨立單元測試、無 I/O）＋薄儲存層（讀檔／原子寫檔）。
"""

import json
import os
import tempfile
from typing import Dict, List, Optional, Sequence

import pandas as pd

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

#: 紀錄的欄位白名單（data-model.md §1.2）。
#: **不得**新增任何憑證、token、通知收件識別或其他個資欄位（FR-023）。
RECORD_FIELDS = (
    "ticker",
    "bar_time",
    "alert_type",
    "direction",
    "timeframe",
    "close",
    "ladder",
    "upper_price",
    "lower_price",
    "atr",
    "param_fingerprint",
    "notified",
    "detected_at",
    "outcomes",
)

#: 主鍵：一次訊號偵測的識別（與 `sent_alerts` 同構但語意不同——
#: 後者記「已通知使用者」，本表記「訊號成立」）。
KEY_FIELDS = ("ticker", "bar_time", "alert_type")

#: 不可變欄位：一經寫入即不得改動（merge 時一律取既有值）。
_IMMUTABLE_FIELDS = tuple(
    f for f in RECORD_FIELDS if f not in ("notified", "outcomes")
)

#: `alert_type` → 方向。看多 +1／看空 −1。
#: 均線觸價（spec 014）為**向下穿越**事件，方向恆為 −1。
_DIRECTION_BY_ALERT = {
    "BULLISH_MSS": 1,
    "BEARISH_MSS": -1,
    "BULLISH_BOS": 1,
    "BEARISH_BOS": -1,
    "BREAK_UPPER_BAND": 1,
    "BREAK_LOWER_BAND": -1,
    "MA_CROSS_BELOW_MONTHLY": -1,
    "MA_CROSS_BELOW_QUARTERLY": -1,
    "MA_CROSS_BELOW_HALF_YEARLY": -1,
    "MA_CROSS_BELOW_YEARLY": -1,
}


def direction_for(alert_type: str) -> int:
    """
    由 `alert_type` 導出方向。未知型別 fail-fast——靜默回傳 0 會讓方向調整後
    報酬恆為 0，整群樣本看起來「沒有資訊量」，而真正的原因是型別漏登記。
    """
    if alert_type not in _DIRECTION_BY_ALERT:
        raise ValueError(
            f"未知的 alert_type '{alert_type}'：新增告警類型時必須同步登記其方向"
            f"（alert_outcomes._DIRECTION_BY_ALERT）"
        )
    return _DIRECTION_BY_ALERT[alert_type]


# ---------------------------------------------------------------------------
# 純函式核心
# ---------------------------------------------------------------------------

def build_fingerprint(*,
                      structure_period: int,
                      use_fvg: bool,
                      fvg_lookback: int,
                      swing_n: int,
                      volume_mult: float,
                      use_bos_volume: bool,
                      bos_volume_mult: float,
                      bos_volume_period: int) -> str:
    """
    由監控端結構參數產生**參數識別值**（data-model.md §3）。

    格式為可讀的正規字串，例如 `sp10_fvg1_fl3_sn2_vm1.5_bv0_bvm1.5_bvp20`。
    性質是**單射**：值相同 ⇔ 參數相同（不只是雜湊意義上的碰撞不太可能）。

    **不使用內建 `hash()`**：其對 `str` 有 per-process 隨機化（PYTHONHASHSEED），
    跨輪次不穩定，會讓同一組參數在不同執行產生不同識別值（違反 SC-007）；
    且該錯誤在**同一行程內的測試會誤過**。若日後改用雜湊必須走 `hashlib`。

    **維護義務**：監控端日後若改為傳入市況濾網或其他影響訊號判定的參數，
    本函式的參數清單**必須同步擴充**——否則兩批不可比的樣本會共用同一個識別值，
    而那正是本欄位存在的理由。
    """
    return (
        f"sp{int(structure_period)}"
        f"_fvg{int(bool(use_fvg))}"
        f"_fl{int(fvg_lookback)}"
        f"_sn{int(swing_n)}"
        f"_vm{float(volume_mult):g}"
        f"_bv{int(bool(use_bos_volume))}"
        f"_bvm{float(bos_volume_mult):g}"
        f"_bvp{int(bos_volume_period)}"
    )


def _opt_float(bar, key: str) -> Optional[float]:
    """
    自 bar 取數值欄位；不存在或為 NaN 時回傳 `None`。

    **不得回傳 0.0**：缺值與「值為零」必須可區分，否則分布統計會出現假零。
    """
    try:
        value = bar[key]
    except (KeyError, IndexError, TypeError):
        return None
    if value is None or pd.isna(value):
        return None
    return float(value)


def make_record(*,
                ticker: str,
                bar_time,
                alert_type: str,
                timeframe: str,
                bar,
                param_fingerprint: str,
                detected_at: Optional[str] = None) -> Dict:
    """
    組裝一筆告警紀錄（尚未寫入）。

    輸出欄位集合**恆等於** `RECORD_FIELDS`——多一欄或少一欄皆為契約違反
    （由 SC-006 斷言）。`notified` 初始為 False、`outcomes` 為空 dict
    （回填時逐視窗填入）。
    """
    if timeframe not in ("5m", "daily"):
        raise ValueError(f"未知的 timeframe '{timeframe}'（僅接受 '5m' / 'daily'）")
    stamp = detected_at or pd.Timestamp.now().isoformat(timespec="seconds")
    return {
        "ticker": str(ticker),
        "bar_time": _to_iso(bar_time),
        "alert_type": str(alert_type),
        "direction": direction_for(alert_type),
        "timeframe": timeframe,
        "close": _opt_float(bar, "close"),
        "ladder": _opt_float(bar, "ladder"),
        "upper_price": _opt_float(bar, "upper_price"),
        "lower_price": _opt_float(bar, "lower_price"),
        "atr": _opt_float(bar, "atr"),
        "param_fingerprint": str(param_fingerprint),
        "notified": False,
        "detected_at": stamp,
        "outcomes": {},
    }


def merge_record(existing: Optional[Dict], incoming: Dict) -> Dict:
    """
    upsert 的合併規則（research.md D3）。

    - `existing is None` → 直接採用 `incoming`
    - 不可變欄位一律取 `existing`
    - `notified` 為 `existing or incoming`——**單向升級，永不降級**。
      SC-005 的落點：已通知成功的告警在後續輪次被去重擋下時，該輪不會執行推播，
      若無條件以本輪結果覆寫，已成功的列會被改回 False。
    - `outcomes` 逐視窗「`existing` 非空者優先」——已回填者不重算（FR-013）

    冪等保證：`merge(merge(a, b), b) == merge(a, b)`。
    """
    if existing is None:
        return dict(incoming)

    merged = dict(existing)
    for field in _IMMUTABLE_FIELDS:
        if field in existing:
            merged[field] = existing[field]
        elif field in incoming:
            merged[field] = incoming[field]

    merged["notified"] = bool(existing.get("notified")) or bool(incoming.get("notified"))

    outcomes = dict(existing.get("outcomes") or {})
    for key, value in (incoming.get("outcomes") or {}).items():
        if outcomes.get(key) is None and value is not None:
            outcomes[key] = value
    merged["outcomes"] = outcomes
    return merged


def compute_outcomes(record: Dict,
                     daily_df: Optional[pd.DataFrame],
                     horizons: Sequence[int]) -> Dict:
    """
    計算前瞻結果（research.md D6、data-model.md §2）。

    T+N 定義為該標的日線序列中**日期嚴格大於告警日的第 N 根**。
    以表中實際存在的列計數 → 自動略過假日與停牌，不需維護交易日曆。
    「嚴格大於」使 5 分線告警（其告警日可能尚未入庫）與日線告警共用同一條規則。

    - `ret     = close[T+N] / record["close"] - 1`
    - `ret_adj = ret * direction`（FR-015：空方下跌計為正向）

    三態（FR-014）：已回填為物件；未到期／不足／資料缺漏一律 `None`。
    **不得**以 0.0 填充未到期——「還沒發生」與「報酬為零」混為一談會讓分布
    出現大量假零。

    冪等（FR-013）：已為物件的視窗原值回傳，不重算。
    **不就地修改** `record`；無任何網路存取。
    """
    existing = dict(record.get("outcomes") or {})
    baseline = record.get("close")

    pending = [n for n in horizons if existing.get(f"t{n}") is None]
    if not pending:
        return existing
    if baseline is None or baseline == 0:
        return existing
    if daily_df is None or len(daily_df) == 0 or "close" not in daily_df.columns:
        return existing

    index = pd.to_datetime(daily_df.index)
    alert_day = pd.Timestamp(record["bar_time"]).normalize()
    # tz-aware 與 naive 不可直接比較：統一去除時區後再比對日期
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    if alert_day.tzinfo is not None:
        alert_day = alert_day.tz_localize(None)

    forward = daily_df.loc[index.normalize() > alert_day]
    if forward.empty:
        return existing

    direction = int(record.get("direction", 0))
    forward_index = pd.to_datetime(forward.index)
    if getattr(forward_index, "tz", None) is not None:
        forward_index = forward_index.tz_localize(None)

    for n in pending:
        if n > len(forward):
            continue                      # 未到期／不足 → 維持 None
        close_n = float(forward["close"].iloc[n - 1])
        ret = close_n / float(baseline) - 1.0
        existing[f"t{n}"] = {
            "date": forward_index[n - 1].date().isoformat(),
            "close": close_n,
            "ret": ret,
            "ret_adj": ret * direction,
        }
    return existing


def summarize(records: Sequence[Dict],
              min_samples: int,
              horizons: Sequence[int] = (1, 3, 5),
              param_fingerprint: Optional[str] = None,
              timeframe: Optional[str] = None) -> pd.DataFrame:
    """
    分群統計，供儀表板呈現（演算法留在本模組——UI 層不得內嵌演算法邏輯）。

    分群鍵為 `alert_type` × `timeframe`；可另依 `param_fingerprint` 與
    `timeframe` 篩選。每群每視窗輸出樣本數、`ret_adj` 中位數、正報酬比例，
    以及 `sufficient`（樣本數 ≥ `min_samples`）。

    **樣本數不足的群仍會出現在輸出中**（FR-018）——靜默丟棄會讓使用者以為
    那個組合沒有告警，而事實是有告警但還不夠判讀。統計量欄位於不足時填 `None`。
    """
    rows = [r for r in records if isinstance(r, dict)]
    if param_fingerprint:
        rows = [r for r in rows if r.get("param_fingerprint") == param_fingerprint]
    if timeframe:
        rows = [r for r in rows if r.get("timeframe") == timeframe]

    groups: Dict[tuple, List[Dict]] = {}
    for r in rows:
        groups.setdefault((r.get("alert_type"), r.get("timeframe")), []).append(r)

    out = []
    for (alert_type, tf), members in sorted(
            groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        row = {
            "alert_type": alert_type,
            "timeframe": tf,
            "direction": members[0].get("direction"),
            "n_alerts": len(members),
            "n_notified": sum(1 for m in members if m.get("notified")),
        }
        for n in horizons:
            filled = [
                m["outcomes"][f"t{n}"]["ret_adj"]
                for m in members
                if (m.get("outcomes") or {}).get(f"t{n}") is not None
            ]
            count = len(filled)
            sufficient = count >= min_samples
            row[f"t{n}_n"] = count
            row[f"t{n}_sufficient"] = sufficient
            # 樣本不足時**不輸出**統計量——輸出了就會被讀成結論
            row[f"t{n}_median_adj"] = (
                float(pd.Series(filled).median()) if (count and sufficient) else None
            )
            row[f"t{n}_win_rate"] = (
                float(sum(1 for v in filled if v > 0) / count)
                if (count and sufficient) else None
            )
        out.append(row)

    columns = ["alert_type", "timeframe", "direction", "n_alerts", "n_notified"]
    for n in horizons:
        columns += [f"t{n}_n", f"t{n}_sufficient", f"t{n}_median_adj", f"t{n}_win_rate"]
    return pd.DataFrame(out, columns=columns)


# ---------------------------------------------------------------------------
# 儲存層（薄 I/O）
# ---------------------------------------------------------------------------

def _to_iso(value) -> str:
    """時間值正規化為 ISO 8601 字串（主鍵的一部分，必須穩定）。"""
    ts = pd.Timestamp(value)
    if ts == ts.normalize() and not isinstance(value, str):
        return ts.date().isoformat()
    if isinstance(value, str):
        return value
    return ts.isoformat()


def record_key(record: Dict) -> tuple:
    """主鍵元組。"""
    return tuple(str(record.get(f)) for f in KEY_FIELDS)


def make_key(ticker: str, bar_time, alert_type: str) -> tuple:
    """由原始欄位組主鍵——供呼叫端在不建構完整紀錄的情況下定位既有列。"""
    return (str(ticker), _to_iso(bar_time), str(alert_type))


def _shard_of(record: Dict) -> str:
    """分片鍵取自 `bar_time` 的年月（**非寫入時間**）——同一根 K 線的紀錄
    永遠落在同一分片，與寫入時機無關。"""
    return pd.Timestamp(record["bar_time"]).strftime("%Y-%m")


def _shard_path(log_dir: str, shard: str) -> str:
    return os.path.join(log_dir, f"{shard}.jsonl")


def _sort_key(record: Dict) -> tuple:
    return (str(record.get("bar_time")), str(record.get("ticker")),
            str(record.get("alert_type")))


def _serialize(records: Sequence[Dict]) -> str:
    lines = [
        json.dumps(r, ensure_ascii=False, sort_keys=True)
        for r in sorted(records, key=_sort_key)
    ]
    return "".join(line + "\n" for line in lines)


def load_month(log_dir: str, shard: str) -> List[Dict]:
    """讀取單月分片。檔案不存在回傳 `[]`（非例外——首次執行是正常情況）。"""
    path = _shard_path(log_dir, shard)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_all(log_dir: str) -> List[Dict]:
    """讀取全部分片（供回填與 UI 使用）。目錄不存在回傳 `[]`。"""
    if not os.path.isdir(log_dir):
        return []
    out: List[Dict] = []
    for name in sorted(os.listdir(log_dir)):
        if name.endswith(".jsonl"):
            out.extend(load_month(log_dir, name[: -len(".jsonl")]))
    return out


def _atomic_write(path: str, content: str) -> None:
    """暫存檔 + `os.replace` 原子置換——避免行程中斷留下半截檔案。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def upsert_records(log_dir: str, records: Sequence[Dict]) -> int:
    """
    依主鍵 upsert 並原子寫回，回傳**實際變更的列數**。

    - 分片：依 `bar_time` 年月分檔
    - 排序：寫回前依 `(bar_time, ticker, alert_type)` 排序，使 diff 穩定
      （避免同一內容因寫入順序不同而產生假 diff）
    - **零變更即零寫入**（FR-009）：合併後內容與磁碟上完全相同時**不觸碰檔案**，
      連 mtime 都不動。回傳 0 即代表本輪無需 commit。
    """
    if not records:
        return 0

    by_shard: Dict[str, List[Dict]] = {}
    for record in records:
        by_shard.setdefault(_shard_of(record), []).append(record)

    changed = 0
    for shard, incoming in by_shard.items():
        current = load_month(log_dir, shard)
        indexed = {record_key(r): r for r in current}

        shard_changed = 0
        for record in incoming:
            key = record_key(record)
            merged = merge_record(indexed.get(key), record)
            if indexed.get(key) != merged:
                indexed[key] = merged
                shard_changed += 1

        if shard_changed == 0:
            continue

        path = _shard_path(log_dir, shard)
        content = _serialize(list(indexed.values()))
        # 內容比對後才寫——排序與序列化可能使「有合併但無實質變化」的情況
        # 產生位元組相同的輸出，此時仍不應觸碰檔案（SC-010）
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                if fh.read() == content:
                    continue
        _atomic_write(path, content)
        changed += shard_changed

    return changed


def backfill(log_dir: str,
             horizons: Sequence[int],
             daily_loader) -> int:
    """
    為既有紀錄回填前瞻結果，回傳實際變更的列數。

    `daily_loader(ticker, timeframe) -> DataFrame | None` 由呼叫端提供——
    本模組**不直接接觸 DB 或網路**，使 SC-012（回填不發出任何對外資料請求）
    可由呼叫端注入驗證。

    可在任何時間執行、任意次數重跑（FR-012／FR-013）：行情資料可重抓，
    回填晚幾天無損正確性；已回填者不重算。
    """
    records = load_all(log_dir)
    if not records:
        return 0

    cache: Dict[str, Optional[pd.DataFrame]] = {}
    updated: List[Dict] = []
    for record in records:
        pending = [n for n in horizons
                   if (record.get("outcomes") or {}).get(f"t{n}") is None]
        if not pending:
            continue
        ticker = record.get("ticker")
        if ticker not in cache:
            try:
                cache[ticker] = daily_loader(ticker, record.get("timeframe"))
            except Exception:
                # 資料缺漏 → 該筆維持未回填，且**不得**阻塞其他筆
                cache[ticker] = None
        outcomes = compute_outcomes(record, cache[ticker], horizons)
        if outcomes != (record.get("outcomes") or {}):
            merged = dict(record)
            merged["outcomes"] = outcomes
            updated.append(merged)

    return upsert_records(log_dir, updated) if updated else 0
