---

description: "Task list for 014 — 均線觸價通知（月／季／半年／年線）"
---

# Tasks: 均線觸價通知（月／季／半年／年線）

**Input**: Design documents from `specs/014-ma-touch-alerts/`

**Prerequisites**: [plan.md](plan.md)、[spec.md](spec.md)、[research.md](research.md)、
[data-model.md](data-model.md)、[contracts/ma-alerts.md](contracts/ma-alerts.md)、
[quickstart.md](quickstart.md)

**Tests**: **必要，非選配**。憲章原則 III 要求每條驗收標準對應至少一個 pytest 測試。
採**先紅後綠**（spec 010～013 既有實踐）。

**Organization**: 依 user story 分組。US1（穿越通知）與 US2（既有告警不變）
同為 P1；US2 的 T002 須最先執行——「實作前」的既有告警產出一旦改碼即無從比對。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔案、無未完成依賴）
- **[Story]**: US1 / US2 / US3 / US4；Setup、Foundational、Polish 階段無標籤
- **[A] / [B]**: 驗收環境切分（plan.md §驗收環境切分）
  - **[A]** 離線可完成且可驗收——合成資料即足，CI 可跑
  - **[B]** 需真實市場資料（`trendpoint.db`），**必須在本機執行**。本案僅 T028 一項

## Path Conventions

單一 Python 專案，扁平結構，repo root 即工作目錄。路徑含中文與空格，
**Bash 中一律雙引號**（CLAUDE.md 鐵律 1）。

## ⚠️ 本案三個最容易誤實作的點（實作時逐條核對）

1. **把既有 5 分線取數改成日線**（FR-008）。新增的日線讀取必須是**額外一段**，
   不得取代 `monitor_signals.py:167`。守門任務：T009。
2. **沿用 `min_periods=1`**（FR-006）。`ladder_system.py:463` 有此寫法但語意相反
   （為回測暖機期而設），照抄會由 30 根日線算出假年線。守門任務：T004。
3. **穿越前值取錯**（FR-004）。須取同一條 5 分線序列的前一根已收盤棒，
   非「前一日日線收盤」——後者會漏判開盤跳空跌破。守門任務：T014。

---

## Phase 1: Setup（合成資料與既有告警基準）

**Purpose**: 備妥可精確控制觸發條件的合成資料，並凍結既有六種告警的產出。

- [X] T001 [A] 在 `tests/ma_fixtures.py` 建立本案合成資料產生器（沿用 repo 既有的 `tests/acceptance_fixtures.py` 命名慣例，不另開子目錄）（固定 seed），提供：(a) 足夠長的日線序列（≥ 300 根，供四條線皆可計算）、(b) 僅 100 根的日線序列（供資料不足測試）、(c) 5 分線序列且可指定其收盤價相對指定均線值的位置（自上方跌破／持續低於／同日反覆穿越三種型態）
- [X] T002 [A] 以 T001 的 5 分線 fixture 在**未改碼**狀態下執行 `check_new_signals`，將既有六種告警的產出集合（alert_type、bar_time、訊息內容）存為入版控的測試期望檔（`tests/fixtures_014_baseline_alerts.json`），檔頭註記 commit SHA — 此為 SC-001 的比對來源

**Checkpoint**: 合成資料可精確控制觸發、既有告警行為已凍結

---

## Phase 2: Foundational（`ma_lines.py` 純元件與參數層）—— 阻塞所有 user story

**Purpose**: 產出兩個可獨立單元測試的純函式與參數管道。

**⚠️ CRITICAL**: 本階段未完成前，任何 user story 都無法開始

