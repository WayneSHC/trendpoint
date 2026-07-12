# Tasks: MSS 之 FVG（公平價值缺口）確認

**Input**: Design documents from `/specs/002-fvg-confirmation/`

**Prerequisites**: plan.md、spec.md、research.md、data-model.md、contracts/、quickstart.md（全數已存在）

**Tests**: spec SC-003 明確要求看前偏誤測試，且憲法 III 要求驗收映射測試——故含測試任務。
基準重現採「先取基準、後改碼、再零差異比對」（非 TDD，而是迴歸閘門）。

**Organization**: 單一 User Story（US1，P1）。Setup 取基準與組態；US1 為 FVG 核心與驗證；Polish 收尾。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔案、無未完成依賴）
- **[Story]**: US1（唯一）

## Path Conventions

單體專案、repo 根目錄扁平佈局，依 plan.md 的 Source Code 結構。

---

## Phase 1: Setup（基準與組態）

**Purpose**: 取 FVG 前基準（迴歸閘門錨點）與新增組態參數。**基準必須在任何引擎改碼前落檔並 push**（session/scratchpad 不耐久教訓）。

- [x] T001 取 FVG 前基準：跑 `python run_backtest.py` 與 `python run_portfolio_backtest.py`，記錄五標的報酬/筆數/勝率、組合 8.22%/34、六個 `data/*_backtest_trades.csv` 的 sha256 至 `specs/002-fvg-confirmation/baseline-pre-fvg.md`。**完成即 commit + push**
- [x] T002 [P] 組態：`config/config.py` 的 `SingleStrategyParams` 新增 `use_fvg: bool = Field(default=True, ...)` 與 `fvg_lookback: int = Field(default=3, ge=1, ...)`；`config/config.yaml` 的 `strategy.default` 加 `use_fvg: true` / `fvg_lookback: 3`；若 `tests/test_config.py` 有 schema 覆蓋則同步

---

## Phase 2: User Story 1 - 降低 MSS 假訊號（Priority: P1）🎯 MVP — 核心實作

**Goal**: MSS 只在近 M 根有同向 FVG 時成立；`use_fvg=False` 精確回到 spec 001 基準。

**Independent Test**: `pytest -q tests/test_fvg_confirmation.py` 離線可跑；`use_fvg=False` 回測與 T001 基準零差異。

### Implementation for User Story 1

- [x] T003 [US1] 在 `ladder_system.py` 新增 `_detect_fvg(df, direction) -> pd.Series`（契約見 contracts/fvg-detection.md）：`up` = `df['low'] > df['high'].shift(2)`，`down` = `df['high'] < df['low'].shift(2)`；回傳 bool、前 2 根 False、純向量化
- [x] T004 [US1] 擴充 `ladder_system.py` 的 `detect_market_structure`：新增 keyword-only `use_fvg: bool = False, fvg_lookback: int = 3`；`use_fvg=True` 時以 `_detect_fvg(...).rolling(fvg_lookback).max().fillna(False).astype(bool)` 之同向遮罩閘門 `mss_signal`（真值表見 data-model.md §2）；`bos_signal` 與 `use_fvg=False` 路徑逐位元不變
- [x] T005 [US1] 擴充 `ladder_system.py` 的 `build_indicator_frame`：新增 keyword-only `use_fvg: bool = False, fvg_lookback: int = 3`，原樣轉發給 `detect_market_structure`（預設 False 確保 004 既有 parity 呼叫不變）
- [x] T006 [US1] 擴充 `backtester.py` 的 `run_backtest`：新增 `use_fvg`、`fvg_lookback` kwargs；計算 `effective_use_fvg = use_fvg and ('fvg' not in disabled_filters)`，連同 `fvg_lookback` 傳入 `build_indicator_frame`（比照 `include_regime` 模式）
- [x] T007 [US1] 穿線呼叫端：`run_backtest.py` 把 `params.use_fvg`/`params.fvg_lookback` 傳入 `run_backtest`；`run_ablation.py` 同樣穿線，並在 `ABLATION_TARGETS` 加 `("停用 FVG 確認", "fvg")`
- [x] T008 [US1] `monitor_signals.py`：`check_new_signals` 的 `build_indicator_frame` 呼叫加 `use_fvg=True, fvg_lookback=3`（即時告警亦套 FVG，research.md R6）

**Checkpoint**: FVG 核心就緒，管線全通（回測、消融、監控）

### Tests & Validation for User Story 1

