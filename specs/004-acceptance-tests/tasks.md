# Tasks: 驗收標準自動化測試套件（Acceptance Criteria as Tests）

**Input**: Design documents from `/specs/004-acceptance-tests/`

**Prerequisites**: plan.md、spec.md、research.md、data-model.md、contracts/、quickstart.md（全數已存在）

**Tests**: 本功能的交付物**就是**測試套件，故各 User Story 的「測試任務」即實作任務本身。
Foundational 的重構任務另有獨立迴歸閘門（非 TDD 之測試先行，而是「先取基準、後重構、再比對」）。

**Organization**: 依 User Story 分組；US1 為 MVP。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔案、無未完成依賴）
- **[Story]**: 對應 spec.md 的 US1/US2/US3

## Path Conventions

單體專案、repo 根目錄扁平佈局（模組在根、測試在 `tests/`），依 plan.md 的 Source Code 結構。

---

## Phase 1: Setup（共用基礎）

**Purpose**: 測試基礎設施——pytest 設定與共用合成資料

- [ ] T001 新增 `pytest.ini`：註冊 `performance` marker（描述：延遲預算測試，可用 `-m "not performance"` 排除）；不加任何 `addopts` 過濾（CI 預設全跑，spec FR-003）
- [ ] T002 [P] 建立 `tests/acceptance_fixtures.py`：依 data-model.md §3 實作 `make_klines(n, freq)`、`make_klines_with_gap(n, gap_at, gap_len)`、`make_klines_with_outlier(n, at, kind)`——固定 seed、OHLC 合法性不變式、支援 `freq="5min"/"1D"`、生成 < 1s、檔頭加 MPL-2.0 標頭

---

## Phase 2: Foundational（阻塞性前置——`build_indicator_frame()` 重構）

**Purpose**: US1/US2 的正典受測入口。**逐行搬移、不改語意**，以前後回測零差異證明無害（contracts/indicator-frame.md 遷移契約）。

**⚠️ CRITICAL**: US1 與 US2 在本階段完成前不得動工（US3 不依賴本階段，可平行）。

- [ ] T003 取基準：於重構前的分支狀態跑 `python run_backtest.py`，記錄各標的交易筆數、每筆成交價、組合總報酬至 `specs/004-acceptance-tests/baseline-pre-refactor.md`；同時對一份固定合成 df 記錄 `monitor_signals.check_new_signals` 相關判定輸出。**完成本任務後立即 commit + push**（防 session 中斷遺失基準）
- [ ] T004 在 `ladder_system.py` 新增 `build_indicator_frame()`（簽名見 contracts/indicator-frame.md）：逐行搬移 `backtester.py:118-163` 的組裝邏輯（含內聯三關價、yesterday_high/low groupby-shift、daily_open、regime、chandelier），欄名以 `mss_signal`/`bos_signal` 為正典；`include_regime=False` 時省略 `regime_ok`；不讀 config、不含硬編碼參數
- [ ] T005 改 `backtester.py`：`run_backtest` 的內聯區塊（118-163）改為呼叫 `build_indicator_frame()`，參數自既有 config 物件傳入；不動迴圈本體與 timebase（`sig_row = iloc[i-1]`、`struct_row = iloc[i-2]` 維持原樣）
- [ ] T006 改 `monitor_signals.py`：`check_new_signals` 的內聯區塊（117-141）改為呼叫 `build_indicator_frame(..., include_regime=False)`；欄名 `mss`/`bos` 對齊為 `mss_signal`/`bos_signal`（同步改後續引用處）；`select_closed_bar_indices` 邏輯不動
- [ ] T007 迴歸閘門：重跑 `python run_backtest.py` 與固定 df 的 monitor 判定，與 T003 基準逐位比對（交易筆數、每筆成交價、總報酬、訊號集合全同）；`pytest -q` 全綠；對照表附入 `baseline-pre-refactor.md` 並於 PR 說明引用。**通過後 commit + push**

**Checkpoint**: 正典計算入口就緒且證明無害——US1/US2 可動工

---

## Phase 3: User Story 1 - 回測 ↔ 即時一致性（Parity）（Priority: P1）🎯 MVP

**Goal**: 一個測試檔證明前綴一致性：`build_indicator_frame(df[:i])` 尾列 == `build_indicator_frame(df)` 第 i−1 列，零容差（spec 001 SC-003）。