- [X] T003 [P] [A] 在 `tests/test_ma_lines.py` 新增 `compute_ma_set` 契約測試（**先紅**）：(a) 均線值＝最後 period 根收盤價算術平均，與手算一致（誤差 0）、(b) `len < period` 之線回傳 **`None`**（非 NaN、非 0）、(c) 單一條線資料不足不影響其他線、(d) 不就地修改輸入 — 對應 contracts §1、SC-005/006
- [X] T004 [A] 建立 `ma_lines.py` 實作 `compute_ma_set(daily_close, periods)`（T003 轉綠）。**檔頭加 MPL-2.0 標頭**（CLAUDE.md 授權節）。**嚴禁 `min_periods=1`**——須在函式 docstring 寫明「`ladder_system.py:463` 的 `min_periods=1` 是為回測暖機期而設，語意相反，不得沿用；用於通知會由不足資料算出假均線」。模組不得 import `monitor_signals` / `backtester` / `ladder_system` — FR-006
- [X] T005 [P] [A] 在 `tests/test_ma_lines.py` 新增 `detect_cross_below` 契約測試（**先紅**）：(a) `prev > ma and curr <= ma` 成立時回傳該線、(b) `curr == ma`（觸及）亦成立（對應「達到或低於」）、(c) 持續低於（`prev <= ma`）不成立、(d) `ma` 為 `None` 之線被略過且不拋錯、(e) 回傳可為空 list — 對應 contracts §2、SC-002/003
- [X] T006 [A] 在 `ma_lines.py` 實作 `detect_cross_below(prev_price, curr_price, ma_set)`（T005 轉綠）；docstring 須寫明「僅偵測向下穿越；向上突破不在本案範圍」— FR-004
- [X] T007 [P] [A] 在 `config/config.py` 新增 `MaLineConfig`（`enabled: bool`、`period: int = Field(ge=2)`）與 `MaAlertConfig`（`ma_alerts_enabled: bool = False` 總開關 + 四條線），並在 `SystemConfig` 新增 `alerts: MaAlertConfig` 欄位（與 `data`/`strategy`/`trading_cost`/`portfolio` 並列，`config/config.py:291-300`）。**不得**放進 `SingleStrategyParams`（理由見 research.md D3）— FR-011
- [X] T008 [P] [A] 在 `config/config.yaml` 新增 `alerts` 區塊（`ma_alerts_enabled: false`；四條線 enabled 皆 true、週期 20/60/120/240），加註解說明「總開關預設關閉；這些是通知偏好、非策略參數，故不進 strategy 區塊」

**Checkpoint**: 兩個純函式可獨立測試通過、參數可讀

---

## Phase 3: User Story 2 - 基準不被污染 (P1，**先於 US1 完成接線守門**)

**Goal**: 保證既有六種告警的行為與資料路徑完全不受影響。

**Independent Test**: 總開關關閉時，六種告警的產出集合與 T002 期望檔完全相同。

> 本階段置於 US1 之前，是因為 T009 的接線位置決定了 US1 能否安全實作——
> 先把「不能碰哪裡」釘死，再往上加功能。

- [X] T009 [US2] [A] 在 `monitor_signals.check_new_signals` 的**現貨分支尾端**（既有六種告警之後）新增均線判定區塊的骨架：總開關關閉時**直接 return / 短路，不讀日線表**。**禁止**修改 `monitor_signals.py:167` 的 5 分線 fetch、**禁止**讓均線判定共用 `build_indicator_frame` 的輸出（那來自 5 分線，算不出月線以上任何一條）— FR-008，contracts §3
- [X] T010 [US2] [A] 在 `tests/test_ma_alerts.py` 新增 SC-001 **三層**回歸測試：(a) 總開關關閉時六種告警產出集合與 T002 期望檔逐則相同、(b) 關閉時**日線表讀取次數為 0**（以 mock 或計數器驗證真正短路，而非「讀了但沒用」）、(c) 總開關開啟時六種告警產出集合**仍與關閉時相同**（只多出均線通知）
- [X] T011 [P] [US2] [A] 新增 SC-007 測試：總開關關閉→完全不發；總開關開啟但單線關閉→該線不發、其餘線正常

**Checkpoint**: 既有告警的不變性已由 CI 常態守門，可安全往上加功能

