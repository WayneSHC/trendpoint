---

description: "Task list for 016-intraday-evaluation-protocol"
---

# Tasks: 盤中時框評估協定（Intraday Evaluation Protocol）

**Input**: Design documents from `/specs/016-intraday-evaluation-protocol/`

**Prerequisites**: [plan.md](./plan.md)、[spec.md](./spec.md)、[research.md](./research.md)、
[data-model.md](./data-model.md)、[contracts/](./contracts/)

**Tests**: **納入（必要，非選配）**——憲章原則 III 與 SC-013 要求每條驗收標準
對應至少一個 pytest 測試，故測試任務為硬性項目。

**Organization**: 依 user story 分組，每組可獨立實作與驗證。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔案、無未完成相依）
- **[Story]**: 對應 spec.md 的 user story（US1–US4）
- 每項含確切檔案路徑

## Path Conventions

本 repo 為**扁平模組**佈局（`ladder_system.py`、`backtester.py` 等皆位於根目錄），
測試一律於 `tests/`。新增檔案清單見 plan.md 的 Project Structure。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 凍結基準與建立組態，使後續任何改動都能被證明沒有動到生產路徑

- [X] T001 凍結生產路徑基準：執行日線回測並將逐筆交易、逐根權益、summary 序列化至 `tests/fixtures/016_baseline_daily.json`，附產生指令於檔頭註解（**必須在任何程式改動之前完成**，否則 SC-011 失去比對對象）
- [X] T002 [P] 於 `config/config.py` 新增 `IntradayEvaluationConfig` Pydantic 模型：納入準則四維門檻、`lookback_days`、標籤門檻（`min_test_windows`、`min_trades_per_window`）、`scale_factors`、`excluded_tickers`，含 `model_validator` 檢核（門檻須為正、`scale_factors` 須含 1.0 且嚴格遞增）
- [X] T003 於 `config/config.yaml` 新增 `intraday_evaluation` 區塊並填入 research.md 的預設值（`lookback_days: 20`、`min_test_windows: 3`、`min_trades_per_window: 30`、`scale_factors: [0.25, 0.5, 1.0, 2.0, 4.0]`），並將其掛入 `SystemConfig`
- [X] T004 [P] 建立合成盤中資料產生器 `tests/fixtures_016_intraday.py`：可產出指定交易日數、每日根數、可注入缺口/重疊/衝突/斷裂的 OHLCV DataFrame（離線、確定性、無網路）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 快照正規化、指紋、CSV 契約與隔離護欄——所有 user story 的共同地基

**⚠️ CRITICAL**: 本階段未完成前，任何 user story 不得開工

- [X] T005 建立 `intraday_snapshot.py`（MPL-2.0 標頭）並實作 `normalize_frame()`：欄名小寫、欄序固定、價格四捨五入至 4 位小數、`volume` 轉 int64、索引排序去重，違反前置條件（負價、NaN、`high < low`、空表）時硬失敗
- [X] T006 於 `intraday_snapshot.py` 實作 `fingerprint()`：正規化後 CSV 位元組的 SHA-256，並實作 `Snapshot` 資料結構（`ticker`/`frame`/`fingerprint`/`first_ts`/`last_ts`/`bars`/`trading_days`）
- [X] T007 [P] 於 `tests/test_intraday_snapshot.py` 撰寫正規化與指紋測試：同內容不同浮點雜訊 → 同指紋；同內容不同欄序 → 同指紋；內容差一位小數 → 不同指紋；空表與負價 → 硬失敗
- [X] T008 於 `intraday_snapshot.py` 實作 canonical CSV 讀寫 `read_history()` / `write_history()`，嚴格遵循 `contracts/accumulated-history.md`（固定 4 位小數格式化、無 BOM、`\n` 行尾、無額外索引欄）
- [X] T009 [P] 於 `tests/test_intraday_snapshot.py` 撰寫 CSV 契約往返測試：`write → read → write` 的位元組完全相同；讀入非遞增索引的 CSV 時硬失敗
- [X] T010 建立 `tests/test_intraday_isolation.py` 的**靜態零引用檢查**：斷言 `monitor_signals.py`、`backtester.py`、`ladder_system.py`、`portfolio_backtester.py`、`app.py`、`alerts.py` 皆未 import 本案任一模組（`intraday_snapshot` / `intraday_universe` / `intraday_report` / `run_intraday_eval`），沿用 `tests/test_alert_outcomes.py` 的既有手法（**此測試先於實作建立，使後續任務一旦誤接線即立刻紅燈**）

