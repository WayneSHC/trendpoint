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

- [X] T001 複製全歷史 raw 資料庫到工作目錄：`cp ".claude/worktrees/awesome-liskov-efbdf6/trendpoint.db" "./trendpoint.db"`，並以 `sqlite3` 確認 `fut_TXF_raw_daily` 為 34,722 列、`fut_TXF_daily` 為 6,947 根且**僅有** `datetime/open/high/low/close/volume` 六欄（此即 FR-008 硬失敗的現況測資）
  - ✅ 實測：34,722 / 6,947 / 六欄無 `unadj_*`，與 010 T017 記錄一致
- [X] T002 擷取「修正前」基準：以現況程式碼執行 `python run_backtest.py`，將 trades/equity CSV 另存至 **`data/011-baseline/`**（改置於已 gitignore 的 `data/`，避免可再生成產物入版控——憲章 VI），並記錄 commit SHA 於 `BASELINE_INFO.txt`；此為 SC-003 的比對來源
  - ✅ 實測（commit `4bd14a2`）：TXF 總報酬 **−636.06%**、進場 2 次、**單次口數 max 463**、佔用保證金 max 2,733,412 元、**觸發爆倉**。首筆 `1999-06-21,BUY,463.0,price=139.0,sizing_price=98.0` — bug 完整重現，與 010 T017 逐字吻合
- [X] T003 [P] 記錄現況現貨回測輸出作為 008b 位元不變回歸的對照，存於同一 baseline 目錄
  - ✅ 實測：2330 +26.70%／0050 +0.66%／00878 +1.55%／00919 +9.14%／00631L +36.44%；MTX（mock 期貨）+9.16%、10 口

**Checkpoint**: raw 在庫、修正前行為已凍結成檔，可以開始改碼

---

## Phase 2: Foundational（未調整欄位的產生與把關）—— 阻塞所有 user story

**Purpose**: 讓連續層產出 `unadj_*` 四欄、讓所有期貨來源都有此欄位、
讓資料契約守住正性。US1/US2/US3 全部依賴這一層。

**⚠️ CRITICAL**: 本階段未完成前，任何 user story 都無法開始

- [X] T004 [P] 在 `tests/test_rollover.py` 新增未調整欄位透傳測試（**先紅**）：以手算小資料框斷言 `build_continuous` 輸出含 `unadj_open/high/low/close`，且其值等於對應日近月契約的原始 OHLC（不受平移影響）— 對應 SC-002
  - ✅ 綠。錨定值 unadj_close=[100,102,111,112,113,121,122,123]（調整後為 115,117,118,119,120,121,122,123）
- [X] T005 [P] 在 `tests/test_rollover.py` 新增**截斷不變性**測試（先紅）：對同一 raw 取全量與截斷至第 k 根兩種輸入建構連續序列，斷言前 k 根的 `unadj_*` 完全相同；同時斷言調整後 `close` **允許**不同（對照組，證明測試有鑑別力）。寫法可沿用 `tests/test_acceptance_parity.py` 既有的前綴一致性範式（截斷點 i + `check_exact=True` 零容差）— 對應 SC-008
  - ✅ 綠。截於 2023-01-05：unadj_* 逐位元相同；調整後 close 確實改變（鑑別力對照成立）
- [X] T006 在 `data_sources/rollover.py` 的 `build_continuous` 實作 `unadj_*` 四欄：於 `rows.append` 階段（回溯平移**之前**）擷取原始 OHLC，平移迴圈只作用於既有 `price_cols`，不得觸及 `unadj_*`；同步更新模組 docstring 說明兩組價格的用途分工 — FR-001（T004/T005 轉綠）
  - ✅ 實作於 rollover.py — 平移前擷取 o/h/l/c，`price_cols` 不含 unadj_*；模組 docstring 補兩組價格分工與 FR-011 禁令
- [X] T007 [P] 在 `tests/test_lookahead_bias.py` 新增防禦測試（先紅→綠）：斷言 `unadj_*` 不得由「調整後價 − 位移量」導出——以截斷序列驗證未調整價不變、並斷言實作未讀取未來列 — FR-007/FR-011
  - ✅ 新增 3 個測試於 test_lookahead_bias.py，含**反證**：位移量回推確實隨截斷改變（證明 FR-011 非過度設計）
