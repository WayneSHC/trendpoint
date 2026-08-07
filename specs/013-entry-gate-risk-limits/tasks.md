---

description: "Task list for 013 — 進場閘門（回撤上限 + 結算日封鎖）"
---

# Tasks: 進場閘門（回撤上限 + 結算日封鎖）

**Input**: Design documents from `specs/013-entry-gate-risk-limits/`

**Prerequisites**: [plan.md](plan.md)、[spec.md](spec.md)、[research.md](research.md)、
[data-model.md](data-model.md)、[contracts/entry-gate.md](contracts/entry-gate.md)、
[quickstart.md](quickstart.md)

**Tests**: **必要，非選配**。憲章原則 III 要求每條驗收標準對應至少一個 pytest
測試；原則 I 要求新增判定必須在 `tests/test_lookahead_bias.py` 加防禦測試。
採**先紅後綠**（spec 010/011/012 既有實踐）。

**Organization**: 依 user story 分組。US1（回撤閘門）與 US2（基準不被污染）
同為 P1；**T002 必須最先執行**——「實作前」的行為一旦改碼即消失。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔案、無未完成依賴）
- **[Story]**: US1 / US2 / US3；Setup、Foundational、Polish 階段無標籤
- **[A] / [B]**: 驗收環境切分（plan.md §驗收環境切分）
  - **[A]** 離線可完成且可驗收——合成資料即足，CI 可跑
  - **[B]** 需真實市場資料（`trendpoint.db`），**必須在本機執行**

## Path Conventions

單一 Python 專案，扁平結構，repo root 即工作目錄。路徑含中文與空格，
**Bash 中一律雙引號**（CLAUDE.md 鐵律 1）。

## ⚠️ 本案兩個最高風險（實作時逐條核對）

1. **閘門誤擋出場**：接線點**必須**在 `backtester.py:244` 的
   `if not pm.is_active and position_shares == 0.0:` 區塊內、`if is_entry:`（`:308`）
   之前。**禁止**在迴圈開頭 `continue`——那會連出場判定與權益 append 一起跳過。
   守門任務：T013。
2. **狀態更新誤置於迴圈開頭**：`DrawdownGate.update()` **必須**在迴圈**尾端**
   （權益 append 處 `:581`）呼叫。搬到開頭會用到當根權益，構成看前偏誤。
   守門任務：T015。

---

## Phase 1: Setup（合成資料與基準凍結）

**Purpose**: 備妥可精確控制觸發條件的合成序列，並凍結「實作前」行為。
基準錯過即無法重建（改碼後舊行為消失），故必須最先。

- [X] T001 [A] 在 `tests/fixtures/` 建立本案專用合成序列產生器（固定 seed，沿用 `data_sources/mock_source.py` 的 rng 慣例），須提供四種變體：(a) 含連續虧損段 + 之後回升段（供回撤封鎖與恢復測試）、(b) 「封鎖中持倉且觸發吊燈停損」情境、(c) 索引含完整每月第三個週三、(d) 索引**刻意缺席**某月第三個週三（供假日後推測試）
- [X] T002 [A] 以 T001 的變體 (a) 在**未改碼**狀態下執行 `BacktestEngine.run_backtest`（預設參數），將**逐筆 trades、`equity_curve` 逐根全欄、以及 `equity_curve` 欄位集**三者存為入版控的測試期望檔（`tests/fixtures/013_baseline_*.csv` + 欄位集清單），檔頭註記 commit SHA
  - 說明：本案比 spec 012 多一層——**欄位集**必須凍結，否則 `block_reason` 若被誤實作成恆定輸出，SC-001 抓不到（見 quickstart.md A 段第 (c) 層）

**Checkbox**: 實作前行為（含欄位集）已凍結且入版控

---

## Phase 2: Foundational（`risk_gates.py` 純元件與參數層）—— 阻塞所有 user story

**Purpose**: 產出兩個可獨立單元測試的元件與參數管道。US1/US2/US3 全部依賴這一層。

**⚠️ CRITICAL**: 本階段未完成前，任何 user story 都無法開始

