---

description: "Task list for 015 — 推播訊號的事後表現追蹤（A 段：日線視窗）"
---

# Tasks: 推播訊號的事後表現追蹤（A 段：日線視窗）

**Input**: Design documents from `specs/015-alert-outcome-tracking/`

**Prerequisites**: [plan.md](plan.md)、[spec.md](spec.md)、[research.md](research.md)、
[data-model.md](data-model.md)、[contracts/alert-outcomes.md](contracts/alert-outcomes.md)、
[quickstart.md](quickstart.md)

**Tests**: **必要，非選配**。憲章原則 III 要求每條驗收標準對應至少一個 pytest 測試，
且合併前 `pytest -q` 全綠為硬性關卡。採**先紅後綠**（spec 010～014 既有實踐）。

**Organization**: 依 user story 分組。US2（既有告警不變）雖與 US1 同為 P1，
但其基準凍結（T002）**必須最先執行**——「實作前」的既有告警產出一旦改碼即無從比對。
US3（持久化）的儲存層被提前至 Foundational，因為 US1／US2 的驗收皆需讀取已寫入的紀錄。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔案、無未完成依賴）
- **[Story]**: US1 / US2 / US3 / US4；Setup、Foundational、Polish 階段無標籤
- **[A] / [B]**: 驗收環境切分（plan.md §驗收環境切分）
  - **[A]** 離線可完成且可驗收——合成資料即足，CI 可跑
  - **[B]** 需真實市場資料與時間累積，**必須在本機執行**。本案為 T030／T031

## Path Conventions

單一 Python 專案，扁平結構，repo root 即工作目錄。路徑含中文與空格，
**Bash 中一律雙引號**（CLAUDE.md 鐵律 1）。

## ⚠️ 本案五個最容易誤實作的點（實作時逐條核對）

依 research.md 的風險排序。每點附守門任務——該任務失敗即代表踩到。

1. **重構了七個告警分支**（D4）。`monitor_signals.py:217-264` 的重複是**刻意留下的**；
   合併會使 SC-001 的驗證從「讀 diff」變成「證明重構等價」。守門：T016。
2. **把記錄點放進 `mark_alert_as_sent`**（D4）。該函式只在推播成功時被呼叫，
   語意是「已通知使用者」（`alerts.py:137`）。放進去 ⇒ 推播失敗的訊號永不被記錄，
   直接違反 FR-001。守門：T016（SC-003）。
3. **用內建 `hash()` 做參數識別值**（D5）。`str` 的 hash 有 per-process 隨機化，
   跨輪次不穩定——**且同一行程內的測試會誤過**，必須跨行程驗證。守門：T024。
4. **回填時把 `null` 寫成 `0.0`**（D6）。「還沒發生」與「報酬為零」混為一談，
   會讓分布出現大量假零。守門：T010（SC-014）。
5. **動了 `db_security.py:19` 的 `TABLE_NAME_PATTERN`**（契約 §6）。
   本案**不新增 SQLite 表**，出現這個念頭即代表偏離 D1 的設計。守門：T025。

---

## Phase 1: Setup（合成資料與既有告警基準）

**Purpose**: 備妥可精確控制觸發條件的合成資料，並在**未改碼**狀態下凍結既有告警產出。

- [ ] T001 [A] 在 `tests/outcome_fixtures.py` 建立本案合成資料產生器（沿用 repo 既有的 `tests/acceptance_fixtures.py`／`tests/ma_fixtures.py` 命名慣例，不另開子目錄）（固定 seed），提供：(a) 含假日缺口的日線序列（供驗證 T+N 取交易日而非日曆日）、(b) 末端不足 5 根的短日線序列（供驗證「未到期」三態）、(c) 可指定觸發特定 `alert_type` 的 5 分線序列、(d) 日線時框的期貨序列（供驗證 timeframe 分群）
- [ ] T002 [A] 以 T001 的 fixture 在**未改碼**狀態下執行 `monitor_signals.check_new_signals`，將既有七種告警的產出集合（`alert_type`、`bar_time`、完整訊息字串）存為入版控的期望檔 `tests/fixtures_015_baseline_alerts.json`，檔頭註記 commit SHA — 此為 SC-001 的唯一比對來源

**Checkpoint**: 合成資料可精確控制觸發條件、既有告警行為已凍結且不可再變

