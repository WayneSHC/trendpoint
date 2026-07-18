---

description: "Task list for 011 — 期貨連續序列未調整參考價（sizing 與期交稅價格基準修正）"
---

# Tasks: 期貨連續序列未調整參考價（sizing 與期交稅價格基準修正）

**Input**: Design documents from `specs/011-unadjusted-sizing-price/`

**Prerequisites**: [plan.md](plan.md)、[spec.md](spec.md)、[research.md](research.md)、
[data-model.md](data-model.md)、[contracts/price-basis-contract.md](contracts/price-basis-contract.md)

**Tests**: **必要，非選配**。憲章原則 III 要求每條驗收標準對應至少一個 pytest
測試；原則 I 要求新增訊號/價格基準必須在 `tests/test_lookahead_bias.py` 加防禦
測試。故本案所有測試任務為硬性，且採**先紅後綠**（spec 010 既有實踐）。

**Organization**: 依 user story 分組。US1（sizing）為 MVP——它單獨完成即可
解除爆倉護欄誤觸、讓全歷史回測跑通，US2/US3 是成本精確性與回歸保證。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔案、無未完成依賴）
- **[Story]**: US1 / US2 / US3；Setup、Foundational、Polish 階段無標籤

## Path Conventions

單一 Python 專案，扁平結構，repo root 即工作目錄。路徑含中文與空格，
**Bash 中一律雙引號**（CLAUDE.md 鐵律 1）。

---

## Phase 1: Setup（資料與基準備妥）

**Purpose**: 備妥 raw 資料與「修正前」對照基準。基準一旦錯過就無法重建
（改碼後舊行為即消失），故必須最先做。

- [ ] T001 複製全歷史 raw 資料庫到工作目錄：`cp ".claude/worktrees/awesome-liskov-efbdf6/trendpoint.db" "./trendpoint.db"`，並以 `sqlite3` 確認 `fut_TXF_raw_daily` 為 34,722 列、`fut_TXF_daily` 為 6,947 根且**僅有** `datetime/open/high/low/close/volume` 六欄（此即 FR-008 硬失敗的現況測資）
- [ ] T002 擷取「修正前」基準：以現況程式碼執行 `python run_backtest.py`（TXF），將 trades/equity CSV 另存為 `specs/011-unadjusted-sizing-price/baseline/` 下的對照檔，並在檔頭記錄 commit SHA；此為 SC-003 的比對來源
- [ ] T003 [P] 記錄現況現貨回測輸出（任一 equity 標的）作為 008b 位元不變回歸的對照，存於同一 baseline 目錄

**Checkpoint**: raw 在庫、修正前行為已凍結成檔，可以開始改碼

---

## Phase 2: Foundational（未調整欄位的產生與把關）—— 阻塞所有 user story

**Purpose**: 讓連續層產出 `unadj_*` 四欄、讓所有期貨來源都有此欄位、
讓資料契約守住正性。US1/US2/US3 全部依賴這一層。

**⚠️ CRITICAL**: 本階段未完成前，任何 user story 都無法開始

