---
description: "Task list for 007 — MSS 進場區別化（fractal 反轉校正）"
---

# Tasks: MSS 進場區別化（fractal 反轉校正）

**Input**: Design documents from `specs/007-mss-entry-distinction/`

**Prerequisites**: plan.md、spec.md、research.md、data-model.md、contracts/、quickstart.md

**Tests**: 本專案憲章 III 與 spec FR-011/FR-012 明確要求測試 → 生成測試任務（TDD：先寫失敗測試再實作）。

**Organization**: 依 user story 分組。因訊號→進場→FVG 驗證天然相依，US 順序為序列鏈（非完全獨立），已於 Dependencies 誠實標註。**長側先行**；短側進場執行任務標 `[BLOCKED-003]`（依賴重開的 spec 003 → 台指期基礎建設）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔、無未完成相依）
- **[Story]**: US1 / US2 / US3
- 所有路徑為 repo 根相對路徑

---

## Phase 1: Setup（基準擷取）

**Purpose**: 為 SC-002 前後對照擷取「校正前」基準；確認測試綠燈起點。

- [x] T001 以現行程式對代表標的（`config.yaml` tickers）跑回測，記錄交易數/勝率/EV/MDD（含 `trading_cost`）為「校正前」基準，寫入 `specs/007-mss-entry-distinction/baseline-pre-mss.md`
- [x] T002 [P] 執行 `pytest -q` 記錄現況綠燈基線（作為回歸對照起點）

---

## Phase 2: Foundational（阻塞性前置）

**Purpose**: 組態集中化（憲章 V）——新參數為 US1/US2 共同前置。

**⚠️ CRITICAL**: 本階段完成前，任何 user story 不得開工。

- [x] T003 於 `config/config.py` 的 `SingleStrategyParams` 新增欄位：`swing_fractal_n:int=Field(2,ge=1)`、`mss_reversal_entry:bool=Field(True)`、`mss_ladder_k:float|None=Field(None)`、`mss_volume_mult:float=Field(1.5,gt=0)`（含 docstring/驗證）
- [x] T004 於 `config/config.yaml` 的 `strategy.default` 新增對應鍵（並在 `ticker_overrides` 加註可覆寫）；確認舊組態不填亦可載入

**Checkpoint**: 組態就緒，US1 可開工。

---

## Phase 3: User Story 1 — 校正 MSS 為 fractal 反轉語意（Priority: P1）🎯 MVP

**Goal**: MSS 改為「反向已確認波段點突破 + 位移」，與 BOS 續勢語意分離、不再是子集；看前偏誤防禦到位。

**Independent Test**: `pytest tests/test_mss_reversal.py tests/test_lookahead_bias.py` 全綠；存在 bar 使 `mss_signal==±1` 而同向 `bos_signal` 不成立（SC-001）；遮蔽 `>t` 後 `MSS[t]` 不變（SC-004）。

### Tests for User Story 1 ⚠️（先寫，先讓它 FAIL）

- [x] T005 [P] [US1] `tests/test_mss_reversal.py`：`detect_swing_points` 對稱碎形真值表（swing high/low、平台、前後 N 根邊界為 False/NaN）
- [x] T006 [P] [US1] `tests/test_mss_reversal.py`：已確認結構分類 HH/HL/LH/LL 與 `trend_bias`（+1/−1/0）真值表
- [x] T007 [P] [US1] `tests/test_mss_reversal.py`：MSS 反轉真值表（上升跌破 HL→看跌 MSS、下降突破 LH→看漲 MSS、位移有無、FVG on/off）+ **SC-001 斷言**（mss ⊄ bos；同 bar 同向不同時成立）
- [x] T008 [P] [US1] `tests/test_lookahead_bias.py`：MSS fractal 遮蔽測試（遮 `>t` 後 `MSS[t]` 不變）+ `(t−N,t]` 未確認樞紐不影響 + 時序契約（第 i 列僅依賴 `iloc[:i+1]`）

### Implementation for User Story 1

- [x] T009 [US1] 於 `ladder_system.py` 新增 `detect_swing_points(df, n)`：向量化 `rolling(2n+1, center=True).max()/.min()` 對稱碎形，回傳 `is_swing_high/low` 與 `swing_high_val/low_val`（不做確認延遲，單一職責）
- [x] T010 [US1] 於 `ladder_system.py` 新增已確認結構推導：`shift(n)` 標記確認時點 + ffill 最近已確認 swing 值；分類 HH/HL/LH/LL 與 `trend_bias`（向量化；若需序列狀態改 numba `@jit` 並附無 numba 降級，憲章 IV）
- [x] T011 [US1] 校正 `ladder_system.py` 的 `detect_market_structure`（`:143`）：新增 `swing_n`/`volume_mult` 關鍵字（維持既有預設向後相容）；MSS 改為反向已確認波段點突破 + 位移；BOS 語意不變；`use_fvg` 閘門套於**新** MSS；移除硬編 `1.5`（改用 `volume_mult`）
- [x] T012 [US1] 於 `ladder_system.py` 的 `build_indicator_frame`（`:390`）把 `swing_n`/`volume_mult`（來自 config）穿線到 `detect_market_structure`，維持既有時序契約

