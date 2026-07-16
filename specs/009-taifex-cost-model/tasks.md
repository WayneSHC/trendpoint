---
description: "Task list for 009 (008b) — 台指期成本/口數模型（期貨可交易回測）"
---

# Tasks: 台指期成本/口數模型（008b — 期貨可交易回測）

**Input**: Design documents from `specs/009-taifex-cost-model/`

**Prerequisites**: plan.md、spec.md、research.md（D1-D9）、data-model.md、contracts/、quickstart.md

**Tests**: 憲章 III 與 spec FR-010 要求測試 → 生成測試任務（TDD：先寫失敗測試再實作）。

**Organization**: 依 user story 分組。Foundational 承載 schema 與現股元件搬移；
US1（期貨誠實成本）、US2（槓桿 sizing + 會計）、US3（現貨位元不變 parity）。
**現貨 parity 為橫向硬約束**（US3 驗證，貫穿全程——引擎注入後每個 checkpoint 都須既有測試全綠）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔、無未完成相依）
- **[Story]**: US1 / US2 / US3
- 路徑為 repo 根相對路徑

---

## Phase 1: Setup

- [x] T001 [P] 確認回歸基線：`pytest -q` 全綠 + `python run_backtest.py` 4 檔數字與 quickstart V1 表逐位元相同，記錄至 `specs/009-taifex-cost-model/baseline-check.md`（SC-001 對照錨點）
- [x] T002 [P] 建立 `trading_costs.py` 骨架：MPL-2.0 標頭、`CostModel(ABC)`（entry_cost/exit_cost/slip）、`PositionSizer(ABC)`（size/partial_units）、`for_asset_class()` 工廠 stub（契約：contracts/cost-model-contracts.md）

---

## Phase 2: Foundational（阻塞性前置）

**Purpose**: schema 與現股元件——阻塞所有 user story。

**⚠️ CRITICAL**: 本階段完成前，US1/US2/US3 不得開工。

- [x] T003 於 `instruments.py` 定義 `ContractSpec`（Pydantic frozen：point_value>0、tick_size>0 預設 1.0、exchange_fee_per_lot≥0）與 `Instrument.contract: ContractSpec | None = None`；model validator：futures ⟹ contract 必帶、equity ⟹ 必為 None（fail-fast）
- [x] T004 於 `config/config.py` 新增 `FuturesCostConfig`（broker_commission_per_lot=0、tax_rate=0.00002、slippage_ticks=1、margin_rate=0.055、margin_utilization=0.5，驗證界線見 data-model.md）掛於 `TradingCostConfig.futures`（預設齊全→既有 config 零改可載）；`config/config.yaml` 新增 `trading_cost.futures` 區塊 + `data.instruments` 之 TXF（point_value 200/fee 20）、MTX（50/12.5）帶 `contract`
- [x] T005 於 `trading_costs.py` 實作 `EquityCostModel` + `EquitySizer`：自 `backtester.py:232-336` 現行公式**逐字搬移**（slip ×(1±slip_rate)、entry=price×units×commission、exit=price×units×(commission+tax)、size=round_to_lot(equity/(exec_price×(1+commission)))、partial=round_to_lot(held×fraction)）；工廠 equity 分支完成

**Checkpoint**: 抽象與現股元件就緒（引擎尚未觸碰，全套測試仍綠）。

---

## Phase 3: User Story 1 — 期貨誠實摩擦成本回測（Priority: P1）🎯 MVP

**Goal**: 期貨每口成本正確（定額兩邊 + 期交稅兩邊 + tick 滑價）、護欄退役、mock 期貨回測跑通且成本非零、long-only。

**Independent Test**: `pytest tests/test_trading_costs.py tests/test_futures_backtest_e2e.py tests/test_futures_backtest_guard.py` 綠——成本數學吻合錨定例；e2e（測試內顯式注入常數 1 口 sizer，sizing 中立）跑通。

### Tests for User Story 1 ⚠️（先寫，先 FAIL）

- [ ] T006 [P] [US1] `tests/test_trading_costs.py`：FuturesCostModel 錨定例——TX 1 口 @20,000 單邊 = 20 + 80 = 100 NT$；滑價 = 成交價 ±1 tick 點偏移（**不重複計費**）；MTX/TMF 縮放；來回兩邊各收（SC-002）
- [ ] T007 [P] [US1] `tests/test_futures_backtest_e2e.py`：mock TXF → 回測（顯式注入常數 1 口 sizer + FuturesCostModel）→ 跑通、不拋 `FuturesBacktestNotSupportedError`、總摩擦成本 > 0、交易全為多方（SC-004 初步 + SC-006）
- [ ] T008 [P] [US1] 改寫 `tests/test_futures_backtest_guard.py`：語意反轉——**單標的**路徑（引擎 + `run_backtest.py`）futures 不再被拒；**組合路徑（portfolio）拒絕斷言保留**（analyze H1：範圍邊界護欄）；equity 預設路徑不受影響（SC-006）