- [X] T003 [P] [A] 在 `tests/test_risk_gates.py` 新增 `settlement_days` 契約測試（**先紅**）：(a) 每月第三個週三正確、(b) 該日缺席時取其後第一個交易日、(c) 日內索引（同日多棒）去重後正確、(d) 該月第三個週三之後無交易日時該月不列入且**不拋錯**、(e) 純函式（同輸入同輸出、不含價量依賴）— 對應 contracts §1、SC-006/007 純函式層
- [X] T004 [P] [A] 在 `tests/test_risk_gates.py` 新增 `DrawdownGate` 狀態機測試（**先紅**）：(a) 初始 OPEN、(b) `dd <= -limit` → BLOCKED、(c) `dd >= -resume` → OPEN、(d) **遲滯區間內維持原狀態**（同一 dd 值在兩個方向進入時結果不同）、(e) `peak` 單調不減、(f) `resume_pct = 0.0` 合法、(g) `peak <= 0` 不除零 — 對應 contracts §2、data-model.md §1
- [X] T005 [A] 建立 `risk_gates.py` 實作 `settlement_days()` 與 `DrawdownGate`（T003/T004 轉綠）。**檔頭須加 MPL-2.0 標頭**（CLAUDE.md 授權節；格式參照既有核心 .py 檔）。模組**不得** import `backtester` / `ladder_system`（單向依賴，見 plan.md Complexity Tracking）；docstring 須說明「本模組為路徑相依風控元件，與無狀態指標層刻意分離」
- [X] T006 [P] [A] 在 `config/config.py` 新增四欄位：`use_dd_gate: bool = False`、`dd_limit_pct: float = Field(default=0.20, gt=0.0, lt=1.0)`、`dd_resume_pct: float = Field(default=0.10, ge=0.0, lt=1.0)`、`use_settlement_gate: bool = False`；並加**跨欄位 model validator** 強制 `dd_resume_pct < dd_limit_pct`（相等亦拒絕），錯誤訊息須說明遲滯用意 — FR-005/FR-012、SC-005
- [X] T007 [P] [A] 在 `config/config.yaml` 的 `strategy.default` 顯式寫出四參數（兩道閘門皆 `false`），加註解說明「預設關閉；門檻預設值僅為形式佔位，實際值須由 spec 013 SC-015 以 monte_carlo p95 回撤校準」— FR-012
- [X] T008 [P] [A] 在 `tests/test_config.py`（或既有 config 測試檔）新增 SC-005 測試：`dd_resume_pct >= dd_limit_pct` 之設定被 schema 拒絕（相等與反向兩種情形皆測）

**Checkpoint**: 兩個純元件可獨立測試通過、參數可讀且非法組合被擋

---

## Phase 3: User Story 1 - 回撤達上限時停止開新倉 (P1)

**Goal**: 在「爆倉」與「正常交易」之間補上唯一的中間層。

**Independent Test**: 構造連續虧損序列，啟用閘門後回撤跨過門檻起不再出現
新進場；回撤回復後恢復進場（兩個轉折皆可指出確切根數）。