- [ ] T004 [P] 在 `tests/test_rollover.py` 新增未調整欄位透傳測試（**先紅**）：以手算小資料框斷言 `build_continuous` 輸出含 `unadj_open/high/low/close`，且其值等於對應日近月契約的原始 OHLC（不受平移影響）— 對應 SC-002
- [ ] T005 [P] 在 `tests/test_rollover.py` 新增**截斷不變性**測試（先紅）：對同一 raw 取全量與截斷至第 k 根兩種輸入建構連續序列，斷言前 k 根的 `unadj_*` 完全相同；同時斷言調整後 `close` **允許**不同（對照組，證明測試有鑑別力）。寫法可沿用 `tests/test_acceptance_parity.py` 既有的前綴一致性範式（截斷點 i + `check_exact=True` 零容差）— 對應 SC-008
- [ ] T006 在 `data_sources/rollover.py` 的 `build_continuous` 實作 `unadj_*` 四欄：於 `rows.append` 階段（回溯平移**之前**）擷取原始 OHLC，平移迴圈只作用於既有 `price_cols`，不得觸及 `unadj_*`；同步更新模組 docstring 說明兩組價格的用途分工 — FR-001（T004/T005 轉綠）
- [ ] T007 [P] 在 `tests/test_lookahead_bias.py` 新增防禦測試（先紅→綠）：斷言 `unadj_*` 不得由「調整後價 − 位移量」導出——以截斷序列驗證未調整價不變、並斷言實作未讀取未來列 — FR-007/FR-011
- [ ] T008 [P] 在 `tests/test_acceptance_data_quality.py` 新增測試（先紅）：`unadj_*` 存在且含非正值時，即使 `allow_nonpositive_prices=True` 仍須被擋下；調整後價含負值時仍照常放行 — 對應 FR-003
- [ ] T009 在 `data_ingestion.py` 的 `validate_data_contract` 加入未調整欄位嚴格正值（且有限）檢查，**必須置於 `allow_nonpositive_prices` 提早 return 之前**（現行於 `data_ingestion.py:162-163` return），否則對連續層永遠不會執行 — FR-003（T008 轉綠）
- [ ] T010 在 `run_ingestion.py` 通用資料路徑（非 taifex 分支）為 **futures 資產類別**補上 `unadj_* = 對應調整後欄位`，使 MTX/mock 與 csv 期貨來源亦具備此欄位；equity 路徑不得加此欄 — FR-009（MTX 不經 rollover，見 research.md D3）
- [ ] T011 重建連續層：執行 `python run_ingestion.py`，確認 `fut_TXF_daily` 重建為 6,947 根且新增四欄、`fut_MTX_daily` 亦具備四欄；以 SQL 驗 `unadj_close <= 0 OR unadj_open <= 0` 計數為 0 — FR-002/SC-004

**Checkpoint**: 資料層完備——連續表帶正確且恆正的未調整價，user story 可開工

---

## Phase 3: User Story 1 — 全歷史回測以真實價位計算口數與保證金（P1）🎯 MVP

**Goal**: 口數與保證金改以未調整收盤價計名目值，解除因價格基準失真而誤觸的
爆倉護欄，讓 1998 起全歷史回測跑得完。

**Independent Test**: 全歷史 TXF 回測跑通；任取一筆早期交易，口數等於
`floor(權益 × margin_utilization ÷ (unadj_close × point_value × margin_rate))`；
1999-06-21 不再是 463 口。

### Tests for User Story 1（先紅）

- [ ] T012 [P] [US1] 在 `tests/test_futures_backtest_e2e.py` 新增期貨 sizing 基準測試：以含 `unadj_*` 的合成資料（刻意讓調整後價遠低於未調整價、甚至為負）斷言口數以 `unadj_close` 手算式計得，且調整後價為負時不再產生天量口數或負保證金；該檔既有 `test_futures_e2e_margin_sizer_full` 與 `test_futures_blowup_terminates_and_flags` 為鄰近先例 — 對應 SC-001、spec Edge Case
- [ ] T013 [P] [US1] 在 `tests/test_futures_backtest_guard.py` 新增缺欄硬失敗測試：以**不含** `unadj_*` 的期貨資料框執行回測，斷言拋出 `ValueError`、訊息含表名與重建提示，且**不產生任何回測結果**；同時斷言現貨資料框（本就無此欄）不受影響照常執行。該檔正是「引擎對 futures 的接受/拒絕語意」歸屬地（008b 護欄反轉即記於此）— 對應 SC-007、FR-008 作用域

### Implementation for User Story 1

- [ ] T014 [US1] 在 `backtester.py` 引擎初始化處加入期貨欄位守門：`is_futures` 且缺 `unadj_open`/`unadj_close` 時 `raise ValueError`（含表名、缺欄清單、`run_ingestion.py` 重建指令）；**不得**在迴圈內檢查、**不得**留任何 fallback 分支 — FR-008（T013 轉綠）
- [ ] T015 [US1] 在 `backtester.py` 將做多進場的 `sizing_price` 由 `sig_row['close']` 改為 `sig_row['unadj_close']`（現行於 `backtester.py:287`），保證金占用計算沿用同一變數（`backtester.py:336`）— FR-004
- [ ] T016 [US1] 在 `backtester.py` 對做空進場套用同一改動（現行於 `backtester.py:345`、保證金於 `:383`），維持多空鏡像對稱（spec 003 既有原則）— FR-004
- [ ] T017 [US1] 在 trades 記錄中明確 `sizing_price` 語意已為未調整價，並增列調整後訊號根收盤欄以便驗收比對（`backtester.py:333/379`）— data-model.md §5

