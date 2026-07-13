---
description: "Task list for 008a — 台指期資料管線 + Instrument 抽象（純資料層）"
---

# Tasks: 台指期資料管線 + Instrument 抽象（純資料層）

**Input**: Design documents from `specs/008-taifex-data-pipeline/`

**Prerequisites**: plan.md、spec.md、research.md、data-model.md、contracts/、quickstart.md

**Tests**: 憲章 III 與 spec FR-010 要求測試 → 生成測試任務（TDD：先寫失敗測試再實作）。

**Organization**: 依 user story 分組。Foundational 承載共用抽象；US1（期貨資料端到端）、US2（現貨向後相容/parity）、US3（回測 fail-fast 護欄）。**現貨路徑位元不變**為橫向硬約束（US2 驗證，貫穿全程）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔、無未完成相依）
- **[Story]**: US1 / US2 / US3
- 路徑為 repo 根相對路徑

---

## Phase 1: Setup

- [ ] T001 [P] 擷取回歸基準：跑 `pytest -q` 與 `python run_backtest.py` 記錄現況（綠燈 + 代表標的 equity 回測數字），作為 SC-001 parity 對照，寫入 `specs/008-taifex-data-pipeline/baseline-equity.md`
- [ ] T002 建立 `data_sources/` 套件骨架（`data_sources/__init__.py`、`data_sources/base.py` 空殼）

---

## Phase 2: Foundational（阻塞性前置）

**Purpose**: 共用抽象——阻塞所有 user story。

**⚠️ CRITICAL**: 本階段完成前，US1/US2/US3 不得開工。

- [ ] T003 於 `instruments.py` 定義 `AssetClass`(Enum: equity｜futures) 與 `Instrument`(Pydantic frozen：id/asset_class/source/display_name/timeframes)
- [ ] T004 於 `config/config.py` 新增 `InstrumentSpec` 與 `DataConfig.instruments`，`DataQualityConfig` 擴充 per-asset-class 門檻（equity 維持 3.0）；`config/config.yaml` 新增一個範例 `source=mock` 的 futures instrument、保留既有 `tickers`
- [ ] T005 於 `instruments.py` 實作 `InstrumentRegistry`（`resolve(id)`/`all()`）：合併 `data.tickers`（→equity/yfinance）+ `data.instruments`；`id` 唯一，衝突 fail-fast
- [ ] T006 於 `data_sources/base.py` 定義 `DataSourceAdapter` 抽象（`source_key`、`fetch(instrument, timeframe)->OHLCV`）；`data_sources/__init__.py` 提供 `get_adapter(source_key)` 分派（未知鍵 fail-fast）
- [ ] T007 於 `db_security.py` 新增 `table_name_for(instrument, timeframe)`（equity→`stock_{clean_id}_{tf}` 逐字元同現行、futures→`fut_{clean_id}_{tf}`），並將 `TABLE_NAME_PATTERN` 放寬為 `^(stock|fut)_[a-zA-Z0-9_]+_(daily|5m)$`

**Checkpoint**: 抽象就緒，US 可開工。

---

## Phase 3: User Story 1 — 宣告式接入新資產類別資料（Priority: P1）🎯 MVP

**Goal**: 註冊一個期貨 instrument（mock/csv 來源）即能 ingest→驗證→存→載出連續 OHLCV，零改引擎。

**Independent Test**: `pytest tests/test_futures_pipeline_e2e.py tests/test_data_sources.py` 綠——mock futures（含 rollover 跳空）走完整資料路徑並符合資料契約。

### Tests for User Story 1 ⚠️（先寫，先 FAIL）

- [ ] T008 [P] [US1] `tests/test_data_sources.py`：adapter `fetch` 回傳契約（OHLCV + datetime 遞增、正價、量≥0、因果）；`get_adapter` 未知 source fail-fast
- [ ] T009 [P] [US1] `tests/test_futures_pipeline_e2e.py`：mock futures（含 rollover 跳空）→ ingest→驗證→存（`fut_*` 表）→載，斷言符合資料契約