### Implementation for User Story 1

- [ ] T009 [US1] 於 `trading_costs.py` 實作 `FuturesCostModel`（建構自 ContractSpec + FuturesCostConfig；slip 點偏移、entry/exit = 定額 + 名目×稅率）；工廠 futures 分支（sizer 部分暫 stub，US2 完成）
- [ ] T010 [US1] `backtester.py` 注入改造：`run_backtest(..., cost_model=None, sizer=None, point_value=1.0)`——None 預設現股元件（**既有呼叫零改動**）；232/294/323 行滑價、250/304-305/327-328 行成本、235-236/298 行 sizing/partial 全改走元件；P&L 統一 `units × Δprice × point_value`；**sizer.size 的 price 輸入語意按資產類別**（analyze M1：equity 傳成交價＝現行語意、futures 傳訊號根收盤＝FR-004，docstring 明確化）；改造後立即跑全套既有測試（parity 檢查點）
- [ ] T011 [US1] 護欄退役（**僅單標的路徑**，analyze H1）：`backtester.py` 移除引擎層 `assert_backtestable` 拒絕呼叫（函式與例外**定義保留**，import 相容）；`run_backtest.py` 入口放行 futures；**`run_portfolio_backtest.py`（:51）與 `portfolio_backtester.py`（:223）護欄保留**——組合期貨接入不在本 spec，放行會落入現股成本路徑違反憲章 II（D9 修訂）
- [ ] T012 [US1] `portfolio_backtester.py` 同步注入（引擎方法同簽名、預設現股元件、既有行為不變）

**Checkpoint**: US1 完成 → SC-002（成本數學）+ SC-006（護欄退役）+ SC-004（初步，常數口）。

---

## Phase 4: User Story 2 — 槓桿 sizing 與保證金會計（Priority: P2）

**Goal**: 保證金式整數口 sizing、return-on-margin 權益會計、爆倉終止、期貨完整 e2e。

**Independent Test**: sizing 單元錨定例 + margin-sizer e2e + 看前偏誤防線綠。

### Tests for User Story 2 ⚠️（先寫，先 FAIL）

- [ ] T013 [P] [US2] `tests/test_trading_costs.py` 擴充：FuturesSizer 錨定例——equity 1,000,000 / close 20,000 / TX / rate .055 / util .5 → 2 口；equity 200,000 → 0 口不進場；`partial_units(1,.5)`=0、`(3,.5)`=1（SC-003 / FR-012）
- [ ] T014 [P] [US2] `tests/test_futures_backtest_e2e.py` 擴充：工廠分派 margin sizer 之 TXF+MTX e2e——全程口數非負整數、權益曲線無 NaN、佔用保證金 ≤ 權益×使用率；爆倉情境用**測試內自製極端下跌 K 線直接餵引擎**（analyze M2：mock adapter 為固定向上序列，不依賴之）→ 當根強制結清、曲線截止、`summary` 標記（SC-004 完整 / FR-011）
- [ ] T015 [P] [US2] `tests/test_lookahead_bias.py` 擴充：期貨防線——截斷第 N 根之後資料不改變第 N 根 sizing 決策（口數）與成交價；成交於 N+1 開盤 ± 滑價 tick（SC-005 / FR-007）

### Implementation for User Story 2

- [ ] T016 [US2] 於 `trading_costs.py` 實作 `FuturesSizer`（margin_rate/margin_utilization/floor；partial floor + 0 口語意）；工廠 futures 分支完成
- [ ] T017 [US2] `backtester.py` 會計擴充：持倉 mark-to-market 權益、爆倉檢查（權益 ≤ 0 當根強制結清 + 終止 + `summary["blown_up"]`）、交易紀錄擴充（口數、佔用保證金、成本明細分列）；partial-exit 0 口時跳過平倉但止損照移保本位（FR-012 引擎側）
- [ ] T018 [US2] `run_backtest.py` 入口穿線：futures instrument 經 `for_asset_class` 分派元件、以既有迴圈跑 config 內 mock TXF/MTX；期貨 summary 顯示口數/保證金資訊