**Independent Test**: `pytest -q tests/test_acceptance_parity.py` 離線獨立全綠。

### Implementation for User Story 1

- [ ] T008 [US1] 建立 `tests/test_acceptance_parity.py`：用 `acceptance_fixtures.make_klines`（5 分 K 與日 K 各一組、長度 ≥ 500）；截斷點取樣 ~40 個（rolling 邊界 period∈{10,14,20,22} 前後密集、中段均勻、尾部密集）；對欄位 `atr, ladder, mid_price, upper_price, lower_price, mss_signal, bos_signal, chandelier_long` 逐點 `assert_series_equal` 零容差；docstring 記載 R1 已知限制（Wilder 起點播種——不同起點歷史本就不等，parity 定義為同起點增長端點）
- [ ] T009 [US1] SC-002 有效性驗證（一次性人工步驟）：暫時移除 `detect_market_structure` 一處 `.shift(1)` → `pytest tests/test_acceptance_parity.py` 必須變紅；還原後回綠；截圖/輸出記錄於 PR 說明（quickstart.md §2）
- [ ] T010 [US1] 改 `.github/workflows/tests.yml`：no-numba 重跑清單（現為 test_ladder_system.py、test_lookahead_bias.py）加入 `tests/test_acceptance_parity.py`（spec Edge Case：兩模式一致，research.md R6）

**Checkpoint**: MVP 完成——SC-003 有自動化防線，且經注入驗證證明測試抓得到壞

---

## Phase 4: User Story 2 - 延遲預算（Latency Budget）（Priority: P2）

**Goal**: 新 K 線到達後的全量重算 + 訊號判定路徑，中位數 < 100ms（spec 001 SC-004）。

**Independent Test**: `pytest -q tests/test_acceptance_latency.py -m performance` 離線獨立全綠。

### Implementation for User Story 2

- [ ] T011 [US2] 建立 `tests/test_acceptance_latency.py`：`@pytest.mark.performance`；兩個情境——(a) 10,000 根 5 分 K 壓力情境、(b) ~270 根監控實際視窗（5 天 × 5 分 K）；每情境：預先備妥 df → 追加一根新 bar → 量測 `build_indicator_frame()` + 取最後已收盤列判定訊號的整段耗時，`time.perf_counter` 跑 21 次取中位數，斷言 < 0.1s；輸出實測中位數至測試訊息（research.md R3）

**Checkpoint**: SC-004 有自動化防線，效能劣化會讓 CI 變紅

---

## Phase 5: User Story 3 - 資料容錯（Gap & Outlier）（Priority: P2）

**Goal**: 缺漏 ffill 容錯與離群值拒絕皆有實作與測試（spec 001 SC-005）。先補實作（現行契約擋不住價格 0 與千倍跳動，research.md R4），再落測試。

**Independent Test**: `pytest -q tests/test_acceptance_data_quality.py` 離線獨立全綠。

**Note**: 本 story 不依賴 Phase 2，可與 US1/US2 平行開發。

### Implementation for User Story 3

- [ ] T012 [P] [US3] 組態：`config/config.yaml` 新增 `data_quality: {max_close_jump_ratio: 3.0}` 區塊；`config/config.py` 新增 `class DataQualityConfig(BaseModel)`（`max_close_jump_ratio: float = Field(3.0, gt=0.0)`）並掛入 `SystemConfig`（optional、給預設值以保向後相容）；`tests/test_config.py` 若有 schema 覆蓋測試則同步
- [ ] T013 [US3] 強化 `data_ingestion.py` 的 `validate_data_contract`：增加 keyword-only 參數 `quality`（預設從 `load_config()` 取 `data_quality`）；價格欄位 `< 0` 改 `<= 0` 拒絕（volume 仍允許 0）；新增 `abs(close.pct_change().iloc[1:]) > quality.max_close_jump_ratio` 拒絕；違反時先 `logger.warning`（含違規類型、首個違規時間戳、違規值）再 raise `ValueError`（contracts/data-contract.md 規則 4/5）；不得出現字面量閾值
- [ ] T014 [US3] `data_ingestion.py` 的 `clean_kline_dataframe`：兩處 `print` 警告（缺漏填補、頭部捨棄）遷移至模組層 `logging.getLogger(__name__).warning`，訊息含根數（research.md R5）；行為（ffill-only → dropna 頭部）不變
- [ ] T015 [US3] 建立 `tests/test_acceptance_data_quality.py`：場景 1——`make_klines_with_gap` 經 `clean_kline_dataframe` 後索引嚴格遞增、無 NaN、填補值 == 缺漏前值（**斷言非 bfill**，憲法 VI 回歸防線）、後續 `calculate_atr` 除 warm-up 外無 NaN、`caplog` 有含根數的 WARNING；場景 2——`make_klines_with_outlier(kind="zero")` 與 `kind="spike"` 餵 `validate_data_contract` 皆 raise `ValueError` 且 `caplog` 有含時間戳的 WARNING；另加一組正常資料通過驗證的對照
- [ ] T016 [US3] SC-002 式有效性驗證（一次性人工步驟）：臨時把 `max_close_jump_ratio` 設為 `float("inf")` → spike 測試必須變紅；還原後回綠；記錄於 PR 說明（quickstart.md §2）