**Checkpoint**: 地基就緒——快照可正規化、可指紋、可落地，且護欄已就位

---

## Phase 3: User Story 1 - 逐標的、可重現、帶邊界標示的評估報告 (Priority: P1) 🎯 MVP

**Goal**: 對一份固定快照產出逐標的報告；同一輸入必得同一輸出；每個績效數字帶效力標籤；
零交易可被分解成因；跨標的合併必帶離散度。

**Independent Test**: 對同一份 fixture 連續執行兩次逐欄比對（場景 1）；
對含 0 交易標的的 fixture 執行，確認成因分類非「原因不明」（場景 3）。
本階段不需要累積歷史與納入準則——標籤恆為 `in_sample_descriptive`，
標的清單直接由 `--state-dir` 的檔案決定。

### Tests for User Story 1 ⚠️

> 先寫測試並確認其失敗，再進實作

- [X] T011 [P] [US1] 於 `tests/test_intraday_report.py` 撰寫 `test_determinism`：同一 fixture 兩次評估，`inputs` 與 `results` 逐欄相同，`provenance` 允許不同（SC-001）
- [X] T012 [P] [US1] 於 `tests/test_intraday_report.py` 撰寫 `test_every_perf_has_label`：`performance` 每一項皆為 `{value, validity_label}` 物件，無裸數值（SC-002）
- [X] T013 [P] [US1] 於 `tests/test_intraday_report.py` 撰寫 `test_pooled_has_dispersion`：每筆 `pooled` 皆帶 `min`/`max`/`ratio`/`n_tickers`，且 `pooled_value` 不得單獨出現（SC-003）
- [X] T014 [P] [US1] 於 `tests/test_intraday_report.py` 撰寫 `test_zero_trade_cause_exhaustive`：對四種人造情境各自產生對應的互斥成因，且 `trades == 0` 時該欄不得為 `None` 或 `"unknown"`（SC-004）
- [X] T015 [P] [US1] 於 `tests/test_intraday_report.py` 撰寫 `test_no_efficacy_claims`：序列化後全文對 `contracts/evaluation-report.md` 的措辭清單命中數為 0，並涵蓋三種 `validity_label`（SC-012）
- [X] T016 [P] [US1] 於 `tests/test_intraday_report.py` 撰寫 `test_signal_density_directional`：BOS/MSS 分方向四欄齊備，且其基數與 attrition 的基數一致（FR-008）

### Implementation for User Story 1