- [X] T009 [A] 在 `backtester.py` 的 `run_backtest` 簽名新增四參數（預設值同 schema），並於迴圈**前**建立 `DrawdownGate(initial_equity=self.initial_capital, ...)`；閘門未啟用時不建立實例（或建立後恆不封鎖，擇一並註明）
- [X] T010 [A] 在 `backtester.py` 迴圈**尾端**（權益 append 處 `:581` 的同一位置）呼叫 `gate.update(current_equity)`。**此位置為 FR-004 的落點——註解須寫明「搬到迴圈開頭即構成看前偏誤」**，避免後人「優化」時搬動
- [X] T011 [A] 在 `backtester.py` 開新倉判定區塊尾端、`if is_entry:`（`:308`）**之前**接線：合成 `gate_ok`（回撤閘門 AND 結算日閘門，各自受 `disabled_filters` 的 `'dd_gate'` / `'settlement_gate'` 影響），若 `not gate_ok` 則 `is_entry = False; short_entry = False` 並記錄封鎖原因。**禁止**以 `continue`、折進 `global_ok`、或擴充 `check_entry_signal` 實作（三種反模式的後果見 contracts §3）— FR-001/FR-002/FR-006/FR-011
- [X] T012 [A] 在 `backtester.py` 實作 `block_reason` **條件輸出欄**：任一閘門啟用時，`equity_curve` 每根增該欄（未封鎖為 `""`，值域見 data-model.md §4）；兩道皆關閉時**不輸出**該欄 — FR-010
- [X] T013 [US1] [A] **【最高風險守門】** 在 `tests/test_entry_gate_integration.py` 新增 SC-003 測試：以 T001 變體 (b) 構造「閘門封鎖中且持倉觸發吊燈停損」，斷言**出場確實發生**且權益曲線無斷點（每根皆有值）。另加鑑別力對照：若把接線改成迴圈開頭 `continue`，此測試須失敗（以註解記錄，不需真的實作反模式）
- [X] T014 [P] [US1] [A] 新增 SC-002 整合測試：以 T001 變體 (a) 斷言回撤跨過封鎖門檻後不再出現新進場、回復至恢復門檻之上後恢復進場，**且能指出兩個轉折的確切根索引**
- [X] T015 [US1] [A] **【第二高風險守門】** 擴充 `tests/test_lookahead_bias.py`：SC-004 防禦測試——篡改判定根**之後**的價格（含序列尾端追加資料），斷言所有閘門判定與進場決策不變。另加鑑別力對照：把 `gate.update()` 搬到迴圈開頭時此測試須失敗（避免測試永遠為綠）
- [X] T016 [P] [US1] [A] 新增 SC-012 測試：封鎖事件的 `block_reason` 值正確（`"drawdown"` / `"settlement"` / `"drawdown+settlement"` / `""`），且未封鎖根為空字串
- [X] T017 [P] [US1] [A] 新增 SC-010 空方鏡像測試：以價格鏡像資料 + 期貨 `enable_short=True`，斷言閘門對空方進場的影響與多方逐項對稱（閘門無方向性）
- [X] T018 [US1] [B] **[需真實資料，須早於 T019]** SC-015 門檻校準：執行 `python run_backtest.py`，自 `monte_carlo` 重抽輸出取 p95 最大回撤作為 `dd_limit_pct` 的參考起點，並將**選擇依據**寫入 `spec.md` 的 SC-015 條目。順序不可與 T019 顛倒（理由見 quickstart.md B 段）
  → **完成 2026-08-07，run 31138969771**（`run_b_segment.py` 內建校準，順序由驅動保證：先校準、再以校準值跑情境）。七標的門檻表已回填 SC-015。**兩個依據層級的坑一併記錄**：(1) 帶號分布要取第 5 而非第 95 百分位（初版取錯，每檔都校準出 0.0000）；(2) 本輪數字產出前修掉期貨逐筆報酬率分母用調整後價的缺陷（曾使 TXF 深尾算出 −567.70%），故引用者須確認來源 run 在 `15a295e` 之後
- [X] T019 [US1] [B] **[需真實資料]** SC-014 前後回測對照：依 quickstart.md B 段步驟，對 `0050.TW`（現貨）與 `TXF`（期貨，含空方）跑啟用/停用各組，記錄 **MDD、Calmar、期望值、Profit Factor、交易筆數、總報酬**；判讀依 quickstart.md 的判讀原則表（**總報酬下降屬預期，不得據此判定無效**）
  → **完成 2026-08-07，run 31138969771。** 回撤閘門：七標的**全部封鎖 0 根**，逐欄與基準相同 → **「未觸發、無對照數據」，非「無效」**。結算日閘門（僅期貨）：TXF 封鎖 336 根卻只擋掉 1 筆交易，**MDD 加深 1.37pp、Calmar 惡化一倍** → 以風控該用的尺量，風險變大。逐項數字回填於 SC-014

**Checkpoint**: US1 的 A 段可獨立交付——回撤閘門就位且兩個高風險點已被測試釘死

---

## Phase 4: User Story 2 - 基準不被污染 (P1)

**Goal**: 保證兩道閘門關閉時全系統行為與實作前逐筆、逐根、逐欄一致。

**Independent Test**: 閘門關閉時，回測輸出與 T002 凍結的三層期望檔完全相等。