---

## Phase 2: Foundational（`alert_outcomes.py` 純元件、儲存層與參數層）—— 阻塞所有 user story

**Purpose**: 產出可獨立單元測試的純函式核心與薄儲存層。

**⚠️ CRITICAL**: 本階段未完成前，任何 user story 都無法開始。
儲存層置於此階段（而非 US3）是因為 US1／US2 的驗收皆需讀取已寫入的紀錄。

- [ ] T003 [A] 在 `config/config.py` 新增 `OutcomeTrackingConfig`（`enabled: bool = False`、`log_dir: str`、`horizons: list[int]`、`min_samples: int`）並掛入既有 `alerts` 模型（spec 014 建立），驗證規則：`horizons` 為遞增正整數且非空、`min_samples >= 1`、`log_dir` 非空且**不得以 `data/` 開頭**（該目錄整體 gitignored，D2）
- [ ] T004 [A] 在 `config/config.yaml:113` 既有 `alerts` 區塊下新增 `outcome_tracking` 子區塊，`enabled: false`、`log_dir: "alert_log"`、`horizons: [1, 3, 5]`、`min_samples: 20`；**不動** `ma_alerts_enabled`
- [ ] T005 [A] 建立 `alert_outcomes.py`（MPL-2.0 檔頭，比照既有核心 .py），實作 `build_fingerprint(**params) -> str`：由八個監控端結構參數產生正規字串（data-model.md §3），浮點數固定小數位格式化；**禁用內建 `hash()`**
- [ ] T006 [P] [A] 在 `alert_outcomes.py` 實作 `make_record(...) -> dict`：欄位集合**恆等於** data-model.md §1.2 白名單，`direction` 由 `alert_type` 導出（§1.3 對照表），bar 中不存在的指標欄位填 `None` **不得填 0**，`notified=False`、`outcomes` 三個 `None`
- [ ] T007 [P] [A] 在 `alert_outcomes.py` 實作 `merge_record(existing, incoming) -> dict`：不可變欄位取 `existing`；`notified` 為 `existing or incoming`（**單向升級**）；`outcomes` 逐視窗「`existing` 非 `None` 者優先」；保證 `merge(merge(a,b),b) == merge(a,b)`
- [ ] T008 [P] [A] 在 `alert_outcomes.py` 實作 `compute_outcomes(record, daily_df, horizons) -> dict`：T+N = 日線索引中日期**嚴格大於** `bar_time` 日期的第 N 根；`ret = close_N / record["close"] - 1`；`ret_adj = ret * direction`；三態（已回填／未到期／缺漏）；已為物件者不重算；**不得**就地修改 `record`、**不得**任何網路存取
- [ ] T009 [A] 在 `alert_outcomes.py` 實作儲存層：`load_month(log_dir, ym)`（檔不存在回 `[]` 而非例外）、`load_all(log_dir)`、`upsert_records(log_dir, records) -> int`（依 `bar_time` 年月分片、寫回前依 `(bar_time, ticker, alert_type)` 排序、暫存檔 + `os.replace` 原子置換、**零變更即零寫入**並回傳實際變更列數）
- [ ] T010 [A] 新增 `tests/test_alert_outcomes.py`：純函式與儲存層測試 — SC-006（欄位白名單恆等）、SC-011（交易日對齊、基準價為紀錄的 `close`）、SC-013（回填冪等）、SC-014（三態可區分且序列化後 `None` ≠ `0.0`）、SC-015（方向調整對稱，以鏡像資料驗證）、`merge_record` 冪等

**Checkpoint**: 純函式與儲存層可獨立驗收，尚未接觸 `monitor_signals.py`

---

## Phase 3: User Story 2 - 既有告警行為完全不變 (P1，**先於 US1 完成接線守門**)

**Goal**: 在監控端接線的同時，證明既有七種告警逐筆逐則逐欄未變。

**Independent Test**: 對 T001 的固定資料，開關關閉與開啟兩種設定下，
既有告警的產出集合與訊息內容皆與 T002 的凍結基準相同。