- [X] T017 [US1] 建立 `intraday_report.py`（MPL-2.0 標頭）並實作 `build_data_health()` 與 `build_signal_density()`：沿用 `ladder_system.build_indicator_frame`，BOS/MSS **分方向**計數、regime 通過數、暖機損失與可用根數
- [X] T018 [US1] 於 `intraday_report.py` 實作 `build_attrition()`：四道濾網單道通過率與五道合取數，訊號根與判定根的配對沿用 `run_5m_evaluation.py:276` 的 `shift(-1)` 對齊（與引擎 `iloc[i-2]`/`iloc[i-1]` 逐值一致）
- [X] T019 [US1] 於 `intraday_report.py` 實作 `classify_zero_trade()`：依 data-model.md 的順序判定四個互斥成因，保證無「原因不明」
- [X] T020 [US1] 於 `intraday_report.py` 實作 `PooledStatistic` 組裝：`pooled_value` 與 `min`/`max`/`ratio`/`n_tickers` 同屬一結構，分母為 0 時 `ratio` 為 `None`
- [X] T021 [US1] 於 `intraday_report.py` 實作 `decide_validity_label()`：累積狀態的**純函式**，無窗口輸入時恆回傳 `in_sample_descriptive`；**不接受呼叫端指定標籤**（research.md R6）
- [X] T022 [US1] 於 `intraday_report.py` 實作 `build_per_ticker_result()`：組裝 `PerTickerResult`，含 `structure_period_hardcoded` 的顯式標示（FR-021）
- [X] T023 [US1] 於 `intraday_report.py` 實作 `to_json()`：三區結構（`inputs`/`results`/`provenance`）、鍵序排序、陣列依 ticker 排序、浮點固定小數位（research.md R8 的四個確定性風險點）
- [X] T024 [US1] 於 `intraday_report.py` 實作 `render_text()`：**由 JSON 結構渲染**，不得獨立計算任何數值
- [X] T025 [US1] 建立 `run_intraday_eval.py`（MPL-2.0 標頭）並實作 `evaluate` 子命令：依 `contracts/cli.md` 的參數與退出碼；CLI 只做編排，不含判定邏輯
- [X] T026 [US1] 於 `intraday_report.py` 加入措辭檢核常數（措辭清單來自 `contracts/evaluation-report.md`），供 T015 的測試與人工檢視共用單一來源

**Checkpoint**: US1 可獨立運作——給一份 fixture 即可產出確定性、帶標籤、可解釋零交易的報告

---

## Phase 4: User Story 2 - 前置且客觀的標的納入準則 (Priority: P2)

**Goal**: 標的由事前、客觀、可版本化的準則決定；排除理由可追溯；
準則對評估結果的擾動不敏感。

**Independent Test**: 人為改變任一標的的回測輸出，`included` 清單不變（場景 4）；
每個被排除標的皆列出 `failed_criteria` 與 `measured`。

### Tests for User Story 2 ⚠️

- [X] T027 [P] [US2] 於 `tests/test_intraday_universe.py` 撰寫 `test_perturbation_insensitive`：monkeypatch 回測輸出後重跑，`included` 清單與 `measured` 皆不變（SC-005）
- [X] T028 [P] [US2] 於 `tests/test_intraday_universe.py` 撰寫 `test_exclusion_traceable`：每個 `included=false` 的標的皆有非空 `failed_criteria`，且每一項對應到組態中的具體門檻鍵（SC-006）
- [X] T029 [P] [US2] 於 `tests/test_intraday_universe.py` 撰寫 `test_lookback_disjoint_from_eval_window`：準則計算所讀取的時間範圍與評估窗**無交集**（research.md R5 的看前偏誤面）
- [X] T030 [P] [US2] 於 `tests/test_intraday_universe.py` 撰寫 `test_criteria_version_recorded`：門檻改變後報告的 `criteria_version` 隨之改變（FR-012）

### Implementation for User Story 2

- [X] T031 [US2] 建立 `intraday_universe.py`（MPL-2.0 標頭）並實作四個維度的量測純函式：日均量、盤中缺口比率、每日根數變異係數、價格檔位粒度——輸入**僅限** lookback 期間的 OHLCV
- [X] T032 [US2] 於 `intraday_universe.py` 實作 `split_lookback_and_eval()`：由累積歷史切出「評估窗之前」的 lookback 與其後的評估窗，兩者不重疊；lookback 不足時該標的以 `insufficient_lookback` 排除
- [X] T033 [US2] 於 `intraday_universe.py` 實作 `apply_criteria()` → `UniverseDecision`：含 `included`、`failed_criteria`、`measured`，並套用 `excluded_tickers` 顯式排除清單（槓桿/反向 ETF）
- [X] T034 [US2] 於 `intraday_universe.py` 實作 `criteria_version()`：由門檻值集合導出穩定識別字串，門檻改變即改版
- [X] T035 [US2] 於 `intraday_report.py` 將 `results.universe` 接線（`included` 清單 + 逐標的 `decisions`），並使 `evaluate` 在無標的通過時以退出碼 1 明確失敗而非產出空報告
- [X] T036 [US2] 於 `run_intraday_eval.py` 實作 `universe` 子命令（除錯用，輸出逐標的判定與實測值）