**Checkpoint**: SC-005 有實作與自動化防線

---

## Phase 6: Polish & Cross-Cutting

**Purpose**: 收尾——全套驗證、文件同步

- [ ] T017 依 quickstart.md 全套跑一遍：三檔測試全綠、`pytest -q` 全綠、本機 no-numba 重跑（§4）一致、`-m "not performance"` 排除正常
- [ ] T018 [P] 文件同步：`specs/001-ladder-core/spec.md` 的 SC-003/004/005 從「（自動化 → spec 004）」改為指向實際測試檔；`specs/004-acceptance-tests/spec.md` Status 由 Draft 改為 Implemented；修訂摘要表 004 列同步
- [ ] T019 開 PR（base: main）：說明含 T007 前後回測對照表、T009/T016 注入驗證記錄、憲法檢核聲明；CI 全綠後依現行流程合併

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1（Setup）**: 無依賴，立即可做；T001 與 T002 可平行
- **Phase 2（Foundational）**: T003 → T004 → T005/T006 → T007 嚴格串行（基準必須先於重構；T005 與 T006 改不同檔可平行）
- **Phase 3（US1）**: 依賴 Phase 1 + Phase 2；T008 → T009 → T010 串行
- **Phase 4（US2）**: 依賴 Phase 1 + Phase 2；與 US1 平行可行（不同檔）
- **Phase 5（US3）**: 依賴 Phase 1（僅 fixtures）；**不依賴 Phase 2**，可與 Phase 2–4 平行；T012 → T013 → T014 → T015/T016（T012 先行因 T013 引用 schema）
- **Phase 6（Polish）**: 依賴全部完成

### Parallel Opportunities

- T001 ∥ T002（Setup 內）
- T005 ∥ T006（重構的兩個呼叫端，不同檔）
- Phase 5 全線 ∥ Phase 2–4（US3 不碰引擎組裝碼）
- T008 ∥ T011（兩個測試檔，皆只依賴 Phase 2 完成）
- T018 ∥ T017（文件與驗證不同檔）

### Parallel Example: Foundational 完成後

```bash
# 同時開工（不同檔案、零依賴衝突）：
Task A: T008 tests/test_acceptance_parity.py
Task B: T011 tests/test_acceptance_latency.py
Task C: T012→T013→T014 data-quality 實作鏈
```

---

## Implementation Strategy

### MVP First（US1 only）

1. Phase 1 → Phase 2（含迴歸閘門 T007——這是全計畫風險最高點，先過）
2. Phase 3（US1 parity）→ 注入驗證 T009 → **STOP and VALIDATE**
3. 此時 SC-003 已有防線，可先開 PR 交付 MVP，US2/US3 走後續 PR 亦可

### Incremental Delivery

單人開發的建議順序：Setup → Foundational（commit+push 兩次：基準、重構）→
US1 → US2 → US3 → Polish，每個 checkpoint 後 commit + push
（scratchpad/session 不耐久教訓）。全部一個 PR 或 MVP 先行皆可，
以 T007 對照表為 PR 必附件。

---

## Notes

- 總計 19 個任務；風險集中於 T004–T007（動引擎組裝碼），其餘為純新增
- T003 的基準必須在任何重構動工前落檔並 push——順序不可換
- T009/T016 是一次性人工驗證（證明測試會抓壞），不留在程式碼裡
- 新增 .py 檔（fixtures 與三個測試檔）一律加 MPL-2.0 檔頭