- [X] T008 [P] 在 `tests/test_acceptance_data_quality.py` 新增測試（先紅）：`unadj_*` 存在且含非正值時，即使 `allow_nonpositive_prices=True` 仍須被擋下；調整後價含負值時仍照常放行 — 對應 FR-003
  - ✅ 新增 4 個測試於 test_acceptance_data_quality.py（含現貨無欄位不受影響之作用域測試）
- [X] T009 在 `data_ingestion.py` 的 `validate_data_contract` 加入未調整欄位嚴格正值（且有限）檢查，**必須置於 `allow_nonpositive_prices` 提早 return 之前**（現行於 `data_ingestion.py:162-163` return），否則對連續層永遠不會執行 — FR-003（T008 轉綠）
  - ✅ 實作於 data_ingestion.py — 檢查置於 allow_nonpositive_prices 提早 return **之前**
- [X] T010 在 `run_ingestion.py` 通用資料路徑（非 taifex 分支）為 **futures 資產類別**補上 `unadj_* = 對應調整後欄位`，使 MTX/mock 與 csv 期貨來源亦具備此欄位；equity 路徑不得加此欄 — FR-009（MTX 不經 rollover，見 research.md D3）
  - ✅ 實作於 run_ingestion.py 通用路徑（補 AssetClass 匯入）；MTX 已確認帶四欄
- [X] T011 重建連續層：執行 `python run_ingestion.py`，確認 `fut_TXF_daily` 重建為 6,947 根且新增四欄、`fut_MTX_daily` 亦具備四欄；以 SQL 驗 `unadj_close <= 0 OR unadj_open <= 0` 計數為 0 — FR-002/SC-004
  - ✅ 實測：fut_TXF_daily 6,947 根、四欄齊備、缺值 0、非正值 0；調整後負值仍 2,259 根（010 行為未變）。1999-06-21 調整後 188.0 vs 未調整 **8,439.0**（約 45 倍差距＝保證金低估倍率）

**Checkpoint**: 資料層完備——連續表帶正確且恆正的未調整價，user story 可開工

---

## Phase 3: User Story 1 — 全歷史回測以真實價位計算口數與保證金（P1）🎯 MVP

**Goal**: 口數與保證金改以未調整收盤價計名目值，解除因價格基準失真而誤觸的
爆倉護欄，讓 1998 起全歷史回測跑得完。

**Independent Test**: 全歷史 TXF 回測跑通；任取一筆早期交易，口數等於
`floor(權益 × margin_utilization ÷ (unadj_close × point_value × margin_rate))`；
1999-06-21 不再是 463 口。

### Tests for User Story 1（先紅）

- [X] T012 [P] [US1] 在 `tests/test_futures_backtest_e2e.py` 新增期貨 sizing 基準測試：以含 `unadj_*` 的合成資料（刻意讓調整後價遠低於未調整價、甚至為負）斷言口數以 `unadj_close` 手算式計得，且調整後價為負時不再產生天量口數或負保證金；該檔既有 `test_futures_e2e_margin_sizer_full` 與 `test_futures_blowup_terminates_and_flags` 為鄰近先例 — 對應 SC-001、spec Edge Case
  - ✅ 新增 sizing 基準測試 + 負調整價健全性測試（口數/保證金不得為 0 或負）
- [X] T013 [P] [US1] 在 `tests/test_futures_backtest_guard.py` 新增缺欄硬失敗測試：以**不含** `unadj_*` 的期貨資料框執行回測，斷言拋出 `ValueError`、訊息含表名與重建提示，且**不產生任何回測結果**；同時斷言現貨資料框（本就無此欄）不受影響照常執行。該檔正是「引擎對 futures 的接受/拒絕語意」歸屬地（008b 護欄反轉即記於此）— 對應 SC-007、FR-008 作用域
  - ✅ 新增 3 測試：缺欄拋錯、錯誤訊息含缺欄清單與 run_ingestion 重建指令、現貨不受牽連

### Implementation for User Story 1

- [X] T014 [US1] 在 `backtester.py` 引擎初始化處加入期貨欄位守門：`is_futures` 且缺 `unadj_open`/`unadj_close` 時 `raise ValueError`（含表名、缺欄清單、`run_ingestion.py` 重建指令）；**不得**在迴圈內檢查、**不得**留任何 fallback 分支 — FR-008（T013 轉綠）
  - ✅ 守門置於 backtester.py:160-169（指標計算與逐根迴圈之前），無 fallback 分支