### Implementation for User Story 1

- [ ] T010 [US1] `data_sources/mock_source.py`（確定性連續序列，**含一段 rollover 跳空**）與 `data_sources/csv_source.py`（讀 CSV→正規化），皆符合 `DataSourceAdapter` 契約
- [ ] T011 [US1] `data_ingestion.py`：`fetch` 改走 `get_adapter(instrument.source).fetch(...)`；`validate_data_contract` 新增 `asset_class` 參數（離群門檻依資產類別取值），`clean_kline_dataframe` ffill-only 不變
- [ ] T012 [US1] `run_ingestion.py`：迭代 `InstrumentRegistry.all()`、依 source 分派 adapter、以 `table_name_for` 命名存表
  - **跨 story 耦合（analyze I1）**：US1 的 e2e（mock）自足；但**完整** `run_ingestion`（含 equity instrument）依賴 US2 的 `YfinanceAdapter`（T015）已註冊。實作順序上，若要對真實 equity 標的跑 ingestion，需先完成 T015。

**Checkpoint**: US1 完成 → SC-002（期貨資料端到端）。

---

## Phase 4: User Story 2 — 現貨路徑位元不變（Priority: P2）

**Goal**: 既有 equity 的匯入/表命名/回測/parity 完全不變（零回歸）。

**Independent Test**: 全套 `pytest`（含 spec 004 parity）綠；`run_backtest.py` 代表標的數字與 T001 baseline 逐位元相同；既有 `stock_*` 表名不變。

### Tests for User Story 2 ⚠️

- [ ] T013 [P] [US2] `tests/test_instrument_registry.py`：純字串 ticker→`equity`/`yfinance`；結構化 instruments 解析；`id` 衝突 fail-fast（SC-005）
- [ ] T014 [P] [US2] `tests/test_table_naming.py`：equity `table_name_for` 回傳與現行 `stock_*` 逐字元相同；futures→`fut_*`；regex 接受兩者、拒絕非法（SC-003）

### Implementation for User Story 2

- [ ] T015 [US2] `data_sources/yfinance_source.py`：`YfinanceAdapter` 包裝現行 `fetch_stock_data` + `clean_kline_dataframe`，行為與現行一致
- [ ] T016 [US2] 表名讀取呼叫點改用 `table_name_for`：`run_backtest.py` / `run_portfolio_backtest.py` / `optimizer.py` / `run_ablation.py` / `run_walk_forward.py` / `app.py`（唯一導出點）
- [ ] T017 [US2] 回歸驗證：全套 `pytest -q` 綠 + `run_backtest.py` 代表標的數字與 `baseline-equity.md` 逐位元相同（**SC-001 parity**）

**Checkpoint**: US2 完成 → SC-001（現貨位元不變）/SC-005（向後相容解析）。

---

## Phase 5: User Story 3 — 期貨回測 fail-fast 護欄（Priority: P3）

**Goal**: 對期貨 instrument 呼叫回測，入口層與引擎層皆 fail-fast，零垃圾數字。

**Independent Test**: `pytest tests/test_futures_backtest_guard.py` 綠——雙層對 futures 皆拋錯；equity 正常。

### Tests for User Story 3 ⚠️

- [ ] T018 [P] [US3] `tests/test_futures_backtest_guard.py`：入口層與引擎層對 futures 皆拋明確錯誤（零績效數字）；equity 正常回測（SC-004）

### Implementation for User Story 3

- [ ] T019 [US3] 引擎層護欄：`backtester.py` / `portfolio_backtester.py` 的回測方法新增 `asset_class: str = "equity"` 參數，`=="futures"` 立即拋明確錯誤（**僅拒絕、不引入成本/Instrument 依賴**；預設 equity → 既有呼叫零影響）
- [ ] T020 [US3] 入口層護欄：`run_backtest.py` / `run_portfolio_backtest.py` 於 dispatch 前檢查 `instrument.asset_class`，futures → 拋明確錯誤