- [X] T020 [US2] [A] 在 `tests/test_entry_gate_integration.py` 新增 SC-001 **三層**回歸測試：與 T002 期望檔比對 (a) 逐筆 trades（時點/方向/口數/損益）、(b) `equity_curve` 逐根 `equity` 值、(c) `equity_curve` **欄位集**（證明 `block_reason` 未在關閉時輸出）。三層差異數皆須為 0
- [X] T021 [P] [US2] [A] 新增 SC-009 兩道閘門獨立性測試：僅啟用回撤閘門時結算日閘門對結果零影響，反之亦然（各單開一次，與雙開對照）
- [X] T022 [US2] [A] 在 `portfolio_backtester.py` 處理組合路徑：閘門關閉時行為不變（無需改動）；**若參數被啟用須明確標註不支援**（載入時警示或明確錯誤），不得沉默忽略——沉默忽略會讓使用者誤以為風控在保護他（research.md D7）
- [X] T023 [P] [US2] [A] 新增組合路徑測試：閘門參數啟用時，組合回測發出可觀察的「不支援」訊號（警示或錯誤），而非靜默執行

**Checkpoint**: 基準位元不變（含欄位集）已由 CI 常態守門

---

## Phase 5: User Story 3 - 結算日不開新倉 (P2)

**Goal**: 期貨標的在台指期結算日不開新倉。

**Independent Test**: 對含結算日的期貨資料，啟用後結算日不出現新進場；
非結算日與現貨標的不受影響。

> 結算日的**接線**已由 T011 的 `gate_ok` 合成一併完成。本階段補預計算與限定條件。

- [X] T024 [A] 在 `backtester.py` 迴圈**前**預計算結算日集合（`settlement_days(temp_df.index)`），並以引擎既有的 `is_futures` 限定：現貨標的不建立該集合、結算日閘門對其無效果且不報錯 — FR-006/FR-007
- [X] T025 [P] [US3] [A] 新增 SC-006 整合測試：以 T001 變體 (c) 斷言結算日不出現新進場，非結算日之進場判定與未啟用時**逐筆相同**
- [X] T026 [P] [US3] [A] 新增 SC-007 整合測試：以 T001 變體 (d)（第三個週三缺席）斷言封鎖日落在其後第一個交易日
- [X] T027 [P] [US3] [A] 新增 SC-008 測試：對現貨標的啟用結算日閘門，結果與未啟用**完全相同**且不拋錯

**Checkpoint**: 三個 user story 的 A 段全部完成，可合併

---

## Phase 6: 消融整合與 Polish

- [X] T028 [A] 在 `run_ablation.py` 的 `ABLATION_TARGETS` 新增兩列（`("停用回撤閘門", "dd_gate")`、`("停用結算日閘門", "settlement_gate")`）並穿線四參數；**閘門未啟用時該列須明示「未啟用，略過」**，不得靜默輸出與基準相同的數字
- [X] T029 [A] **【plan 階段發現的既有問題】** 修正 `run_ablation.py:107-113` 的判讀提示對風控閘門的誤判：現行啟發式為「停用後報酬未惡化且交易數增加 → 該濾網只在扼殺樣本數」，但**停用回撤閘門必然使總報酬上升且交易數增加**，於是這道正在正常工作的風控會被印成「可能只在扼殺樣本數」。修法：(a) `results` 增 `calmar` 欄（`summary` 已有 `calmar_ratio`），輸出表加一欄；(b) 對風控閘門列改以 **Calmar/MDD 惡化** 作為判讀依據，或將該類列排除於原啟發式之外並印出風控專用提示。須在程式碼註解說明「訊號濾網與風控閘門的判讀方向相反」
- [X] T030 [A] 新增 SC-011 測試：斷言 `ABLATION_TARGETS` 含 `'dd_gate'` 與 `'settlement_gate'` 兩鍵；以合成資料實跑該兩列，斷言交易筆數、期望值、Profit Factor、**MDD、Calmar** 皆有值（非 NaN、非空）
- [X] T031 [A] `pytest -q` 全綠；並跑 `pytest -rs` 逐條檢查 skip 理由——**本案新測試若因缺 `trendpoint.db` 而 skip，即代表 A 段設計失敗**（A 段一律以合成資料執行）
- [X] T032 [P] [A] 驗證 Numba 降級路徑：在無 Numba 環境重跑本案測試，輸出須一致（憲章原則 IV；CI 已有 uninstall-rerun 步驟）
- [X] T033 [P] [A] 更新 `CLAUDE.md` 專案地圖：於 specs 清單加入 `013`（進場閘門，預設關閉、待實測裁決），並在演算法/回測區塊加入 `risk_gates.py`（路徑相依風控元件，與無狀態指標層分離）
- [X] T034 [P] [A] 在 `docs/reviews/2026-07-30-risk-engine-agent-review.md` 第六節的建議表補上交叉引用連結至 `specs/013-entry-gate-risk-limits/`
- [X] T035 [B] **[需真實資料]** 把 T018/T019 的實測數字與門檻依據回填至 `spec.md` 的 SC-014 / SC-015 條目，**無論結果有利與否**；若 MDD 未改善，維持兩道閘門 `false` 並標註「實測無益」，Status 標為 `Implemented（閘門保留關閉）`
  → **完成 2026-08-07。** 兩道閘門維持 `false`，Status 已改為 `Implemented（閘門保留關閉）`。**措辭刻意分開**：結算日閘門標「實測無益」，回撤閘門標「未觸發、無對照數據」——後者不是本任務原文預設的「MDD 未改善」，把零證據寫成反面證據會是假記錄。另新增「B 段實測發現」一節