**Checkpoint**: US2 完成 → SC-003 + SC-004（完整）+ SC-005。

---

## Phase 5: User Story 3 — 現貨路徑位元不變（Priority: P3）

**Goal**: 現貨零回歸——元件搬移與引擎注入未改變任何現貨數字。

**Independent Test**: equity 元件 contract test + 全套 pytest + 4 檔數字逐位元對照。

### Tests for User Story 3 ⚠️

- [ ] T019 [P] [US3] `tests/test_trading_costs.py` 擴充：Equity 元件 contract test——對隨機 (price, units, equity) 樣本，EquityCostModel/EquitySizer 輸出與現行內聯公式逐位元相同（SC-001 支柱）

### Implementation for User Story 3

- [ ] T020 [US3] 回歸驗證：全套 `pytest -q` 綠 + `python run_backtest.py` 4 檔數字與 `baseline-check.md`（T001）逐位元相同（**SC-001 硬關卡**）；任何 diff → 修正引擎注入直至歸零

**Checkpoint**: US3 完成 → SC-001。

---

## Phase 6: Polish & Cross-Cutting

- [ ] T021 [P] SoT 稽核：quickstart V6 grep——引擎/元件原始碼無硬編碼期貨費率常數（權威值僅存在 config.yaml 與測試錨定值）；config 載入後 Pydantic 驗證通過（SC-007）
- [ ] T022 [P] 執行 `quickstart.md` V1–V6，確認 SC↔測試對照全數成立
- [ ] T023 [P] 更新 `CLAUDE.md` 專案地圖（008b 實作狀態、`trading_costs.py` 模組、003 解除阻塞）
- [ ] T024 最終 `pytest -q` 全綠（合併關卡）；本 spec 不動訊號邏輯——若實作中觸碰訊號面，附前後回測對照

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 無相依，T001/T002 可平行
- **Foundational (Phase 2)**: 依賴 Setup；**阻塞所有 user story**（T003→T004 可平行於 T003 之後段；T005 依賴 T002）
- **US1 (Phase 3)**: 依賴 Foundational（ContractSpec/config/ABC/現股元件）
- **US2 (Phase 4)**: 依賴 US1 的引擎注入（T010）——sizing/會計疊在注入面之上
- **US3 (Phase 5)**: T019 依賴 T005；T020 依賴 US1 引擎改造完成（改造後才有 parity 可驗）；建議在 US1、US2 每個 checkpoint 都先行預跑
- **Polish (Phase 6)**: 依賴全部 story 完成

### User Story Dependencies

- **US1 (P1)** MVP：Foundational 後即可 → SC-002/006 + SC-004 初步
- **US2 (P2)**：依賴 US1 引擎注入面（例外於「stories 應獨立」原則：會計/sizing 與注入同檔 `backtester.py`，強行獨立會造成同檔兩次大改）
- **US3 (P3)**：驗證性 story，可隨時預跑；正式驗收在引擎穩定後

### Parallel Opportunities

- T001 ∥ T002；T006 ∥ T007 ∥ T008；T013 ∥ T014 ∥ T015；T019 可提前平行；T021 ∥ T022 ∥ T023

---

## Implementation Strategy

### MVP First（US1）

1. Setup（基線 + 骨架）→ Foundational（schema + 現股元件搬移）
2. US1：成本數學 + 引擎注入 + 護欄退役（常數口 e2e）
3. **STOP & VALIDATE**：SC-002/006 + 既有測試全綠（parity 預跑）
4. 最小可交付：期貨成本正確可算、護欄已退役、現貨不動

### Incremental Delivery

1. US1 → 交付（成本層）
2. US2 → 交付（sizing/會計層，期貨完整可交易回測）→ **spec 003 解除阻塞**
3. US3 → 交付（現貨零回歸正式驗收）
4. Polish → 合併

### 硬約束管理

- **SC-001 現貨位元不變**：T010（引擎注入）是唯一高風險任務——「預設 = 現股元件」+「公式逐字搬移」雙保險；注入後立即全套測試，任何紅燈先修再前進。
- 合併前 `pytest -q` 全綠；每完成一任務或邏輯群組即 commit。

---

## Notes

- [P] = 不同檔、無相依
- 引擎注入「預設 = 現股元件」（D9）：既有呼叫與測試零改動
- 期貨在本 spec 僅 long-only；做空 = spec 003
- 監控/walk-forward/optimizer 的期貨接入不在範圍（spec Assumptions）