**Checkpoint**: 全歷史 TXF 回測應可完整跑完且口數合理——MVP 達成

---

## Phase 4: User Story 2 — 期交稅以真實名目價值計算（P2）

**Goal**: 期交稅改以成交當根未調整開盤價（套同一滑價點數）計名目值，
稅額恆正且與當年真實成本一致。

**Independent Test**: 任取一筆早期交易，稅額 =
`(unadj_open ± 滑價點數) × point_value × 口數 × tax_rate` 手算可對上且為正；
每口定額手續費與滑價點數不變。

### Tests for User Story 2（先紅）

- [ ] T018 [P] [US2] 在 `tests/test_trading_costs.py` 新增稅基測試：驗證以未調整成交價計得之稅額為正且符合手算；並以「調整後價為負」的情境斷言舊基準會產生負稅額（證明測試有鑑別力）— 對應 SC-006
- [ ] T019 [P] [US2] 在 `tests/test_trading_costs.py` 斷言滑價自洽性：`slip(unadj_open)` 與 `slip(open)` 的差恆等於 `open − unadj_open`（期貨滑價為點數加減，非比例）— data-model.md §2

### Implementation for User Story 2

- [ ] T020 [US2] 在 `backtester.py` 進場路徑改以未調整成交價呼叫 `cost_model.entry_costs`：對 `row['unadj_open']` 套用 `cost_model.slip` 得未調整成交價，傳入成本計算；**成交價本身（PnL 用）仍為調整後**（現行進場於 `backtester.py:282/302`、`:344/358`）— FR-005/FR-006
- [ ] T021 [US2] 在 `backtester.py` 對所有出場路徑套用同一改動：部分出場（`:410/419`）、一般出場（`:451/455`）、強制結清（`:502`）；確認 `trading_costs.py` **簽章零改動** — FR-005、contracts C1/C2

**Checkpoint**: US1 + US2 皆可獨立運作，成本數字達真實水準

---

## Phase 5: User Story 3 — 訊號與每點損益回歸不變（P3）

**Goal**: 證明本案只改變部位規模與成本計量，未改變策略行為。

**Independent Test**: 同一資料同一參數，修正前後進出場時點與方向 100% 一致、
每點損益增量逐筆相等；差異僅在口數/保證金/稅及其衍生欄位。

### Tests for User Story 3

- [ ] T022 [P] [US3] 在 `tests/test_futures_backtest_e2e.py` 新增訊號不變性測試：以 T002 基準檔比對修正後的進出場日期與方向序列，斷言完全一致 — 對應 SC-003
- [ ] T023 [P] [US3] 在 `tests/test_futures_backtest_e2e.py` 新增每點損益增量比對測試：斷言逐筆 Δ 點數與基準相同（口數不同不影響每點增量）— 對應 SC-003/FR-006
- [ ] T024 [P] [US3] 在 `tests/test_futures_pipeline_e2e.py` 新增等價退化測試：對 `unadj_* == 調整後欄位` 的資料（mock/MTX 路徑）走 adapter→驗證→存→載→回測，斷言結果與修正前完全一致 — 對應 FR-009/SC-005
- [ ] T025 [P] [US3] 在 `tests/test_trading_costs.py` 補現貨位元不變回歸（該檔為 008b 位元不變承諾的既有歸屬地）：以 T003 基準檔比對現貨成本與 sizing 輸出，斷言逐筆相同 — 對應 SC-005、contracts V2
- [ ] T026 [US3] 監控路徑無回歸驗證：執行 `python monitor_signals.py --once`，確認多出四欄不影響指標組裝與訊號輸出（`monitor_signals.py:127-129` 走同一 `SELECT *`）