**Checkpoint**: US1 完成 → SC-001/SC-004 通過；即時監控（`monitor_signals.py`）的 MSS 反轉告警語意自動校正（不需改該檔）。

> **US1 實作註記（2026-07-12）**：
> - 全套 `pytest -q` **90 passed**（含 `test_acceptance_parity` 前綴一致性 → SC-004 額外佐證）。
> - 實測 make_klines(600)：MSS 3 筆、**全部 mss ⊄ bos**（子集冗餘完全消除，SC-001）。
> - **T001（校正前回測基準）延後至 US2**：程式一改，「校正前」需以父提交擷取；且該基準專為 US2 的 SC-002 前後對照，故隨 US2 一起做（用 `mss_reversal_entry=False` 或 git 父提交）。
> - **提前處理的破壞性漣漪**：MSS 語意一改即打破 `test_fvg_confirmation.py` 的舊真值表與 spec 001 位元錨點（原列 T019/T020）。為守住綠燈，US1 已改寫該檔：移除舊語意真值表、新增「BOS 仍等於 spec 001 公式」錨點；新 MSS/FVG 反轉真值表落在 `test_mss_reversal.py`。T019 於 backtester 層的 `mss_reversal_entry=False` 錨點仍待 US2。

---

## Phase 4: User Story 2 — MSS 獨立反轉進場（Priority: P2）｜長側先行

**Goal**: 校正後 MSS 觸發獨立反轉進場，讓 MSS 品質影響回測 P&L（對照 spec 002 T014 零 delta）。

**Independent Test**: 校正後 `mss_reversal_entry=True` vs `False` 前後回測，交易數/勝率/EV/MDD 至少一項非零差異（含成本，長側）；回測 trades 無多空並存（SC-005）。

### Tests for User Story 2 ⚠️

- [x] T013 [P] [US2] `tests/test_mss_reversal.py`（或新增 `tests/test_mss_entry.py`）：反轉進場單元測試——反轉 profile `disabled_filters={'trend','global'}` 長側觸發；部位動作（無部位→開新／持反向→先平再開／持同向→略過）；**SC-005** 無多空並存

### Implementation for User Story 2

- [x] T014 [US2] `backtester.py`（進場迴圈 `:169-231`）：新增 MSS 反轉進場分支（受 `mss_reversal_entry` 開關）；長側 `direction==+1` 呼叫 `check_entry_signal(structure_sig=+1, disabled_filters=frozenset({'trend','global'}))`；k 用 `mss_ladder_k or ladder_k`
- [x] T015 [US2] `backtester.py`：部位動作規則（開新／先平再開／略過）；先平再開確保無多空並存（SC-005）
- [x] T016 [US2] `portfolio_backtester.py`（進場 `:355-361`）：組合層同步反轉進場分支與部位動作
- [x] T017 [US2] [BLOCKED-003] `backtester.py` / `portfolio_backtester.py`：短側反轉進場執行（`direction==−1`）——依賴 spec 003 的 `check_entry_signal` 方向泛化與做空部位管理；在 003（台指期）落地前，`direction==−1` 為 no-op（僅記錄訊號），並在程式碼標註 `# BLOCKED-003`
- [x] T018 [US2] 前後回測對照（`mss_reversal_entry` True vs False）產出 delta（含 `trading_cost`）→ 更新 `baseline-pre-mss.md`；驗證 **SC-002**（長側非零 delta）
- [x] T019 [US2] 檢視/更新受影響既有測試（`tests/test_acceptance_parity.py` 等）：MSS 語意變更後 `use_fvg=False` 不再與 spec 001 位元一致；把回歸錨點改為進場層 `mss_reversal_entry=False`（零進場變化）——避免沉默漂移（憲章 III）

**Checkpoint**: US2 長側完成 → SC-002（長側）/SC-005 通過。短側待 003。

---

## Phase 5: User Story 3 — FVG 對校正後 MSS 的 P&L 作用（Priority: P3）

**Goal**: 驗證 spec 002 的 FVG 閘門套於校正後 MSS 後，開/關 FVG 會改變回測 P&L（收 spec 002 SC-002 懸案）。

**Independent Test**: 校正後系統 `use_fvg=True` vs `False` 回測，P&L/交易數出現可量測差異（SC-003）。

### Tests for User Story 3 ⚠️

- [x] T020 [P] [US3] `tests/test_fvg_confirmation.py`：檢視/擴充 FVG 閘門套於**新** MSS 的真值表（近 `fvg_lookback` 有同向 FVG 才保留；否則歸零）

### Implementation for User Story 3

- [x] T021 [US3] FVG on/off 回測對照（`use_fvg` True vs False，校正後系統、長側）→ 驗證 **SC-003** 非零差異；於 `specs/007-mss-entry-distinction/baseline-pre-mss.md`（或附錄）記錄，明確回應 spec 002 SC-002「FVG 零 delta」懸案