- [~] T036 [B] **[需真實資料]** 僅當 T019 顯示風險調整後指標改善才執行：`python run_walk_forward.py` 取 out-of-sample 確認後，才可討論是否改為預設啟用（單次回測對照不足，門檻值尤易被後見之明挑選）
  → **不執行（前提未成立）。** 回撤閘門無對照數據、結算日閘門風險調整後惡化，兩者皆不滿足觸發條件

---

## Dependencies

```text
Phase 1 (T001-T002)   ← 必須最先；T002 錯過即無法重建基準（含欄位集）
        ↓
Phase 2 (T003-T008)   ← 阻塞所有 user story
        ↓
Phase 3 (US1, T009-T019)
        ↓ （T011 的 gate_ok 合成同時服務 US3）
   ┌────┴────┬──────────┐
   ↓         ↓          ↓
Phase 4    Phase 5   Phase 6
(US2)      (US3)     （T028 依賴 T009-T011）
```

**跨階段硬依賴**：

- T005 依賴 T003、T004（先紅後綠）
- T009 依賴 T005、T006
- T010、T011、T012 依賴 T009（同檔相鄰區塊，須循序）
- T013、T014、T015、T016 依賴 T010-T012
- T020 依賴 T002（期望檔）＋ T012（條件輸出欄）
- T024 依賴 T005（`settlement_days`）＋ T011（接線點已存在）
- T025-T027 依賴 T024
- T028 依賴 T009-T011；T030 依賴 T028、T029
- **T019 依賴 T018**（門檻須先校準，順序不可顛倒）
- **T035、T036 依賴 T019**（無實測數字不得回填、不得討論預設啟用）

**B 段任務（T018、T019、T035、T036）全部依賴本機 `trendpoint.db`**，
在無資料環境一律不得標記完成，亦不得以「合成資料跑過了」代替。

## Parallel Execution Examples

```text
# Phase 2 起手可平行（不同檔案）
T003（純函式測試）｜T004（狀態機測試）｜T006（config schema）｜T007（config yaml）

# Phase 3 測試群可平行（T013-T017 皆為測試，但 T013/T015 須先確認接線與更新位置）
T014 ｜ T016 ｜ T017

# Phase 5 測試群可平行
T025 ｜ T026 ｜ T027

# Phase 6 文件類可平行
T032 ｜ T033 ｜ T034
```

**不可平行**：T009 → T010 → T011 → T012（`backtester.py` 同檔相鄰區塊，
且 T011 的接線位置依賴 T010 建立的狀態更新點）。

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + Phase 3 的 A 段 + Phase 4 的 A 段**。

理由同 spec 012：US1（閘門有效）與 US2（基準不被污染）是同一枚硬幣的兩面——
沒有 US2 的位元不變保證，US1 的消融數字無法歸因。兩者 A 段完成即可合併：
**兩道閘門預設關閉，對既有行為零影響，但風控層就位**。

US3（結算日）為 P2，接線已隨 T011 完成，缺的只是預計算與限定條件測試。

**合併門檻**：A 段全綠（SC-001~013）即可合併。
**採用門檻**（是否改為預設啟用）：需 B 段 SC-014 顯示**風險調整後指標改善**
（不是總報酬）**且** T036 的 out-of-sample 確認——兩者缺一即維持關閉。