---

## Phase 4: User Story 1 - 跌破關鍵均線時收到通知 (P1)

**Goal**: 股價跌破月／季／半年／年線時收到推播，且不因持續低於而重複打擾。

**Independent Test**: 以合成資料構造自均線上方跌破，確認發出且僅發出一則對應通知。

- [X] T012 [US1] [A] 在 `monitor_signals.py` 的均線判定區塊實作日線讀取：以 `safe_load_db_data(DB_PATH, table_name_for(instrument, "daily"))` 取現貨日線；**僅使用已收盤日線**（若含當日進行中的列須排除）— FR-002
- [X] T013 [US1] [A] 接上 `compute_ma_set`（週期與開關取自 `cfg.alerts`），對 `enabled` 為 false 的線不計算不判定 — FR-001/FR-007
- [X] T014 [US1] [A] 接上 `detect_cross_below`，**前值與現值取自既有 `select_closed_bar_indices` 的 `prev_bar['close']` / `latest_bar['close']`**（`monitor_signals.py:94-106`，與既有三關價判定同源 `:230`）。**禁止**以「前一日日線收盤」作為前值——會漏判開盤跳空跌破。此處須加註解寫明理由 — FR-003/FR-004，research.md D2
- [X] T015 [US1] [A] 實作推播與去重：`alert_type` 依線別命名（`MA_CROSS_BELOW_MONTHLY` 等，見 data-model.md §3），**`bar_time` 填交易日（`latest_time.date()`）而非 5 分線時間戳**。此粒度與既有六種告警不同，須加註解寫明理由（均線在同一交易日內為常數，同日多次穿越指的是同一件事），避免被後人「統一」掉 — FR-005，research.md D5
- [X] T016 [US1] [A] 訊息內容含標的、線別、均線值、當前價、乖離幅度、時間；沿用既有 `intraday_note` 盤中註記 — FR-009
- [X] T017 [US1] [A] 期貨標的完全不進入均線判定（沿用既有 `is_futures` 分支）；日線表不存在或為空時跳過該標的並輸出可辨識提示，**不得拋錯中斷其他標的的監控** — FR-010/FR-012
- [X] T018 [P] [US1] [A] 新增 SC-002 測試：構造價格依序穿過四條線的序列，四條線各發出且僅發出一則
- [X] T019 [P] [US1] [A] 新增 SC-003 測試：穿越後連續多根皆低於均線，**僅第一次發出**，後續不再發送
- [X] T020 [P] [US1] [A] 新增 SC-004 測試：同一交易日內於均線附近反覆上下穿越，該標的該線當日**至多一則**
- [X] T021 [P] [US1] [A] 新增 SC-008/SC-009 測試：訊息含 FR-009 全部欄位且線別可辨識；日線表缺失時該標的被跳過而**其他標的的監控照常完成**

**Checkpoint**: 推播功能完整可用（總開關仍預設關閉）

---

## Phase 5: User Story 3 - 資料不足時不誤報 (P2)

**Goal**: 日線不足的標的不發出該條線的通知，而非用不足資料算出假均線。

**Independent Test**: 以僅 100 根日線的標的執行，月線與季線正常、半年線與年線不發。

> 純函式層的資料不足處理已由 T003/T004 完成，本階段驗證其在 monitor 端的端到端行為。

- [X] T022 [P] [US3] [A] 新增 SC-005 端到端測試：以 T001 的 100 根日線 fixture 執行 `check_new_signals`，斷言月線與季線可正常判定與發送、**半年線與年線不發出任何通知**，且該標的其他線不受影響

---

## Phase 6: User Story 4 - 儀表板現況面板 (P3)

**Goal**: 隨時可查目前相對四條均線的位置，補上穿越語意的盲點。

**Independent Test**: 對目前已在年線下方且近期無穿越的標的，儀表板正確顯示
「低於年線」與乖離，而推播不發出訊息。

