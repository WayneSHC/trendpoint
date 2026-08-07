# Phase 1 Data Model: 盤中時框評估協定

**Feature**: `016-intraday-evaluation-protocol` | **Date**: 2026-08-07

所有實體皆為**記憶體內的純資料結構**（dataclass / dict），落地形式僅
canonical CSV（累積歷史）與 JSON（報告）。**不新增 SQLite 表**，
不寫入 `trendpoint.db`。

---

## Snapshot（快照）

某標的在某次取數所得的盤中 OHLCV 序列。可重現性的最小單位。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `ticker` | str | 標的代號（如 `2330.TW`） |
| `frame` | DataFrame | 索引為 `datetime`（遞增、無重複），欄為 `open/high/low/close/volume` |
| `fingerprint` | str | 正規化後內容的 SHA-256（見下方正規化規則） |
| `first_ts` / `last_ts` | Timestamp | 實得期間的兩端（**非請求期間**——見 Edge Case） |
| `bars` | int | 根數 |
| `trading_days` | int | 相異日期數 |

**正規化規則**（決定指紋，故為契約的一部分）：

1. 欄名小寫，欄序固定 `open,high,low,close,volume`。
2. 價格四欄四捨五入至小數 4 位；`volume` 轉為 int64。
3. 索引排序遞增、去除重複時間戳（保留首筆）。
4. 指紋 = 正規化後 CSV 位元組的 SHA-256。

**Validation**：

- 索引須嚴格遞增且無重複（違反 → 硬失敗，不靜默去重後續跑）。
- 價格不得為負或 NaN；`high >= low`（沿用 `validate_data_contract` 的判準）。
- `bars == 0` → 硬失敗（空快照不得進入累積）。

---

## AccumulatedHistory（累積歷史）

同一標的多份快照合併去重後的序列，附合併記錄。樣本外切分的輸入。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `ticker` | str | |
| `frame` | DataFrame | 同 Snapshot 的欄位與正規化規則 |
| `fingerprint` | str | 合併後內容的 SHA-256 |
| `chain_origin` | Timestamp | 本條累積鏈的起算時點 |
| `chain_broken` | bool | 前次累積無法取回（首次執行／逾期／上次失敗） |
| `merge_events` | list[MergeEvent] | 每次併入的記錄 |
| `gaps` | list[Gap] | 時間斷裂位置 |

**合併規則**（FR-013/014，research.md R3）：

- 兩份序列以時間戳外連接；重疊時間戳採**先到者為準**——
  既有累積值保留，新快照的值捨棄。
- 捨棄前逐欄比較：任一欄不同即計為一次衝突，累加至 `MergeEvent.conflicts`。
- 合併後索引須嚴格遞增（後置條件，違反即硬失敗）。

**State transitions**：

```text
（無前次 artifact）
   └─> chain_broken=True, chain_origin=本次快照起點
（取回前次）
   └─> chain_broken=False, chain_origin 沿用前次
       └─> merge(前次, 本次快照) -> 新的 AccumulatedHistory
```

`chain_broken` 為 True 時，`chain_origin` 重設為本次快照起點，
且該事實**必須**出現在報告的 `inputs` 區（FR-023）。

---

## MergeEvent（合併記錄）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `merged_at_fingerprint` | str | 併入之快照的指紋 |
| `bars_before` / `bars_after` | int | 合併前後根數 |
| `bars_added` | int | 淨新增根數 |
| `overlap_bars` | int | 重疊時間戳數 |
| `conflicts` | int | 重疊中數值不同的根數（FR-014） |
| `conflict_first_ts` / `conflict_last_ts` | Timestamp \| None | 衝突的時間範圍 |

---

## Gap（時間斷裂）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `start_ts` / `end_ts` | Timestamp | 斷裂的兩端 |
| `missing_trading_days` | int | 缺失的交易日數 |
| `kind` | str | `weekend_or_holiday` \| `schedule_lapse` \| `chain_restart` |

**判定**：以中位數日內根距與每日根數為基準，缺失交易日數超過門檻者
記為 `schedule_lapse`；`chain_broken` 造成者記為 `chain_restart`。
`kind` 為**列舉欄位**，下游一律以 `kind` 判斷，**不得以標籤字串比對**
（沿用 `run_b_segment.py` 情境表的既有教訓）。

---