**禁止事項**：B 段未完成前，不得在任何文件（含 commit message、PR 說明）
宣稱本案「改善了風險」或「降低了回撤」。

**實作時每次改動 `backtester.py` 後的自檢**（本案特有）：

1. 接線點是否仍在 `if not pm.is_active` 區塊內？（grep 縮排層級）
2. `gate.update()` 是否仍在迴圈尾端？
3. 兩道閘門關閉時，`equity_curve` 是否仍無 `block_reason` 欄？

---

## 實作結果（2026-07-31，A 段完成）

**32/36 完成**；未完成的 4 項（T018、T019、T035、T036）全部是 B 段，需本機
`trendpoint.db`。`pytest -q` → **304 passed, 1 skipped**（新增 40 個測試；
唯一 skip 是既有的 `tests/test_portfolio_backtester.py:134`，缺 `trendpoint.db`，
**本案新測試零 skip**，T031 的 A 段設計檢查通過）。

### 新增／變更檔案

| 檔案 | 內容 |
|---|---|
| `risk_gates.py`（新） | `DrawdownGate` 狀態機 + `settlement_days()` / `third_wednesday_of()` 純函式 |
| `backtester.py` | 四參數、閘門建構、接線點、`record_equity()` 集中權益寫入與閘門推進 |
| `config/config.py` | 四欄位 + `_resume_below_limit` model validator |
| `config/config.yaml` | `strategy.default` 顯式寫出四參數（皆關閉） |
| `portfolio_backtester.py` | `warn_if_entry_gates_enabled()` 範圍護欄 |
| `run_ablation.py` | 兩列消融目標、四參數穿線、未啟用明示略過、Calmar/期望值兩欄、風控列判讀修正 |
| `tests/gate_fixtures.py`（新） | 四個變體的合成序列產生器 |
| `tests/fixtures/013_baseline_*`（新） | T002 凍結基準（trades / equity / 欄位集），commit `92b2a44` |
| `tests/test_risk_gates.py`（新，15 測） | T003/T004 契約測試 |
| `tests/test_entry_gate_integration.py`（新，13 測） | SC-001/002/003/006/007/008/009/010/012 + 組合護欄 |
| `tests/test_ablation_gates.py`（新，5 測） | SC-011 + T029 判讀修正 |
| `tests/test_lookahead_bias.py` | +3 測（SC-004） |
| `tests/test_config.py` | +4 測（SC-005 等） |
| `.github/workflows/tests.yml` | 無 Numba 步驟加入本案兩個測試檔（T032） |

### 兩個高風險守門點——**已實測反模式、確認測試會轉紅**

1. **接線位置**（T013）：暫時把接線改成迴圈開頭 `if not gate_ok: continue` 後重跑，
   `test_sc003_exits_still_execute_while_gate_is_blocking` 立即失敗
   （封鎖期間一筆出場都沒有）。反模式未入版控。
2. **狀態更新時點**（T015）：暫時把 `gate.update()` 搬到迴圈開頭後重跑，
   `test_entry_gate_reads_state_before_updating_it` 失敗（封鎖起始根的前一根
   回撤 −3.85% 未達 4% 門檻）。**另兩個看前偏誤測試仍為綠**——錯誤的更新時點
   並不會讓未來資料回頭影響過去，只會讓封鎖提前一根出現。三者分工已寫進 docstring。

### 與計畫的偏差（皆已在 spec.md「實作期間發現」記錄理由）

1. **SC-002 拆成兩條路徑驗證**。原任務要求「兩個轉折的確切根索引」在同一次
   對照中指出，但單標的路徑下空手且已封鎖的帳戶無法自行恢復（見 spec.md 發現 1），
   兩個轉折不可能都落在空手窗內。實作改為：以 `block_reason` 狀態軌跡指出
   全部五個轉折的確切根索引（1280 封鎖 / 1285 解除 / 1519 封鎖 / 1526 解除 /
   2450 閂鎖），另以較緊門檻（0.02/0.005）的第二組對照證明封鎖效果
   （進場數 10 → 1）。