**Checkpoint**: US1 與 US2 皆可獨立運作——報告的標的清單此時由準則而非人工挑選決定

---

## Phase 5: User Story 3 - 歷史累積以解鎖樣本外切分 (Priority: P3)

**Goal**: 跨執行累積歷史，合併無重複無倒錯、衝突有記錄、斷裂會回報；
累積足夠時切出互不重疊且不跨斷裂的樣本外窗口。

**Independent Test**: 以合成多期快照模擬累積（場景 5、6），全程離線。

### Tests for User Story 3 ⚠️

- [X] T037 [P] [US3] 於 `tests/test_intraday_snapshot.py` 撰寫 `test_merge_no_dup_no_disorder`：兩份重疊快照合併後重複列 0、時序倒錯 0（SC-007）
- [X] T038 [P] [US3] 於 `tests/test_intraday_snapshot.py` 撰寫 `test_merge_first_writer_wins`：重疊處保留既有值、新值被捨棄，`conflicts` 計數與衝突時間範圍正確（FR-014、research.md R3）
- [X] T039 [P] [US3] 於 `tests/test_intraday_snapshot.py` 撰寫 `test_chain_break_reported`：前次累積取不回時 `chain_broken=true`、插入 `kind="chain_restart"` 的 Gap、`chain_origin` 重設，且該事實出現於報告 `inputs`（SC-009）
- [X] T040 [P] [US3] 於 `tests/test_intraday_snapshot.py` 撰寫 `test_window_splits_disjoint`：測試窗兩兩不重疊、不跨越任何非 `weekend_or_holiday` 的 Gap、`train_end < test_start`（SC-008 後半）
- [X] T041 [P] [US3] 於 `tests/test_intraday_snapshot.py` 撰寫 `test_window_insufficient_reports_shortfall`：長度不足時 `splits == []`、`sufficient == false`、`shortfall_trading_days > 0`，且**不得回傳部分切分**（SC-008 前半、FR-015）
- [X] T042 [P] [US3] 於 `tests/test_intraday_snapshot.py` 撰寫 `test_gap_kind_enum`：Gap 的 `kind` 僅取三個列舉值，下游判斷不依賴人類可讀字串

### Implementation for User Story 3

- [X] T043 [US3] 於 `intraday_snapshot.py` 實作 `merge_history()` → `(frame, MergeEvent)`：時間戳外連接、**先到者為準**、逐欄比較計數衝突、後置條件檢查（索引嚴格遞增）
- [X] T044 [US3] 於 `intraday_snapshot.py` 實作 `detect_gaps()`：以中位數日內根距與每日根數為基準判定斷裂，輸出含 `kind` 列舉的 `Gap` 清單
- [X] T045 [US3] 於 `intraday_snapshot.py` 實作 `chain_state.json` 的讀寫，綱要嚴格遵循 `contracts/accumulated-history.md`（含 `chain_origin`、`chain_broken`、逐標的 `merge_events` 與 `gaps`）
- [X] T046 [US3] 於 `intraday_snapshot.py` 實作 `split_windows()`：gap-aware 純函式，只回傳窗口邊界，**不執行任何回測或尋優**（research.md R4）；不足時回傳空列表加量化差距
- [X] T047 [US3] 於 `intraday_report.py` 接線窗口切分與標籤升級：`decide_validity_label()` 依窗數與逐窗樣本量回傳三態之一，並將 `results.windows` 寫入報告
- [X] T048 [US3] 於 `run_intraday_eval.py` 實作 `accumulate` 子命令：取數 → 正規化 → 合併 → 寫回 `--state-dir`；支援 `--offline-csv-dir` 供離線測試；一檔失敗不影響其他檔
- [X] T049 [US3] 建立 `.github/workflows/intraday_accumulate.yml`：**每週一次** cron（FR-022）、`gh run list` 找最近一次成功 run → `gh run download` 取回前次 artifact（research.md R2）、取不到時標記鏈結起點而非失敗、上傳 artifact 並設 `retention-days: 90`
- [X] T050 [US3] 改寫 `.github/workflows/probe_yfinance_5m.yml`：移除其自有的 CSV 快照上傳路徑，改為呼叫同一套累積機制（FR-024），使快照產生方式收斂為單一路徑