- [X] T015 [US1] 在 `backtester.py` 將做多進場的 `sizing_price` 由 `sig_row['close']` 改為 `sig_row['unadj_close']`（現行於 `backtester.py:287`），保證金占用計算沿用同一變數（`backtester.py:336`）— FR-004
  - ✅ backtester.py 多方 sizing_price → sig_row['unadj_close']
- [X] T016 [US1] 在 `backtester.py` 對做空進場套用同一改動（現行於 `backtester.py:345`、保證金於 `:383`），維持多空鏡像對稱（spec 003 既有原則）— FR-004
  - ✅ 空方同步改動，多空鏡像對稱維持
- [X] T017 [US1] 在 trades 記錄中明確 `sizing_price` 語意已為未調整價，並增列調整後訊號根收盤欄以便驗收比對（`backtester.py:333/379`）— data-model.md §5
  - ✅ trades 增列 `sizing_price_adj`。**實測 1999-06-21：口數 463 → 5、sizing_price 98 → 8,349（adj 併記 98）、成交價 139.0 不變**

**Checkpoint**: 全歷史 TXF 回測應可完整跑完且口數合理——MVP 達成

---

## Phase 4: User Story 2 — 期交稅以真實名目價值計算（P2）

**Goal**: 期交稅改以成交當根未調整開盤價（套同一滑價點數）計名目值，
稅額恆正且與當年真實成本一致。

**Independent Test**: 任取一筆早期交易，稅額 =
`(unadj_open ± 滑價點數) × point_value × 口數 × tax_rate` 手算可對上且為正；
每口定額手續費與滑價點數不變。

### Tests for User Story 2（先紅）

- [X] T018 [P] [US2] 在 `tests/test_trading_costs.py` 新增稅基測試：驗證以未調整成交價計得之稅額為正且符合手算；並以「調整後價為負」的情境斷言舊基準會產生負稅額（證明測試有鑑別力）— 對應 SC-006
  - ✅ 新增元件層稅基測試（含負調整價算出負稅額的鑑別力對照）+ 引擎層稅基測試 2 則
- [X] T019 [P] [US2] 在 `tests/test_trading_costs.py` 斷言滑價自洽性：`slip(unadj_open)` 與 `slip(open)` 的差恆等於 `open − unadj_open`（期貨滑價為點數加減，非比例）— data-model.md §2
  - ✅ 滑價基準無關性測試：slip(adj) − slip(unadj) ≡ adj − unadj（點數偏移非比例）

### Implementation for User Story 2

- [X] T020 [US2] 在 `backtester.py` 進場路徑改以未調整成交價呼叫 `cost_model.entry_costs`：對 `row['unadj_open']` 套用 `cost_model.slip` 得未調整成交價，傳入成本計算；**成交價本身（PnL 用）仍為調整後**（現行進場於 `backtester.py:282/302`、`:344/358`）— FR-005/FR-006
  - ✅ 新增 `cost_basis_price` 閉包集中處理（backtester.py:171-182）；進場兩處改走未調整基準
- [X] T021 [US2] 在 `backtester.py` 對所有出場路徑套用同一改動：部分出場（`:410/419`）、一般出場（`:451/455`）、強制結清（`:502`）；確認 `trading_costs.py` **簽章零改動** — FR-005、contracts C1/C2
  - ✅ 出場三處（部分/一般/爆倉強制結清）同步；爆倉以當根收盤成交故取 unadj_close。**trading_costs.py 簽章零改動**

**Checkpoint**: US1 + US2 皆可獨立運作，成本數字達真實水準

---

## Phase 5: User Story 3 — 訊號與每點損益回歸不變（P3）

**Goal**: 證明本案只改變部位規模與成本計量，未改變策略行為。

**Independent Test**: 同一資料同一參數，修正前後進出場時點與方向 100% 一致、
每點損益增量逐筆相等；差異僅在口數/保證金/稅及其衍生欄位。

### Tests for User Story 3