2. **SC-006 的「非結算日逐筆相同」改為前綴相等**（spec.md 發現 2）。
3. **`block_reason` 欄的存在條件取「實際生效」而非「參數為真」**（spec.md 發現 3），
   與 contracts §3 字面略有出入，收緊方向。
4. **`gate.update()` 落在 `record_equity()` 內而非字面上的「迴圈尾端」**。
   權益曲線有四個 append 點，其中兩個以 `continue` 跳過迴圈尾端；閘門若只掛尾端，
   那兩根會漏更新。語意等價（皆為「寫入本根權益的同時推進閘門」）且更完備。
5. **合成序列產生器放在 `tests/gate_fixtures.py`** 而非 T001 寫的 `tests/fixtures/`
   ——後者是既有的樣本**資料檔**目錄（JSON/CSV），可匯入模組依 repo 慣例
   （`acceptance_fixtures.py`、`ma_fixtures.py`）放在 `tests/` 下。
   T002 的凍結檔仍依原文放在 `tests/fixtures/013_baseline_*`。
6. **凍結基準以 `float_format="%.17g"` 寫出、以 `float_precision="round_trip"` 讀回**。
   pandas 預設的 C 浮點剖析器不保證正確捨入，第一版凍結檔讀回會差 1 ulp——
   容忍 1 ulp 等於放棄「逐根位元不變」這條保證。基準檔是在 `git stash` 掉所有
   原始碼改動後於 `92b2a44` 重新產生的，仍是**實作前**行為。
7. **T032 兩條路徑皆已本地驗證**：本容器原無 numba，追加安裝後重跑
   `pytest -q`（304 passed）、再解除安裝重跑本案與既有降級測試（55 passed），
   兩條路徑輸出一致。本案兩個測試檔亦已加入 CI 的「驗證無 Numba 環境之
   降級回退」步驟。
8. **SC-001 的數值層改採 1e-9 相對容差，結構層維持完全相等**（見下節）。

### B 段（未完成，需 `trendpoint.db`）

- **T018 → T019 → T035 →（條件性）T036**，順序不可顛倒。
- 在 T019 完成前，**不得**在任何文件、commit message 或 PR 說明宣稱本案
  「改善了風險」或「降低了回撤」。目前的狀態只是：**風控層就位、預設關閉、
  對既有行為零影響**。

### 首次 CI 失敗與修正（2026-07-31）

第一次推上去後 `pytest (3.10)` 與 `(3.12)` **同時**失敗於
`test_sc001_gates_off_is_bit_identical_to_frozen_baseline`：
`equity_curve` 欄 `position_value` 有 14 根與凍結基準不同。

**排查**（依序排除）：

| 假設 | 檢驗 | 結果 |
|---|---|---|
| 我的接線改變了預設路徑 | trades 全部欄位（含 price、shares、時點）在 CI 上**完全相等** | 排除——行為改變不可能只動權益曲線而不動任何一筆交易 |
| numba 有無造成差異 | 本容器補裝 numba 0.66.0 後重跑 | 零差異，排除 |
| 套件版本漂移 | CI 3.12 job 的 numpy 2.4.6 / pandas 3.0.5 / numba 0.66.0 與本容器**完全相同** | 排除 |
| 硬體相依的 SIMD 路徑 | 兩個 numpy 版本**不同**的 CI job 得到**完全相同**的 14 根差異；本地零差異 | **成立** |

結論：差異源自合成序列產生器 `acceptance_fixtures.make_klines` 用的 `np.exp`——
numpy 的超越函數走 SIMD kernel，指令路徑依 CPU 特性在執行期選擇。
**這是 T002「逐根位元不變」這個寫法本身的問題**：凍結檔跨機器不具位元可攜性，
而回測引擎自身只用 `+,-,*,/` 與比較（IEEE-754 正確捨入、與硬體無關），
真正的行為改變會以 1e-4 以上的相對差異出現。

**修正**：結構層（欄位集、筆數、交易時點、action、索引）維持**完全相等**；
數值層改採 `BASELINE_RTOL = 1e-9` 相對容差，失敗訊息帶出實際最大相對偏差。
容差與真回歸的量級相距五個數量級，鑑別力不受影響。

驗收標準 SC-001 的文字仍為「逐筆一致」——**一致的判準改為「相對偏差 < 1e-9」，
不再是位元相等**。這是對平台現實的修正，不是對驗收的放寬。