- [ ] T011 [US2] [A] 在 `monitor_signals.py` 讀取 `cfg.alerts.outcome_tracking` 並在 `enabled=False` 時**完全不進入**本案任何路徑（不建立目錄、不讀檔、不寫檔、不回填）
- [ ] T012 [US2] [A] 在 `monitor_signals.py:217-264` 的六個結構告警分支與 `check_ma_touch_alerts`（`:279`）中，於 `alert_type` 確定後、`is_alert_already_sent(...)` 判定**之前**各插入一次紀錄收集（收集至函式區域清單，不逐次寫檔）；**不得重構既有分支結構**、**不得改動** `:167` 的 5 分線取數、`:194-199` 的 `build_indicator_frame` 呼叫、`:207-212` 的已收盤棒選取、七種告警的判定條件與訊息字串
- [ ] T013 [US2] [A] 在 `monitor_signals.py` 各分支 `alert_mgr.send_alert(...)` 回傳 `True` 之後（與既有 `mark_alert_as_sent` 同處）標記該紀錄 `notified=True`
- [ ] T014 [US2] [A] 在 `monitor_signals.py` 函式尾端一次性呼叫 `upsert_records` 寫回（單次檔案 I/O，非每分支各寫一次）
- [ ] T015 [US2] [A] 在 `monitor_signals.py` 為紀錄層與回填層加上例外隔離：**任何**例外皆捕捉且不向上傳播，捕捉後印一行提示（比照 `init_sent_alerts_db` 的既有風格，`:53-55`）
- [ ] T016 [US2] [A] 新增 `tests/test_alert_outcomes_monitor.py`：SC-001（開關關閉時比對 T002 基準逐則相同，且斷言 `alert_log/` **未被建立**）、SC-002（monkeypatch 使紀錄層與回填層拋例外，斷言推播仍送出且告警產出不變）、SC-003（令 `send_alert` 回傳 `False`，斷言紀錄存在且 `notified=false`）、SC-005（先成功推播再令去重擋下重跑，斷言 `notified` 仍為 `true`）

**Checkpoint**: 既有行為已被測試鎖定，後續任何階段的改動都會被 SC-001 抓到

---

## Phase 4: User Story 3 - 樣本在排程環境下不會無聲消失 (P1)

**Goal**: 紀錄的持久性不依賴 `actions/cache`，且無事發生的輪次不產生雜訊。

**Independent Test**: 清空並重建 `trendpoint.db` 後紀錄仍完整；
無新告警且無可回填視窗的輪次，檔案逐位元不變。

- [ ] T017 [US3] [A] 在 `tests/test_alert_outcomes_monitor.py` 補：SC-004（同一根 K 線的同一告警重跑 N 次，斷言該主鍵筆數恆為 1）、SC-009（刪除並重建 `trendpoint.db` 後 `load_all` 結果筆數與內容不變）、SC-010（既無新告警亦無可回填視窗的輪次，斷言檔案 mtime 與位元組皆未變）
- [ ] T018 [US3] [A] 修改 `.github/workflows/alert_scheduler.yml`：宣告 `permissions: contents: write`（現行未宣告，A-9）；於推播步驟之後新增 commit 步驟，**僅在 `alert_log/` 確有變更時**執行、commit 訊息含 `[skip ci]`（否則每次告警都觸發 `tests.yml`，D9）、push 前 `pull --rebase` 並重試、**失敗不阻斷該輪推播**（留待下輪一併提交，upsert 冪等）；同時確認 `alert_log/` 未被 `.gitignore` 涵蓋

**Checkpoint**: 紀錄可跨快取逐出存活，且不產生雜訊 commit

---

## Phase 5: User Story 1 - 回答「推播的訊號事後表現如何」 (P1)

**Goal**: 回填前瞻結果並以分群分布呈現，這是本案的全部價值。

**Independent Test**: 以固定資料構造若干告警與其後續日線，
可依 `alert_type` 分群得出前瞻報酬的統計摘要。