**Checkpoint**: 三個 story 之長側部分皆可獨立驗證。

> **US3 實作註記（2026-07-12）**：**SC-003 未達成（如實記錄）**。FVG 確改變 MSS 訊號集
> （如 00878 日線 20→13），但現有資料反轉進場稀疏（日線每檔 0–1 筆）且恰皆伴隨 FVG，
> 故 FVG on/off 的回測 P&L 零差異；5 分線資料僅 243 根、無法佐證。此**精修**（非推翻）
> spec 002 SC-002 懸案：結構性根因（mss ⊆ bos）已解除，FVG 邊際 P&L 價值屬資料/時框相依。
> 不做 p-hacking。詳見 `baseline-pre-mss.md` US3；spec.md SC-003 已註記。

---

## Phase 6: Polish & Cross-Cutting

- [x] T022 [P] `pytest -q` 全綠（**SC-006** 硬性關卡）
- [x] T023 [P] 文件同步：更新受影響處對 MSS 語意的描述（spec 001/002 提及 MSS「同向+量能」之處加註已於 007 校正），避免沉默漂移（憲章 III）
- [x] T024 執行 `quickstart.md` V1–V5 驗證情境，確認 SC↔測試對照全數成立
- [x] T025 [P] 視情況更新 `CLAUDE.md` 專案地圖／specs 狀態（007 進度、003 重開為台指期限定）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 無相依，可立即開始
- **Foundational (Phase 2)**: 依賴 Setup；**阻塞所有 user story**
- **US1 (Phase 3)**: 依賴 Foundational
- **US2 (Phase 4)**: 依賴 **US1**（進場需校正後 MSS 訊號）+ Foundational
- **US3 (Phase 5)**: 依賴 **US2**（FVG 對 P&L 之作用需有反轉進場成交）
- **Polish (Phase 6)**: 依賴所有目標 story 完成

### User Story Dependencies（誠實標註：本功能為序列鏈）

- **US1 (P1)**: Foundational 後即可 → 交付 SC-001/SC-004 與監控告警語意（獨立可測 MVP）
- **US2 (P2)**: 需 US1 訊號；長側可完成，**短側 T017 阻塞於 spec 003（台指期基礎建設）**
- **US3 (P3)**: 需 US2 進場成交才能顯示 FVG 對 P&L 的差異

### Within Each User Story

- 測試先寫且先 FAIL（憲章 III / spec FR-011）
- swing 偵測 → 結構分類 → MSS 判定 → 穿線 config
- 進場分支 → 部位動作 → 前後回測對照
- 每個 story 完成再進下一優先級

### Parallel Opportunities

- T005–T008（US1 測試，不同測試函式/檔）可平行
- T009 與 T010 有相依（T010 用 T009 輸出）；T011 依賴 T009/T010
- Polish 的 T022/T023/T025 可平行

---

## Parallel Example: User Story 1 測試

```bash
# US1 測試先行（先 FAIL）：
Task: "detect_swing_points 真值表 in tests/test_mss_reversal.py"        # T005
Task: "結構分類 HH/HL/LH/LL 真值表 in tests/test_mss_reversal.py"       # T006
Task: "MSS 反轉真值表 + SC-001 in tests/test_mss_reversal.py"           # T007
Task: "MSS fractal 看前偏誤遮蔽 in tests/test_lookahead_bias.py"        # T008
```

---

## Implementation Strategy

### MVP First（US1）

1. Phase 1 Setup（擷取校正前基準）
2. Phase 2 Foundational（config 參數）
3. Phase 3 US1（fractal MSS 校正）
4. **STOP & VALIDATE**：SC-001（mss ⊄ bos）+ SC-004（看前偏誤）+ 監控告警語意
5. 這是最小可交付：MSS 語意已正確（含即時告警），即使尚未影響 P&L

### Incremental Delivery

1. Setup + Foundational → 就緒
2. US1 → 驗 SC-001/SC-004 → 交付（語意校正 MVP）
3. US2（長側）→ 驗 SC-002（長側）/SC-005 → 交付（MSS 開始影響 P&L）
4. US3 → 驗 SC-003 → 交付（FVG 回接 P&L，收 spec 002 懸案）
5. 短側（T017）待 spec 003（台指期）完成後補上，屆時 SC-002 擴至雙向

### 阻塞管理

- **T017（短側進場執行）阻塞於 spec 003 → 台指期資料管線 + 成本模型**；其餘任務不受阻，長側全鏈可獨立完成。
- 合併 007 前：`pytest -q` 全綠（SC-006）+ 附長側前後回測對照（鐵律 4）。

---

## Notes

- [P] = 不同檔、無相依
- 反轉進場複用既有 `check_entry_signal` 的 `disabled_filters`（`ladder_system.py:509`），零新抽象
- **破壞性變更**：MSS 語意改變後，`detect_market_structure(use_fvg=False)` 不再與 spec 001 逐位元相同；回歸錨點改由進場層 `mss_reversal_entry=False` 提供（見 T019）
- 每完成一任務或邏輯群組即 commit
- 任一 checkpoint 可停下獨立驗證
