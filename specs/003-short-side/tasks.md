---
description: "Task list for 003 — 台指期做空（Short Side, Futures-Only）"
---

# Tasks: 台指期做空（Short Side, Futures-Only）

**Input**: Design documents from `specs/003-short-side/`

**Prerequisites**: plan.md、spec.md、research.md（D1-D7）、data-model.md、contracts/、quickstart.md

**Tests**: 憲章 III 與 spec FR-011 要求測試 → 生成測試任務（TDD：先寫失敗測試再實作）。

**Organization**: Foundational 承載對稱化原語（config 旗標 + ladder_system 三處擴充，
全部 back-compat：新參數預設 = 現行為）；US1（空方訊號與可交易回測，含鏡像對稱與
lookahead）、US2（零回歸雙保證）、US3（裁決 + 推播能力）。
**零回歸為橫向硬約束**：每個 checkpoint 既有測試全綠 + 基準數字位元不變。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [x] T001 [P] 確認回歸基線：`pytest -q` 全綠（133）+ `python run_backtest.py` 現貨 5 檔
  與期貨 long-only（TXF 46.74%/4 筆、MTX 9.16%/1 筆）數字與 quickstart V1 表逐位元相同，
  記錄至 `specs/003-short-side/baseline-check.md`（SC-003 對照錨點）

---

## Phase 2: Foundational（阻塞性前置——對稱化原語，全部 back-compat）

**⚠️ CRITICAL**: 新參數預設值 = 現行為；本階段完成後全套既有測試必須仍綠。

- [x] T002 `config/config.py`：`SingleStrategyParams.enable_short: bool = False`（Field，
  期貨限定語意入 description）+ SystemConfig model validator——`ticker_overrides` 中
  **現貨** ticker 明設 `enable_short: true` → ValueError fail-fast（`default.enable_short`
  不受限；契約見 contracts D6 段）
- [x] T003 `ladder_system.py`：`check_entry_signal` 增 `direction: int = 1` 參數——
  direction=1 四維度**逐字現行為**；direction=-1 鏡像（結構==−1、收陰 close<open、
  價<當日開盤【非日線再 AND <VWAP】、振幅同式）；`disabled_filters` 語意不變
- [x] T004 `ladder_system.py`：`manage_position` 增可選參數 `chandelier_short: float = None`
  + `direction == -1` 分支——止損 `close >= stop_loss`、階段 1 目標 `entry − 1.5×ATR`
  （達標 STAGE1_HALF + 止損移保本）、階段 2 吊燈只降不升（`chandelier_short < stop_loss`
  時下移、`close > stop_loss` → CHANDELIER）、時間止盈同；direction=-1 且 stage==2 而
  chandelier_short is None → ValueError；**direction==1 分支逐字不動**
- [x] T005 `ladder_system.py`：空方市況濾網——`calculate_regime_filter` 支援方向（ADX/ER
  分量共用、MA 分量鏡像 價<長均線），`build_indicator_frame` include_regime 時增產
  `regime_ok_short` 欄位；`regime_ok`（多方）**逐字不動**；消融 'regime' 語意同多方

**Checkpoint**: `pytest -q` 全綠（原語就緒、行為零變）。

---

## Phase 3: User Story 1 — 空方訊號與可交易回測（Priority: P1）🎯 MVP

**Goal**: 空頭 BOS 續勢 + 看跌 MSS 反轉（007 短腿解封）進場、空方部位管理、
方向因子會計、空方爆倉；鏡像對稱與 lookahead 防線成立。

**Independent Test**: `pytest tests/test_short_side.py tests/test_short_futures_e2e.py
tests/test_lookahead_bias.py` 綠。

### Tests for User Story 1 ⚠️（先寫，先 FAIL）

- [x] T006 [P] [US1] `tests/test_short_side.py`：原語單元——check_entry_signal
  direction=-1 各維度鏡像真值表 + direction=1 與現行輸出位元 parity；manage_position
  空方手工情境對（止損上穿、階段 1 目標達成、吊燈只降不升、chandelier_short 缺失
  fail-fast）（SC-002b 部分）
- [x] T007 [P] [US1] `tests/test_short_side.py`：數值鏡像變換全鏈測試（SC-002a，
  analyze H1 修訂）——`make_klines` 翻轉（p'=2C−p、high↔low 對調、量能不變；映射
  見 data-model.md），**兩側皆注入常數 1 口 sizer**（隔離價格水位效應：保證金口數
  與稅額依價格而變、翻轉後不對應），斷言原序列多方與翻轉序列空方（enable_short +
  mss_reversal_entry 兩邊同參數）之進出場**根位相同、事件類型鏡像**
  （BUY↔SELL_SHORT、SELL_HALF↔COVER_HALF、SELL_ALL↔COVER_ALL）
- [x] T008 [P] [US1] `tests/test_short_futures_e2e.py`：空方 e2e——fixture 用
  **翻轉之 make_klines 序列**（analyze M2：對稱成立則必觸發空方進場，自足且確定）+
  enable_short + margin sizer → ≥1 筆 SELL_SHORT→COVER_ALL、成本非零兩邊、口數非負
  整數、無借券欄位、確定性（SC-001）；空方爆倉——進場後嫁接 +10%/根急漲 → 權益 ≤ 0
  當根強制回補、曲線截止、summary 標記（SC-006，鏡像 008b 爆倉 fixture 手法）
- [x] T009 [P] [US1] `tests/test_lookahead_bias.py` 擴充：空方防線——截斷不變性（進場
  根後截斷不改變 SELL_SHORT 時間/口數/價格）、成交 = N+1 開盤 − 滑價 tick（賣出開倉
  不利向下）、sizing 用訊號根收盤權益（SC-005）

### Implementation for User Story 1