- [ ] T019 [US1] [A] 在 `monitor_signals.py` 於輪詢**開始時**順帶執行回填（讀既有 `stock_*_daily` / `fut_*_daily`，走既有 `safe_load_db_data`）；新增 `--backfill-only` 旗標：只回填、不取數、不推播（D7）
- [ ] T020 [US1] [A] 在 `alert_outcomes.py` 實作 `summarize(records, min_samples) -> DataFrame`：分群鍵為 `alert_type` × `timeframe`，可再依 `param_fingerprint` 篩選；每群每視窗輸出樣本數、`ret_adj` 中位數、正報酬比例與 `sufficient: bool`；**樣本不足的群仍須出現在輸出中**，不得靜默丟棄
- [ ] T021 [US1] [A] 在 `app.py:621` 將四分頁擴為五，新增「訊號事後表現」唯讀分頁：只呼叫 `summarize()` 呈現（**不內嵌演算法邏輯**，CLAUDE.md UI 規則）；**必須**顯示「非策略績效」標示（不含手續費／稅／滑價、無出場規則、未經樣本外驗證）；**不得**呈現任何回測 KPI 欄位；提供 `timeframe` 與 `param_fingerprint` 篩選；紀錄為空時顯示引導訊息不報錯
- [ ] T022 [US1] [A] 在 `tests/test_alert_outcomes.py` 補：SC-012（monkeypatch 網路層使任何呼叫即失敗，`--backfill-only` 仍應成功）、SC-016（分頁含標示字串且不含任何回測 KPI 欄位名）、SC-017（樣本數 < `min_samples` 的群標示為不足且不顯示統計量，但仍出現在輸出中）

**Checkpoint**: 本案的核心價值可端到端驗收

---

## Phase 6: User Story 4 - 樣本可信：知道每一列是什麼設定產生的 (P2)

**Goal**: 參數改變後新舊樣本可分群，不被混算。

**Independent Test**: 兩組不同監控參數各產生紀錄，參數識別值不同且可分群。

- [ ] T023 [US4] [A] 在 `monitor_signals.py` 將實際傳入 `build_indicator_frame` 的八個結構參數（`:194-199`：`structure_period`、`use_fvg`、`fvg_lookback`、`swing_n`、`volume_mult`、`use_bos_volume`、`bos_volume_mult`、`bos_volume_period`）穿線至 `build_fingerprint()`，**不得**另行硬編碼一份
- [ ] T024 [US4] [A] 在 `tests/test_alert_outcomes.py` 補：SC-007（兩組參數 → 相異；同組參數**跨行程**呼叫 → 相同，須以 subprocess 或明確設定 `PYTHONHASHSEED` 驗證，同行程內測試會誤過）、SC-008（混入 `5m` 與 `daily` 紀錄，斷言分群互不混入且 `summarize` 可篩選）

**Checkpoint**: 樣本具備可追溯性，跨參數變更仍可分群

---

## Phase 7: Polish & Cross-Cutting

- [ ] T025 [P] [A] 在 `tests/test_alert_outcomes.py` 補 SC-019：靜態零引用檢查 — 掃描 `ladder_system.py`、`backtester.py`、`portfolio_backtester.py`、`walk_forward.py`、`optimizer.py`、`monte_carlo.py`、`performance.py`、`trading_costs.py`、`risk_gates.py` 及回測入口 `run_backtest.py`、`run_portfolio_backtest.py`、`run_walk_forward.py`、`run_optimization.py`、`run_ablation.py`、`run_b_segment.py`，斷言**零**引用 `alert_outcomes` 與 `alert_log`；並斷言 `alert_outcomes.py` 未反向 import `monitor_signals`／`backtester`／`ladder_system`
- [ ] T026 [P] [A] 在 `tests/test_alert_outcomes.py` 補 SC-020：斷言本案無字串拼接 SQL（回填僅經既有 `safe_load_db_data`）、斷言欄位白名單不含任何憑證／token／收件識別類鍵名（FR-023）
- [ ] T027 [P] [A] 在 `tests/test_alert_outcomes.py` 補 SC-018：移除組態欄位或填入非法值（`horizons` 非遞增／空、`min_samples < 1`、`log_dir` 以 `data/` 開頭）時載入即失敗；並斷言程式碼中無對應硬編碼常數
- [ ] T028 [A] 執行 `pytest -q` 確認全綠（SC-021），既有測試無退化
- [ ] T029 [A] 更新 `CLAUDE.md` 專案地圖：於通知段補記 `alert_outcomes.py` 與 `alert_log/` 的定位（觀察層、不進訊號或回測路徑、JSONL 為單一真實來源、總開關預設關閉），並註明 `TABLE_NAME_PATTERN` 不得為本案放寬
- [ ] T030 [B] **[MANUAL]** SC-022：開啟總開關實跑至少一個完整交易週，記錄實際告警頻率（每週筆數、依 `alert_type` 與 `timeframe` 的分布），**無論結果有利與否皆如實回填至 `spec.md`**。若頻率低到樣本累積不具意義（A-6），據此決定是否收手——**而非默默保留**。步驟見 [quickstart.md](quickstart.md) §3
- [ ] T031 [B] **[MANUAL]** SC-023：在任一分群樣本數達到 `min_samples` 之前，**不得**對前瞻報酬分布做結論性判讀；首次判讀時同時記錄樣本期間、標的清單與 `param_fingerprint`