- [X] T022 [P] [US3] 在 `tests/test_futures_backtest_e2e.py` 新增訊號不變性測試：以 T002 基準檔比對修正後的進出場日期與方向序列，斷言完全一致 — 對應 SC-003
  - ✅ 改用自足式不變性驗證（同調整後序列 × 不同 unadj 尺度）——時點/方向/成交價/事件逐位元相同，不依賴人工保存的基準檔
- [X] T023 [P] [US3] 在 `tests/test_futures_backtest_e2e.py` 新增每點損益增量比對測試：斷言逐筆 Δ 點數與基準相同（口數不同不影響每點增量）— 對應 SC-003/FR-006
  - ✅ 每點損益增量逐筆相同；另加鑑別力對照（每口保證金精確 50 倍、每口稅額扣除固定滑價點數後等比）
- [X] T024 [P] [US3] 在 `tests/test_futures_pipeline_e2e.py` 新增等價退化測試：對 `unadj_* == 調整後欄位` 的資料（mock/MTX 路徑）走 adapter→驗證→存→載→回測，斷言結果與修正前完全一致 — 對應 FR-009/SC-005
  - ✅ mock 期貨走 adapter→驗證→存→載，四欄存活且與原價逐位元相等
- [X] T025 [P] [US3] 在 `tests/test_trading_costs.py` 補現貨位元不變回歸（該檔為 008b 位元不變承諾的既有歸屬地）：以 T003 基準檔比對現貨成本與 sizing 輸出，斷言逐筆相同 — 對應 SC-005、contracts V2
  - ✅ 現貨路徑帶荒謬 unadj 值仍逐位元相同；另以 A/B 實驗（舊碼＋新資料）證實現貨數字變動源自 yfinance 回溯改寫，非本案程式
- [X] T026 [US3] 監控路徑無回歸驗證：執行 `python monitor_signals.py --once`，確認多出四欄不影響指標組裝與訊號輸出（`monitor_signals.py:127-129` 走同一 `SELECT *`）
  - ✅ `monitor_signals.py --once` TXF/MTX 皆正常。**附帶發現**（與本案無關的既有缺陷）：TAIFEX 當日端點回英文表頭時解析失敗，且 spec 010 T017 聲稱已修的雙語映射實際不在程式碼中——已另立任務

**Checkpoint**: 三個 user story 全部獨立可驗

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T027 執行 quickstart 全流程驗收（[quickstart.md](quickstart.md) 步驟 1–5），逐條記錄 SC-001~008 的實測結果於本檔末（如實記錄，含未達成項）
  - ✅ 見下方「T027 驗收實錄」——SC-001~008 全數通過
- [X] T028 `pytest -q` 全綠（憲章硬性關卡）；確認既有 182 passed 未因增欄而轉紅
  - ✅ **206 passed, 1 deselected**（基準 182 → 新增 24 個測試，既有測試無轉紅）
- [X] T029 [P] 更新 `specs/010-taifex-real-data/tasks.md` T017 第 5 點的「已知限制」註記，標示已由 spec 011 解決並交叉連結
  - ✅ 010 tasks.md T017 第 5 點加註「已由 spec 011 解決」含實測數字與負稅額修正
- [X] T030 [P] 更新 `CLAUDE.md` 專案地圖：移除 010 條目的「back-adjust 早年價位使 008b 保證金 sizing 失真」已知限制，改記 011 的兩組價格基準分工
  - ✅ CLAUDE.md 專案地圖改記 011 的兩組價格分工、FR-011 禁令與缺欄硬失敗紀律
- [X] T031 [P] 更新 `data_sources/rollover.py` 與 `trading_costs.py` 的模組 docstring，明載「調整後供訊號/PnL、未調整供 sizing/稅」的分工與 FR-011 禁止回推之理由
  - ✅ rollover.py（Phase 2 已補）與 trading_costs.py 模組 docstring 載明價格基準分工——後者程式碼零改動但語意已變，必須寫明
- [X] T032 確認 `data/011-baseline/` 未入版控（`data/` 已在 `.gitignore` 第 22 行涵蓋）；驗收數字已落檔於本檔各任務註記，基準 CSV 可安全刪除
  - ✅ `git status --porcelain data/` 為空，確認 baseline 未入版控（.gitignore:22 涵蓋 data/）

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

---

## T027 驗收實錄（2026-07-18，全部如實記錄）