**Checkpoint**: US3 完成 → SC-004（雙層 fail-fast）。

---

## Phase 6: Polish & Cross-Cutting

- [ ] T021 [P] grep 檢查無殘留硬編表名（`f"stock_{...}"` / `f"fut_{...}"`）——`table_name_for` 為唯一導出點（SC-003）
- [ ] T022 [P] `pytest -q` 全綠（含 spec 004 parity）
- [ ] T023 執行 `quickstart.md` V1–V5，確認 SC↔測試對照全數成立
- [ ] T024 [P] 視情況更新 `CLAUDE.md` 專案地圖／`data_sources/` 目錄說明（008a 進度）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 無相依，立即可開始
- **Foundational (Phase 2)**: 依賴 Setup；**阻塞所有 user story**
- **US1 (Phase 3)**: 依賴 Foundational（Instrument/registry/adapter/表命名/驗證）
- **US2 (Phase 4)**: 依賴 Foundational；`table_name_for` + YfinanceAdapter 就位後可驗 parity
- **US3 (Phase 5)**: 依賴 Foundational（Instrument/asset_class）；與 US1/US2 獨立
- **Polish (Phase 6)**: 依賴目標 story 完成

### User Story Dependencies

- **US1 (P1)** MVP：Foundational 後即可 → SC-002（期貨資料端到端）
- **US2 (P2)**：現貨向後相容/parity——橫向硬約束，須在每個階段維持綠；獨立可驗
- **US3 (P3)**：護欄，獨立於 US1/US2

### Within Each User Story

- 測試先寫且先 FAIL（憲章 III / FR-010）
- Foundational 抽象 → adapter/ingestion → run_ 入口
- 每個 story 完成再進下一優先級

### Parallel Opportunities

- T001 與 T002 可平行；US1 測試 T008/T009、US2 測試 T013/T014 各自可平行
- Foundational 內 T003–T007 多為不同檔，部分可平行（T004 config、T007 db_security 與 T003 instruments 獨立）
- Polish T021/T022/T024 可平行

---

## Parallel Example: Foundational

```bash
Task: "AssetClass + Instrument in instruments.py"          # T003
Task: "config schema + config.yaml in config/"            # T004
Task: "table_name_for + regex in db_security.py"          # T007
# T005(registry) 依賴 T003；T006(adapter base) 獨立
```

---

## Implementation Strategy

### MVP First（US1）

1. Phase 1 Setup（擷取 equity baseline）
2. Phase 2 Foundational（抽象）
3. Phase 3 US1（期貨資料端到端，mock/csv）
4. **STOP & VALIDATE**：SC-002（期貨資料進出）
5. 這是最小可交付：資料層抽象成立、期貨資料能進出，現貨不動

### Incremental Delivery

1. Setup + Foundational → 就緒
2. US1 → 驗 SC-002 → 交付（資料層接縫）
3. US2 → 驗 SC-001/SC-005 → 交付（現貨零回歸保證）
4. US3 → 驗 SC-004 → 交付（誤用護欄）
5. 期貨可交易回測待 008b（成本/口數）+ 003（做空）

### 硬約束管理

- **SC-001 現貨位元不變**：每階段跑 parity；`table_name_for` 對 equity 逐字元同現行是關鍵保護。
- 合併前 `pytest -q` 全綠（含 parity）。

---

## Notes

- [P] = 不同檔、無相依
- 引擎僅加 `asset_class` 護欄旗標（預設 equity），**不**引入成本/Instrument 依賴——成本/sizing dispatch 屬 008b
- 期貨在 008a 只驗資料進出，不做可交易回測（護欄擋住）
- 每完成一任務或邏輯群組即 commit