> 本階段雖列 P3，但它是「只做穿越、不做狀態播報」這個決策**得以成立的前提**——
> 少了它，「沒收到通知」會被誤讀為「在均線之上」。**不得只做一半**。

- [X] T023 [US4] [A] 在 `app.py` 單一標的檢視（非 PORTFOLIO 模式）新增「均線現況」表：每條線一列，欄位為線別／均線值／目前價／位置（在上、在下）／乖離幅度。沿用該檢視既有的日線載入（`app.py:404-414`），演算法一律呼叫 `ma_lines` 純函式、**不得在 UI 層內嵌計算**（CLAUDE.md：UI 僅負責呈現）。資料不足之線 **MUST 顯示「資料不足」，不得顯示空白或 0** — FR-013
- [X] T024 [P] [US4] [A] 新增 SC-011/SC-012 測試：對「已低於年線但近期無穿越」之標的，現況表正確顯示位置與乖離**且不觸發任何推播**（FR-014）；資料不足之線顯示「資料不足」

**Checkpoint**: 四個 user story 的 A 段全部完成，可合併

---

## Phase 7: Polish & Cross-Cutting

- [X] T025 [A] `pytest -q` 全綠；並跑 `pytest -rs` 逐條檢查 skip 理由——**本案新測試若因缺 `trendpoint.db` 而 skip，即代表 A 段設計失敗**（A 段一律以合成資料執行）
- [X] T026 [P] [A] 更新 `CLAUDE.md`：專案地圖的通知段補上「均線觸價通知（spec 014，總開關預設關閉）」與 `ma_lines.py`；並註明本案使 `stock_*_daily` 在監控端有了第一個消費者
- [X] T027 [P] [A] 更新 `README.md` 的功能說明與設定範例（`alerts` 區塊），說明四條線的預設週期與總開關預設關閉
- [X] T028 [B] **[需真實資料]** SC-013 實跑驗收：`python run_ingestion.py` → 於 config 開啟 `alerts.ma_alerts_enabled: true` → `python monitor_signals.py --once`（無憑證走 Mock 分支）。觀察四項：(a) 均線值可與看盤軟體目視對照、(b) 訊息含 FR-009 全部欄位、(c) **既有六種告警照常運作且內容未變**、(d) 資料不足的標的被正確跳過而非發出假均線。另跑 `streamlit run app.py` 確認現況表顯示正確

  → **保持未完成（2026-08-07 覆核）。此項無法自動化代跑，須由使用者本人執行。**
  觀察項 (a) 要求與**外部看盤軟體目視對照**、末句要求人工確認 Streamlit 畫面——
  兩者都不是程式可斷言的判準，把它勾掉等於謊報。
  另有環境限制：本專案的 CI/agent 環境 proxy 擋掉 yfinance 與 TAIFEX（403），
  即使只跑 (b)(c)(d) 也取不到即時資料。
  **使用者需做的事**：於本機依上述四步執行，四項觀察逐一目視確認後再勾選。
  在此之前 `alerts.ma_alerts_enabled` 應維持 `false`（見本檔「啟用門檻」）
  → 此段已被下方 2026-09-01 進度部分推翻：本機 Claude Code 環境**取得到** yfinance／
  TAIFEX（出網能力隨 harness 版本漂移，見 CLAUDE.md），故 (b)(c)(d) 與儀表板項
  已實跑驗收；(a) 亦以獨立資料源交叉驗證替代。總開關已於 2026-09-01 依使用者
  要求開啟——**是在完成上述驗收之前開的**，順序與本檔「啟用門檻」不符，如實記錄。

  **2026-09-01 進度**（擴充案一併驗收，見 spec.md〈變更紀錄〉）：
  - (b) 訊息含 FR-009 全部欄位 — ✅ 以本機真實日線渲染確認（並附新增的均線現況區塊）
  - (c) 既有六種告警照常運作且內容未變 — ✅ 凍結基準 `fixtures_014_baseline_alerts.json`
    於總開關關閉時逐則相同；開啟時僅多出區塊
  - (d) 資料不足的標的正確跳過 — ✅ `test_sc005_insufficient_daily_data_skips_only_long_lines`
  - 儀表板現況表 — ✅ 實跑 `streamlit run app.py` 目視確認五條線與乖離皆正確
  - (a) 均線值目視對照 — **改以獨立資料源交叉驗證**（比目視更硬）：以 FinMind
    `TaiwanStockPrice`（未還原）對五檔重算，**週線在 5/5 檔完全吻合到分**，
    證明均線計算與 DB→計算的管線正確。長天期線的落差隨配息率單調上升
    （2330 年線 −0.12%、0050 −0.78%、00878 −2.67%、00919 −5.10%），
    00631L 半年／年線落差 −80%/−90%（股票分割）——**全數為還原股價所致，非缺陷**。
  - LINE 實機格式 — ✅ 觸發雲端 `--test-alert`（該指令自本次起附均線現況**樣本**
    區塊），使用者於 2026-09-01 確認收到且「五條線整齊、排版正常」。分隔線 `──`
    與全形括號 `（+1.00%）` 在 LINE 上呈現無誤。

  **結案（2026-09-01）**，但**必須連同下列兩點一起讀**，否則會高估本項的涵蓋範圍：

  1. **(a) 未經使用者以看盤軟體目視對照**——使用者選擇以 FinMind 交叉驗證的
     結果直接結案。故本項證明的是「均線計算正確、且與獨立資料源的差異可被
     還原股價完全解釋」，**不是**「與使用者慣用軟體顯示一致」。兩者不等價：
     若日後出現「推播的年線跟我看盤軟體差很多」的疑問，那**不是**回歸，
     先查該軟體用的是還原或未還原價、以及週期慣例（20/60/120/240 vs
     21/62/124/248），再回頭懷疑程式。
  2. **總開關是在本項完成之前開啟的**，順序與本檔「啟用門檻」不符。事後補驗
     全數通過，但「先開再驗」這件事本身如實記錄於此，不因結果良好而抹去。