執行環境：`/opt/miniconda3/bin/python`（3.13.13，pandas 3.0.2）——worktree 無 `.venv`，
主 repo 亦無；miniconda 已備齊全部依賴，`pytest -q` 基準線 182 passed 與 010 記錄一致。

| SC | 標準 | 實測 | 判定 |
|----|------|------|------|
| SC-001 | 全歷史跑通、不因基準失真爆倉；1999-06-21 口數合理 | 6,947 根完整執行，**無爆倉**；1999-06-21 進場 **463 → 5 口**，sizing 基準 98 → 8,349 | ✅ |
| SC-002 | 抽驗 ≥20 交易日，unadj 等於原始近月 OHLC | 抽驗 20 日 × 4 欄（1998-07-21 ~ 2025-02-05），**不符 0 項** | ✅ |
| SC-003 | 訊號時點/方向 100% 一致、每點損益逐筆相等 | 逐筆比對：日期、成交價（139.0/378.0/−2.0）、事件**完全相同**；另有自足式不變性測試（同序列 × 不同 unadj 尺度） | ✅ |
| SC-004 | 覆蓋率 100% 且全為正 | 6,947 根，**缺值 0、非正 0**；對照調整後價仍有 9,026 項 ≤ 0（010 行為未變） | ✅ |
| SC-005 | 既有測試全綠 + 新測試通過 | **206 passed, 1 deselected**（基準 182 → +24） | ✅ |
| SC-006 | 稅以未調整成交價計、手算一致且恆正 | 引擎層與元件層測試皆綠；**1999-07-14 出場稅額由 −1.856（負）修正為 +98.988** | ✅ |
| SC-007 | 缺欄舊資料明確失敗、不產生結果 | 以真實 6 欄舊表實測：拋 `ValueError`，訊息含缺欄清單與 `run_ingestion.py` 重建指令 | ✅ |
| SC-008 | 截斷重建後 unadj 不變（調整後允許變） | 截於 2010-01-01（2,895 根）：unadj **完全相同**；調整後 close **有變動**（鑑別力對照成立） | ✅ |

### 全歷史回測數字（同一份資料，逐階段）

| 階段 | TXF 總報酬 | 進場次數 | 單次最大口數 | 爆倉 |
|------|-----------|---------|------------|------|
| 修正前（基準，commit `4bd14a2`） | **−636.06%** | 2 | **463** | **是** |
| 僅 sizing 修正（US1） | +81.02% | 24 | 5 | 否 |
| 含稅基修正（US2，最終） | **+54.43%** | 24 | 5 | 否 |

US2 使報酬由 81.02% 降至 54.43% 屬**預期且誠實**：早年稅額原以 188 點名目值計，
修正後以真實 8,439 點計，摩擦成本高約 45 倍。憲章原則 II 要求成本真實，不得為了
數字好看而回避。

MTX（mock 期貨）**9.16%／10 口／保證金 469,839 全程不變**——FR-009 等價退化成立。

### 現貨數字變動之歸因（重要）

T011 重跑 `run_ingestion.py` 後，2330 由 26.70% → 17.50%、00878 由 1.55% → 1.99%
（0050／00919／00631L 不變）。**經 A/B 實驗證實與本案程式無關**：在 commit `4bd14a2`
（改碼前）建臨時 worktree、餵**同一份新資料庫**執行，得到完全相同的 17.50%／1.99%，
而 TXF 仍為 −636.06%。成因為 yfinance auto-adjust 在配息後**回溯改寫歷史價**
（K 線根數不變、值改變），與 spec 010 T017 記載的現象同源。現貨位元不變承諾成立。

### 附帶發現（不屬本案範圍，已另立任務）

`monitor_signals.py --once` 顯示 TAIFEX 當日端點回**英文表頭**時解析失敗
（降級改用庫內資料，不中斷）。查證：`data_sources/taifex_source.py` 的 `_COL_MAP`
只有中文鍵、全檔無任何英文欄位字串；而 spec 010 T017 第 1 點記載「parser 已改
雙語映射（commit `4479cd5`）」——該 SHA 不在目前歷史中，該修正實際未落地。
此為規格↔程式漂移（憲章原則 III），已立獨立任務處理（含更正 010 記錄）。