- [x] T009 [P] [US1] 新增 `tests/test_fvg_confirmation.py`：(a) `_detect_fvg` 對手工三根缺口 df 的偵測正確（含前 2 根 False）；(b) mss 閘門真值表五種情形（data-model.md §2）；(c) **基準重現**——同一 df 上 `detect_market_structure(use_fvg=False)` 與現行輸出 `assert_series_equal` 零差異；(d) `bos_signal` 在 use_fvg True/False 皆不變。檔頭加 MPL-2.0
- [x] T010 [US1] `tests/test_lookahead_bias.py` 新增 FVG tail-tamper 案例：`use_fvg=True` 下跑原資料與「split 後 OHLC 加倍」資料，斷言 split 前交易逐筆相同（沿用既有 `_generate_mock_data` 與斷言模式）
- [x] T011 [US1] `tests/test_acceptance_parity.py` 加 `use_fvg=True` 參數化變體（`_PARAMS` 增加一組），確保 FVG 欄位納入前綴一致性與逐根重播（零容差）
- [x] T012 [US1] **基準重現閘門**：設 `use_fvg=False`（或用 `disabled_filters={'fvg'}`）重跑 `run_backtest.py`/`run_portfolio_backtest.py`，六個 trades CSV sha256 與 T001 逐一相同；`pytest -q` 全綠。結果填入 `baseline-pre-fvg.md`
- [x] T013 [US1] **SC-001 實跑驗證**：對五標的比較 `use_fvg` True/False 的 MSS 計數，確認每檔下降且 > 0；若某檔歸零則放寬 `config.yaml` 的 `fvg_lookback` 並記錄。數字寫入 `baseline-pre-fvg.md` 與 PR
- [x] T014 [US1] **SC-002 消融報告**：跑 `python run_ablation.py`，確認輸出含「停用 FVG 確認」列與其對報酬/筆數/EV 的 delta；擷取表格入 PR

**Checkpoint**: US1 完整——SC-001/002/003 全數有證據

---

## Phase 3: Polish & Cross-Cutting

- [x] T015 依 quickstart.md 全套驗證：三測試檔全綠、`pytest -q` 全綠、`-m "not performance"` 正常、本機 no-numba 重跑（parity 已在清單）一致
- [x] T016 [P] 文件同步：`specs/001-ladder-core/spec.md` 修訂摘要表「MSS 必須伴隨 FVG」列由「移出基準」改為指向已實作的 002；`specs/002-fvg-confirmation/spec.md` Status 由 Draft 改為 Implemented
- [ ] T017 開 PR（base: main）：說明含 T012 基準重現對照（CSV sha256 相同）、T013 MSS 計數比較、T014 消融表格、T010/T011 看前偏誤三層防線、憲法檢核聲明；CI 全綠後合併

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1（Setup）**: T001 必須最先且獨立完成並 push（基準先於改碼）；T002 可與 T001 平行（不同檔）
- **Phase 2 實作（T003–T008）**: 串行依賴——T003（FVG 偵測）→ T004（閘門）→ T005（frame 穿線）→ T006（run_backtest 穿線）→ T007（呼叫端 + 消融）；T008（monitor）可在 T005 後平行
- **Phase 2 測試（T009–T014）**: 全部依賴實作完成；T009/T010/T011 可平行（不同測試檔）；T012（基準重現）依賴 T006/T007；T013/T014 依賴全管線
- **Phase 3（Polish）**: 依賴全部完成；T016 可與 T015 平行

### Parallel Opportunities

- T001 ∥ T002（Setup）
- T008 ∥ T006/T007（monitor 與 backtester 呼叫端不同檔，皆在 T005 後）
- T009 ∥ T010 ∥ T011（三測試檔）
- T015 ∥ T016（驗證與文件）

### Parallel Example: 實作完成後

```bash
# 三測試檔同時開工：
Task A: T009 tests/test_fvg_confirmation.py
Task B: T010 tests/test_lookahead_bias.py（FVG 案例）
Task C: T011 tests/test_acceptance_parity.py（use_fvg=True 變體）
```

---

## Implementation Strategy

### MVP（US1 = 全部）

單一故事即 MVP。建議順序：Setup（T001 基準先 push）→ 實作鏈 T003–T008 →
測試 T009–T011 → **基準重現閘門 T012（全計畫風險最高點：證明 use_fvg=False
零差異）** → SC 驗證 T013/T014 → Polish。每個 checkpoint 後 commit + push
（scratchpad/session 不耐久）。

### 關鍵風險與緩解

- **SC-001 可能歸零**（M 太緊）：T013 實跑驗證，`fvg_lookback` 可調放寬（research.md R3）
- **基準重現不成立**（FVG 邏輯洩到 use_fvg=False 路徑）：T012 sha256 逐位比對即擋下；
  T009(c) 單元層先抓
- **看前偏誤**：三層防線 T010/T011 + 004 既有 parity 自動覆蓋 mss_signal

---

## Notes

- 總計 17 個任務；風險集中於 T003–T006（動 detect_market_structure）與 T012（基準重現閘門）
- T001 基準必須在任何引擎改碼前 push——順序不可換
- 新增 .py 測試檔加 MPL-2.0 檔頭
- 刻意不修 `structure_period` 硬編碼債（超範圍，research.md R5）