---

## 實作結果（2026-07-30，A 段完成）

**28/28 完成**（T028 於 2026-09-01 結案）。惟 T028(a) 係以 FinMind 獨立資料源交叉驗證**替代**原訂的「與看盤軟體目視對照」，該替代的涵蓋範圍差異見 T028 條目。

- **測試**：`pytest -q` → **264 passed / 1 skipped / 1 deselected**
  （實作前為 234 passed，本案新增 30 個測試）。
  `pytest -rs` 確認唯一的 skip 是既有的 `test_portfolio_backtester.py:134`
  （需 `trendpoint.db`）——**本案新測試無一跳過**，A 段設計成立。
- **新增檔案**：`ma_lines.py`（純函式，MPL-2.0 標頭）、
  `tests/test_ma_lines.py`（14 測試）、`tests/test_ma_alerts.py`（16 測試）、
  `tests/ma_fixtures.py`、`tests/fixtures_014_baseline_alerts.json`（基準，
  commit `537ef25`）。
- **修改檔案**：`monitor_signals.py`（新增 `check_ma_touch_alerts`，
  既有 5 分線路徑與六種告警未動）、`config/config.py`（`MaLineConfig` /
  `MaAlertConfig` / `SystemConfig.alerts`）、`config/config.yaml`（`alerts` 區塊）、
  `app.py`（均線現況表 + `ma_lines` import）、`CLAUDE.md`、`README.md`。
- **零改動**（符合 plan 的範圍宣告）：`backtester.py`、`ladder_system.py`、
  `trading_costs.py`、`performance.py`。

**實作過程中的兩個修正**（記錄以供後續參考）：