- [x] T010 [US1] `backtester.py` 空方進場分支：flat 時三關價裁決（close>mid → 既有多方
  分支**逐字不動**；close<mid 且 `enable_short and is_futures` → 空方：BOS==−1 續勢
  【global = close<mid AND regime_ok_short】→ 未進場則 MSS==−1 反轉【AND
  mss_reversal_entry；鏡像 profile：放寬 trend、global=close<mid、免 regime】）；
  SELL_SHORT 成交 `cost_model.slip(open,"sell")`、sizing 同 008b、pm.direction=-1、
  止損 = entry + 2×ATR、SELL_SHORT 紀錄（含 point_value/sizing_price/margin_used）；
  順手更新 backtester.py:227 之 BLOCKED-003 註解（analyze L1）
- [x] T011 [US1] `backtester.py` 方向因子會計：持倉管理呼叫傳 chandelier_short；
  COVER_HALF（`sizer.partial_units` floor、0 口跳過但保本照移）/COVER_ALL 動作
  （回補 slip(open,"buy") 不利向上）；realized/unrealized = d×units×Δ×pv；爆倉檢查
  方向化（空方上漲觸發、強制回補 COVER_ALL）；`_calculate_metrics` 增空方配對
  （SELL_SHORT→COVER_ALL + COVER_HALF 中途，profit 含方向因子），**多方配對段逐字不動**
- [x] T012 [US1] `run_backtest.py`：`params.enable_short` 穿線至 `engine.run_backtest`
  （config 預設 false → 行為不變；期貨開啟示例入 config.yaml 註解）

**Checkpoint**: US1 完成 → SC-001/002/005/006 + SC-007（007 短腿實際可觸發）。

---

## Phase 4: User Story 2 — 零回歸雙保證（Priority: P2）

**Goal**: 預設 config 下現貨全套 + 008b 期貨 long-only 位元不變。

**Independent Test**: 全套 pytest + 基準數字對照。

- [x] T013 [US2] 回歸驗證：全套 `pytest -q` 綠 + `python run_backtest.py` 現貨 5 檔與
  期貨 long-only 2 檔數字與 `baseline-check.md`（T001）逐位元相同（**SC-003 硬關卡**）；
  任何 diff → 修正至歸零

**Checkpoint**: US2 完成 → SC-003。

---

## Phase 5: User Story 3 — 多空裁決與推播能力（Priority: P3）

**Goal**: 同根多空互斥裁決驗證、現貨硬邊界、monitor 期貨迭代 + mock 標示。

**Independent Test**: `pytest tests/test_short_side.py tests/test_monitor_short.py` 綠。

### Tests for User Story 3 ⚠️（先寫，先 FAIL）

- [ ] T014 [P] [US3] `tests/test_short_side.py` 擴充：裁決與硬邊界——同根多空訊號 →
  三關價唯一方向（不同時進場）；現貨 ticker override enable_short=true → config 載入
  ValueError；equity 回測任何旗標組合零空單（引擎閘門）（SC-004）
- [ ] T015 [P] [US3] `tests/test_monitor_short.py`：推播 dry-run——mock 期貨空方訊號 →
  檢測 → 訊息含方向與【MOCK 資料—dry-run】前綴 → Mock 通知端送達；去重行為不變
  （SC-008）

### Implementation for User Story 3

- [ ] T016 [US3] `monitor_signals.py`：標的迭代改 `InstrumentRegistry.from_config(...)`
  全 instrument（equity 行為不變；futures 走 `get_adapter(source).fetch` 或 `fut_*` 表）；
  instrument.source == "mock" 時訊息前綴標示；空頭訊號文案復用既有（:140-156）

**Checkpoint**: US3 完成 → SC-004/008。

---

## Phase 6: Polish & Cross-Cutting

- [ ] T017 [P] 007 短腿解封文件：`specs/007-mss-entry-distinction/spec.md` 之
  BLOCKED-003 註記移除/更新（指向 003 已實作）；`specs/003-short-side/spec.md`
  Status → Implemented（SC-007 文件面）
- [ ] T018 [P] 更新 `CLAUDE.md` 專案地圖（003 實作狀態、期貨多空、007 短腿解封）
- [ ] T019 [P] 執行 `quickstart.md` V1–V6 全數確認（含 `monitor_signals.py --once` 目檢）
- [ ] T020 最終 `pytest -q` 全綠（合併關卡）；多方訊號邏輯零觸碰（V1 位元對照為證），
  免前後回測對照義務

---

## Dependencies & Execution Order

- **Setup → Foundational → US1 → US2 → US3 → Polish**；US2 為驗證性 story
  （依賴 US1 引擎改造完成）；US3 之 T014 依賴 T002/T010、T015/T016 與 US1 平行可做
  （monitor 不依賴引擎會計）
- Foundational 內 T002 ∥ T003 ∥ T004 ∥ T005（不同區塊）；T006-T009 四測試檔可平行；
  T014 ∥ T015；T017 ∥ T018 ∥ T019

## Implementation Strategy

1. **MVP = US1**：Foundational 原語 → 空方測試先行 → 引擎分支+會計 → e2e 綠。
2. 每 checkpoint 跑全套 pytest（零回歸橫向約束——所有新參數預設=現行為，
   紅燈即結構性錯誤，先修再前進）。
3. 高風險點 = T010/T011（backtester 同檔兩任務）：多方路徑逐字不動為鐵則，
   空方為**新增分支**而非改寫；metrics 多方配對段不動、空方配對為新增段。
4. 每任務或邏輯群組 commit（分支已 push 追蹤）。

## Notes

- 訊號偵測層（detect_market_structure）與 008b 成本/sizing 元件零改動
- 組合路徑護欄不動（期貨組合仍拒；空方僅單標的）
- 反手、真期貨資料源不在範圍