## InclusionCriteria / UniverseDecision（納入準則與決定）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `version` | str | 準則版本識別（FR-012），門檻改變即改版 |
| `lookback_days` | int | 判定所用的 lookback 長度（位於評估窗**之前**） |
| `min_avg_daily_volume` | float | 日均量下限 |
| `max_gap_ratio` | float | 盤中缺口根數比率上限 |
| `max_bars_per_day_cv` | float | 每日根數變異係數上限 |
| `max_tick_ratio` | float | 價格檔位粒度上限 |
| `excluded_tickers` | list[str] | 顯式排除清單（槓桿/反向 ETF） |

`UniverseDecision`（逐標的）：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `ticker` | str | |
| `included` | bool | |
| `failed_criteria` | list[str] | 未達的具體準則項（FR-011；included 時為空） |
| `measured` | dict[str, float] | 各維度的實測值，供讀者自行檢驗判定 |

**不變式**：`UniverseDecision` 的計算輸入**僅限** lookback 期間的
OHLCV 與準則本身。任何回測、訊號、績效輸入即違反 FR-010，
由 `tests/test_intraday_universe.py` 的擾動測試守住（SC-005）。

---

## WindowSplit（窗口切分）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `index` | int | 第幾組（0 起） |
| `train_start` / `train_end` | Timestamp | 訓練窗邊界 |
| `test_start` / `test_end` | Timestamp | 測試窗邊界 |
| `test_bars` | int | 測試窗根數 |

**不變式**（FR-016）：

- 任兩組的測試窗**不重疊**：`test_start[i+1] > test_end[i]`。
- 任一窗**不跨越** `Gap`（含 `weekend_or_holiday` 以外的任何 kind）。
- `train_end < test_start`（訓練嚴格早於測試）。

窗數不足時**不回傳部分結果**，而是回傳空列表加上量化差距
（還差幾個交易日），由報告層轉為 FR-015 的明示訊息。

---

## PerTickerResult（逐標的評估結果）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `ticker` | str | |
| `data_health` | dict | 根數、覆蓋期間、每日根數、缺口 |
| `signal_density` | dict | BOS/MSS **分方向**計數、regime 通過數、暖機損失（FR-008） |
| `attrition` | dict | 四道濾網單道通過率 + 五道合取數 |
| `trades` | int | 完成**來回**交易數（以進場事件計，非明細列數） |
| `zero_trade_cause` | str \| None | 見下方列舉（FR-007） |
| `performance` | dict | 報酬/回撤/PF/勝率，**每項附 `validity_label`** |
| `structure_period_hardcoded` | int | 顯式標示既有硬編碼值（FR-021） |

**`zero_trade_cause` 列舉**（互斥，僅在 `trades == 0` 時非 None）：

| 值 | 判定 |
|---|---|
| `no_structure_signal` | 分方向後的 BOS/MSS 訊號數皆為 0 |
| `filters_rejected_all` | 有結構訊號，但五道合取為 0 |
| `all_candidates_blocked_by_position` | 合取 > 0，但引擎全在持倉期間跳過 |
| `entered_but_never_exited` | 有進場，資料結束前未出場 |

判定順序即上表由上而下，第一個成立者勝——保證互斥且無「原因不明」（SC-004）。

---

## ValidityLabel（效力標籤）

列舉三值，由累積狀態的純函式決定，**不可由呼叫端指定**（research.md R6）：

`in_sample_descriptive` | `out_of_sample_insufficient` | `out_of_sample_validated`

附掛於**每一個**績效類數字（FR-005）。標籤本身不構成有效性宣稱——
FR-006/SC-012 的措辭檢核對三者一律適用。

---

## PooledStatistic（跨標的合併統計）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `metric` | str | 指標名 |
| `pooled_value` | float | 合併值 |
| `min` / `max` | float | 逐標的極值 |
| `ratio` | float | `max / min`（分母為 0 時為 None） |
| `n_tickers` | int | 參與合併的標的數 |

**不變式**（FR-002/SC-003）：任何 `pooled_value` 不得單獨序列化——
離散度三欄與它同屬一個結構，缺一即為缺陷。此設計使「pooling 隱含的
同質性假設」在讀者眼前，而非藏在附錄裡（2026-08-06 實測的 7 倍差即為此而設）。

---

## EvaluationReport（評估報告，落地為 JSON）

三區，比對範圍見 research.md R8：

| 區 | 內容 | 納入 SC-001 逐欄比對 |
|---|---|---|
| `inputs` | 快照/累積指紋、參數、準則版本、標籤門檻、`chain_broken`、實得期間 | ✅ |
| `results` | 逐標的 `PerTickerResult`、`PooledStatistic`、`WindowSplit`、尺度掃描 | ✅ |
| `provenance` | 執行時間戳、run id、主機、程式版本 | ❌（排除） |

文字報表由本結構**渲染**產生，不獨立計算——兩條計算路徑必然漂移。