---

## Dependencies

```
Phase 1 (T001, T002)
    │  T002 必須在任何改碼之前完成——基準一旦被污染即無法重建
    ▼
Phase 2 Foundational (T003 → T004, T005 → T006/T007/T008 [P], T009, T010)
    │  阻塞所有 user story
    ▼
Phase 3 US2 (T011 → T012 → T013 → T014 → T015 → T016)
    │  接線與守門；T016 通過後既有行為被鎖定
    ▼
Phase 4 US3 (T017, T018)
    │  持久性與雜訊控制
    ▼
Phase 5 US1 (T019 → T020 → T021 → T022)
    │  核心價值
    ▼
Phase 6 US4 (T023 → T024)
    │
    ▼
Phase 7 Polish (T025/T026/T027 [P] → T028 → T029 → T030 [B] → T031 [B])
```

**關鍵前後序**：

- **T002 先於一切改碼**。基準是「實作前」的產出，改了碼就再也做不出來。
- **T005 先於 T006**：`make_record` 需要 `build_fingerprint` 的輸出。
- **T009 先於 T014／T016／T017**：接線與回歸測試皆需儲存層可用。
- **T016 先於 Phase 4 起的所有任務**：既有行為未鎖定前，後續改動的影響無法界定。
- **T023 先於 T024**：參數穿線完成才能驗證跨行程穩定性。
- **T028 先於 T029**：文件更新應反映已驗證的實作。
- **T030／T031 在合併之後**：需要真實排程累積，不阻塞 A 段合併。

## Parallel Execution Examples

**Phase 2 內**（不同函式、同檔但互不依賴，建議依序提交以免衝突）：

```
T006 [P] make_record
T007 [P] merge_record      ← 三者互不依賴，可平行設計與撰寫測試
T008 [P] compute_outcomes
```

**Phase 7 內**（不同測試關注點，皆追加於同一測試檔）：

```
T025 [P] 靜態零引用檢查
T026 [P] 安全檢查            ← 三者互不依賴
T027 [P] 組態 schema 驗證
```

**跨 story 無平行機會**：US2 → US3 → US1 → US4 為嚴格序列，
因為每一階段都建立在前一階段鎖定的行為之上。

## Implementation Strategy

### MVP 範圍

**MVP = Phase 1 + Phase 2 + Phase 3 + Phase 4**（T001–T018）。

此時系統已能**開始累積樣本**且不影響既有行為——而累積必須先開始，
因為樣本是時間的函數。Phase 5 的呈現層可以晚幾天做，
但每晚一天開始記錄就永久少一天樣本。

**這與一般「先做核心價值」的直覺相反**，理由是本案的核心價值（US1 的分布判讀）
在樣本湊齊前**沒有東西可看**。先讓資料開始流入，才是對的順序。

### 增量交付

1. **T001–T010**：純元件完成，`pytest -q` 全綠，零行為變更（可安全合併）
2. **T011–T016**：接線完成，開關預設關閉 ⇒ 生產行為仍為零變更（可安全合併）
3. **T017–T018**：持久化與排程就緒 ⇒ **可開啟開關開始累積**
4. **T019–T022**：呈現層 ⇒ 樣本可判讀
5. **T023–T029**：可追溯性與收尾
6. **T030–T031**：真實資料驗收，決定本案去留

### 停損點

**T030 是本案的停損檢查**。若實跑一週的告警頻率低到樣本永遠湊不齊（A-6），
正確處置是**據此收手並如實記錄**，而非保留一個看似完整、實則永遠無法判讀的功能。
spec 的 SC-022 已明訂「無論結果有利與否皆須如實記錄」。