1. **T002 首次擷取的基準為空集合**（0 則告警），該基準無鑑別力——
   「實作前 0 則、實作後 0 則」的比對抓不到任何回歸。根因是
   `frame_from_closes` 以**百分比**外擴 high/low，價位越高外擴越大，
   使「前一根 high」蓋過「本根收盤」、突破型訊號永不成立。
   改為**絕對值** pad 後基準含 1 則 `BULLISH_BOS`，比對才有意義。
2. **測試初版的訊息過濾條件過鬆**（以「跌破」二字辨識均線通知），
   誤將既有的「跌破下關價」與「BOS 結構連續跌破」計入，SC-002 誤判為 6 則。
   改以均線通知獨有的「乖離:」欄位為判準。

**尚未經人工目視確認**：`app.py` 的均線現況表（Streamlit 畫面）僅由
`ma_lines.build_status_rows` 的單元測試涵蓋其資料，實際渲染屬 T028 範圍。

## Dependencies

```text
Phase 1 (T001-T002)  ← 必須最先；T002 錯過即無從比對既有告警
        ↓
Phase 2 (T003-T008)  ← 阻塞所有 user story
        ↓
Phase 3 (US2, T009-T011)  ← 先釘死「不能碰哪裡」
        ↓
Phase 4 (US1, T012-T021)  ← 在已守住的接線點上加功能
        ↓
   ┌────┴────┐
   ↓         ↓
Phase 5    Phase 6
(US3)      (US4)
   └────┬────┘
        ↓
Phase 7 (T025-T028)
```

**跨階段硬依賴**：

- T004 依賴 T003、T006 依賴 T005（先紅後綠）
- T009 依賴 T007（需 `cfg.alerts` 才能判斷總開關）
- T010 依賴 T002（期望檔）＋ T009
- T012→T013→T014→T015→T016 為 `monitor_signals.py` 同區塊的循序改動
- T022 依賴 T012-T017（端到端路徑）
- T023 依賴 T004（`compute_ma_set`）
- **T028 依賴全部 A 段完成**

**B 段任務僅 T028**，依賴本機 `trendpoint.db`，在無資料環境不得標記完成。

## Parallel Execution Examples

```text
# Phase 2 起手可平行（不同檔案）
T003（純函式測試）｜T005（純函式測試）｜T007（config schema）｜T008（config yaml）

# Phase 4 測試群可平行
T018 ｜ T019 ｜ T020 ｜ T021

# Phase 7 文件類可平行
T026 ｜ T027
```

**不可平行**：T012 → T013 → T014 → T015 → T016（`monitor_signals.py` 同一新增區塊的循序建構）。

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + Phase 3 + Phase 4**（US2 + US1 的 A 段）。

理由：US2（既有告警不變）與 US1（穿越通知）是同一枚硬幣的兩面——
沒有 US2 的守門，US1 的實作隨時可能把既有推播改壞。兩者完成即可合併：
**總開關預設關閉，對既有行為零影響，功能就位待啟用**。

US3（資料不足）的核心已在 Phase 2 的純函式完成，Phase 5 只是端到端驗證。

**US4（儀表板）雖列 P3，但不得省略**——它是「只做穿越、不做狀態播報」
這個決策的配套。少了它，使用者無法得知「開啟功能時就已在均線下方」的標的，
而「沒收到通知」會被誤讀為「在均線之上」。

**合併門檻**：A 段全綠（SC-001~012）即可合併。
**啟用門檻**：使用者於 config 開啟總開關前，建議先完成 T028 確認輸出長相。

**本案與 spec 012／013 的性質差異**：不進入訊號或回測路徑，
故**不需要**前後回測對照與消融。它需要的是 SC-001 的「既有告警不變」
與 T028 的實跑觀察。

**實作時每次改動 `monitor_signals.py` 後的自檢**：

1. `monitor_signals.py:167` 的 5 分線 fetch 是否原封不動？
2. 總開關關閉時是否**完全不讀**日線表（而非讀了不用）？
3. 穿越前值是否取自 5 分線序列的前一根，而非前一日日線收盤？