**Checkpoint**: 三個 story 皆可獨立運作——累積鏈就位，樣本外切分在長度足夠時自動生效

---

## Phase 6: User Story 4 - 參數時框語意：待驗證問題而非既定需求 (Priority: P4)

**Goal**: 以尺度掃描**量測**參數時框語意是否構成瓶頸；
使既定處方改為由量測驅動。

**Independent Test**: 對固定 fixture 執行掃描，輸出完整反應曲線（場景 7）；
曲線平坦即為「尺度不是瓶頸」的可讀證據。

### Tests for User Story 4 ⚠️

- [X] T051 [P] [US4] 於 `tests/test_intraday_report.py` 撰寫 `test_scale_sweep_curve`：對每個 `scale_factor` 皆輸出單道通過率、合取數、交易數，且 `factor=1.0` 的結果與未掃描時的主結果一致
- [X] T052 [P] [US4] 於 `tests/test_intraday_report.py` 撰寫 `test_verdict_requires_measurement`：未提供掃描結果時，判讀輸出**不得**包含「參數時框化」類既定處方（FR-018）
- [X] T053 [P] [US4] 於 `tests/test_intraday_report.py` 撰寫 `test_scale_sweep_no_config_write`：掃描前後 `config/config.yaml` 的位元組完全相同（記憶體內覆寫，沿用 `run_b_segment.py` 慣例）

### Implementation for User Story 4

- [X] T054 [US4] 於 `intraday_report.py` 實作 `run_scale_sweep()`：對週期類參數施加**倍率**（非絕對值），逐倍率輸出單道通過率、合取數、完成來回交易數；覆寫全在記憶體內
- [X] T055 [US4] 於 `run_intraday_eval.py` 接線 `--scale-sweep` 旗標，並將結果寫入 `results.scale_sweep`
- [X] T056 [US4] 改寫 `run_5m_evaluation.py::verdict`（現行 `run_5m_evaluation.py:399-406`）：移除交易數不足時無條件輸出的「先做參數時框化」處方，改為僅在掃描結果顯示某參數為瓶頸時才指出該參數與其影響幅度；無掃描結果時只陳述樣本量事實

**Checkpoint**: 四個 story 全部可獨立驗證

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T057 於 `tests/test_intraday_isolation.py` 補上**逐欄基準對照**：以 T001 凍結的 fixture 比對日線回測的逐筆交易、逐根權益、summary，任一欄不同即失敗（SC-011）
- [X] T058 [P] 依 `.claude/docs/maintenance-protocol.md` 更新 `CLAUDE.md` 專案地圖：新增 spec 016 條目，如實記錄「協定已實作、尚未累積出樣本外結論」，**不得**寫成已驗證盤中訊號
- [X] T059 [P] 於 `docs/reviews/2026-08-06-intraday-timeframe-feasibility.md` 加註：其 8 檔結論係事後挑選標的所產生，已由本協定取代，僅作為方法論反例保留（spec.md Assumptions）
- [X] T060 執行 quickstart.md 全部 9 個自動化場景，逐項確認預期輸出
- [ ] T061 **[MANUAL，未執行]** 場景 10：手動觸發 `intraday_accumulate.yml` 兩次，驗證 `chain_broken` 由 `true` 轉 `false`、`bars_added > 0`、`chain_origin` 沿用、`actual_span` 為實得期間。**本項無法自本機完成**——需真實 runner 與跨 run 的 artifact 傳遞；首次排程（每週六 01:00 UTC）或手動觸發後才驗得到
- [X] T062 `pytest -q` 全綠，並確認 SC-001~SC-013 的對照表（quickstart.md 末表）每列皆有實際存在的測試

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 無相依，可立即開始。**T001 必須最先完成**——基準若在改動後才凍結，SC-011 就變成自我證成
- **Foundational (Phase 2)**: 依賴 Setup；**阻擋所有 user story**
- **US1 (Phase 3)**: 依賴 Foundational
- **US2 (Phase 4)**: 依賴 Foundational；T035 需 US1 的 `intraday_report.py` 已存在
- **US3 (Phase 5)**: 依賴 Foundational；T047 需 US1 的 `decide_validity_label()` 已存在
- **US4 (Phase 6)**: 依賴 Foundational；T054 需 US1 的 `build_attrition()` 已存在
- **Polish (Phase 7)**: 依賴所有欲交付的 story