**Checkpoint**: 三個 user story 全部獨立可驗

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T027 執行 quickstart 全流程驗收（[quickstart.md](quickstart.md) 步驟 1–5），逐條記錄 SC-001~008 的實測結果於本檔末（如實記錄，含未達成項）
- [ ] T028 `pytest -q` 全綠（憲章硬性關卡）；確認既有 182 passed 未因增欄而轉紅
- [ ] T029 [P] 更新 `specs/010-taifex-real-data/tasks.md` T017 第 5 點的「已知限制」註記，標示已由 spec 011 解決並交叉連結
- [ ] T030 [P] 更新 `CLAUDE.md` 專案地圖：移除 010 條目的「back-adjust 早年價位使 008b 保證金 sizing 失真」已知限制，改記 011 的兩組價格基準分工
- [ ] T031 [P] 更新 `data_sources/rollover.py` 與 `trading_costs.py` 的模組 docstring，明載「調整後供訊號/PnL、未調整供 sizing/稅」的分工與 FR-011 禁止回推之理由
- [ ] T032 清理 `specs/011-unadjusted-sizing-price/baseline/` 暫存對照檔（屬可再生成產物，不應入版控——確認 `.gitignore` 已涵蓋或改置於 scratchpad）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup（Phase 1）**：無依賴，且 **T002 必須在任何程式碼修改前完成**（基準一旦錯過無法重建）
- **Foundational（Phase 2）**：依賴 Setup；**阻塞所有 user story**
- **US1（Phase 3）**：依賴 Foundational
- **US2（Phase 4）**：依賴 Foundational；與 US1 觸碰 `backtester.py` 不同段落，可平行但需注意同檔衝突
- **US3（Phase 5）**：依賴 US1 + US2 完成（它驗的是兩者的回歸）
- **Polish（Phase 6）**：依賴全部

### Within Each User Story

- 測試先寫且**必須先紅**，再實作轉綠
- Foundational 的資料層先於任何消費端改動
- 同一 user story 內，守門（T014）先於基準替換（T015/T016）——避免中途以缺欄資料跑出誤導結果

### Parallel Opportunities

- T004/T005 可平行（同檔不同測試函式，建議仍序列提交避免衝突）
- T007/T008 可平行（不同測試檔）
- US1 與 US2 的**測試任務**（T012/T013 vs T018/T019）可平行——不同檔案
- US3 的 T022~T025 全部可平行（純驗證、互不影響）
- Polish 的 T029/T030/T031 可平行（不同檔案）

### 同檔衝突警示

`backtester.py` 被 T014~T017（US1）與 T020/T021（US2）共同觸碰。若要平行，
US1 改 sizing 段、US2 改成本呼叫段，需協調；**建議序列執行**（US1 → US2），
本案規模不大，序列成本低於衝突處理成本。

---

## Parallel Example: Foundational 測試先行

```bash
# 先紅階段可同時起（不同檔案）：
Task: "tests/test_lookahead_bias.py 未調整價非未來資訊防禦測試"   # T007
Task: "tests/test_acceptance_data_quality.py 未調整欄位正性不受豁免測試"  # T008
```

## Implementation Strategy

### MVP First（US1 Only）

1. Phase 1 Setup（**T002 基準務必先做**）
2. Phase 2 Foundational（阻塞層，資料先正確）
3. Phase 3 US1 → **停下驗證**：全歷史回測跑通、1999-06-21 口數合理
4. 此時已解除爆倉護欄誤觸，010 記錄的已知限制主體已解決

### Incremental Delivery

1. Setup + Foundational → 資料層完備（連續表帶未調整價）
2. + US1 → 口數/保證金正確（MVP，可獨立交付）
3. + US2 → 成本數字達真實水準
4. + US3 → 回歸保證成立，可安心合併
5. Polish → 文件同步、010 已知限制結案

---

## Notes

- **路徑一律雙引號**（CLAUDE.md 鐵律 1）；檔案操作優先用 Read/Edit/Write 工具
- 每完成一個任務或邏輯群組即 commit；**commit 後立刻 push**（scratchpad 與
  worktree 皆不保證跨 session 存活）
- 合併前 `pytest -q` 全綠為硬性關卡；影響訊號邏輯的變更需附前後回測對照
  （本案的對照即 US3 的等價驗證）
- **禁止**為了省欄位而以「調整後價 − 位移量」回推未調整價（FR-011）——
  這是本案最容易被後續改動悄悄破壞的地方
- 驗收數字如實記錄，未達成項照實寫（spec 010 T017 既有實踐）