### User Story Dependencies

- **US1 (P1)**: Foundational 後即可開始，無跨 story 相依 —— **MVP**
- **US2 (P2)**: 邏輯上獨立（`intraday_universe.py` 可單獨測試），僅在接線點 T035 觸及 US1 的檔案
- **US3 (P3)**: 邏輯上獨立（合併與切分皆為 `intraday_snapshot.py` 的純函式），僅在接線點 T047 觸及 US1
- **US4 (P4)**: 需 US1 的 attrition 計算作為掃描的被測對象；此相依是實質的，不建議跳過 US1 直接做 US4

### Within Each User Story

- 測試先寫並確認失敗，再進實作
- 純函式先於接線；接線先於 CLI 子命令；CLI 先於 workflow

### Parallel Opportunities

- T002 與 T004 可與 T001 之後平行
- Phase 2 的 T007、T009 可平行（不同測試函式、不同關注點）
- 各 story 的測試任務（T011–T016、T027–T030、T037–T042、T051–T053）組內皆可平行
- Foundational 完成後，US1／US2／US3 的**純函式部分**可由不同人平行推進，
  接線點（T035、T047）需排在 US1 之後

---

## Parallel Example: User Story 1

```bash
# 先平行寫完 US1 的六個測試（同檔不同函式，可分工但需協調 import 區）：
Task: "test_determinism in tests/test_intraday_report.py"
Task: "test_every_perf_has_label in tests/test_intraday_report.py"
Task: "test_pooled_has_dispersion in tests/test_intraday_report.py"
Task: "test_zero_trade_cause_exhaustive in tests/test_intraday_report.py"
Task: "test_no_efficacy_claims in tests/test_intraday_report.py"
Task: "test_signal_density_directional in tests/test_intraday_report.py"

# 確認全部失敗後，實作依序推進（同檔，不可平行）：
T017 → T018 → T019 → T020 → T021 → T022 → T023 → T024 → T025 → T026
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup（T001 最先）
2. Phase 2 Foundational（阻擋性）
3. Phase 3 US1
4. **停下驗證**：場景 1、2、3、9 應全數通過
5. 此時已修掉原探查的缺陷 2（統計 pooled）與缺陷 4（不可重現）——
   報告可被引用，這本身就是可交付的價值

### Incremental Delivery

1. Setup + Foundational → 地基
2. + US1 → 報告可引用（**MVP**）
3. + US2 → 標的不再是事後挑選（修缺陷 3）
4. + US3 → 累積鏈啟動；**此後每週自動增長**，樣本外結論是時間的函數
5. + US4 → 參數尺度假設被量測，可能的產出是**刪除**該假設

### 時序上的現實

US3 完成當天不會產生任何樣本外結論——累積需要數月。故**越早完成 US3 越好**
（累積的起算點就是它上線的日子），但 US1/US2 必須先行，否則期間產出的
報告仍然不可引用。這是本規格唯一一處優先序與時間效益不完全一致的地方。

---

## Notes

- `[P]` = 不同檔案、無相依
- 每完成一項或一個邏輯群組即提交
- **不得**在任何階段讓生產路徑 import 本案模組——T010 的護欄從 Phase 2 起持續生效
- **不得**在無量測支持時輸出既定處方（FR-018）——這是 US4 存在的理由
- 憲章原則 III：本檔每一項測試任務皆對應 spec.md 的具體 SC，
  對照表見 quickstart.md 末段
