---

description: "Task list for 012 — BOS 續勢進場的量能確認濾網（可消融、預設關閉）"
---

# Tasks: BOS 續勢進場的量能確認濾網（可消融、預設關閉）

**Input**: Design documents from `specs/012-bos-volume-confirmation/`

**Prerequisites**: [plan.md](plan.md)、[spec.md](spec.md)、[research.md](research.md)、
[data-model.md](data-model.md)、[contracts/bos-volume-filter.md](contracts/bos-volume-filter.md)、
[quickstart.md](quickstart.md)

**Tests**: **必要，非選配**。憲章原則 III 要求每條驗收標準對應至少一個 pytest
測試；原則 I 要求新增訊號判定必須在 `tests/test_lookahead_bias.py` 加防禦測試。
故本案所有測試任務為硬性，且採**先紅後綠**（spec 010/011 既有實踐）。

**Organization**: 依 user story 分組。US2（基準不被污染）與 US1 同為 P1 且
**US2 的 T002 必須最先執行**——「實作前」的行為一旦改碼就消失、無法重建。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔案、無未完成依賴）
- **[Story]**: US1 / US2 / US3；Setup、Foundational、Polish 階段無標籤
- **[A] / [B]**: 驗收環境切分（plan.md §驗收環境切分）
  - **[A]** 離線可完成且可驗收——合成資料即足，CI 可跑
  - **[B]** 需真實市場資料（`trendpoint.db`），**必須在本機執行**

## Path Conventions

單一 Python 專案，扁平結構，repo root 即工作目錄。路徑含中文與空格，
**Bash 中一律雙引號**（CLAUDE.md 鐵律 1）。

---

## Phase 1: Setup（基準凍結）

**Purpose**: 凍結「實作前」行為。這一步錯過就無法重建——改碼後舊行為即消失，
SC-001 的逐筆比對將失去比對對象。

- [X] T001 [A] 在 `tests/fixtures/` 建立可重現的合成 OHLCV 產生器（固定 seed，沿用 `data_sources/mock_source.py` 的 rng 慣例），輸出足以觸發多次 BOS 進場的日線序列（建議 ≥ 400 根）；序列須包含「BOS 成立但量能萎縮」與「BOS 成立且量能放大」兩類 K 線，供 SC-003 使用
- [X] T002 [A] 以 T001 序列在**未改碼**的狀態下執行 `BacktestEngine.run_backtest`（預設參數），將逐筆 trades 與 equity 終值存為**入版控的測試期望檔**（如 `tests/fixtures/012_baseline_trades.csv`），並於檔頭註記 commit SHA
  - 說明：與 spec 011 把 baseline 放進已 gitignore 的 `data/` 不同——本案的 baseline 是 **CI 常態守門的測試期望值**（SC-001），必須入版控才能持續生效。它是測試輸入而非可再生成產物，不牴觸憲章 VI。體積須壓到最小（單一標的、單一參數組）。

**Checkpoint**: 實作前行為已凍結成檔且入版控，可以開始改碼

---

## Phase 2: Foundational（指標與參數層）—— 阻塞所有 user story

**Purpose**: 產出量能判定本體、參數管道與進場層介面。US1/US2/US3 全部依賴這一層。

**⚠️ CRITICAL**: 本階段未完成前，任何 user story 都無法開始

- [X] T003 [P] [A] 在 `tests/test_bos_volume_confirmation.py` 新增 `calculate_volume_confirmation` 契約測試（**先紅**）：以手算小序列斷言 (a) 平均量只用判定根之前的 K 線（`.shift(1)`）、(b) 前 `period` 根恆為 `False`、(c) `vol_ma <= 0` 時為 `False`、(d) `volume` 恰等於門檻時為 `False`（嚴格大於）、(e) 回傳無 NaN — 對應 contracts §1
- [X] T004 [A] 在 `ladder_system.py` 實作 `calculate_volume_confirmation(df, period=20, mult=1.5)`：`vol_ma = df['volume'].rolling(period).mean().shift(1)`，判定式 `vol_ma.notna() & (vol_ma > 0) & (df['volume'] > vol_ma * mult)`，`fillna(False)` 收尾並轉 `bool` dtype；**禁止**依賴 NaN 比較的隱性 False（research.md D6）— FR-001/FR-007（T003 轉綠）
- [X] T005 [P] [A] 在 `config/config.py` 的策略參數模型新增三欄位：`use_bos_volume: bool = False`、`bos_volume_mult: float = Field(default=1.5, gt=0)`、`bos_volume_period: int = Field(default=20, ge=2)`，description 標註 spec 012 — FR-003/FR-009
- [X] T006 [P] [A] 在 `config/config.yaml` 的 `strategy.default` 顯式寫出三參數（`use_bos_volume: false`），並加註解說明「預設關閉；與 `mss_volume_mult` 獨立；回看期未綁定 `structure_period`，理由見 spec 012 research.md D4」— FR-009
- [X] T007 [A] 在 `ladder_system.build_indicator_frame` 新增三個關鍵字參數（`use_bos_volume=False`、`bos_volume_mult=1.5`、`bos_volume_period=20`），並在 `use_bos_volume=True` 時輸出 `bos_volume_ok` 欄；**關閉時不輸出該欄**（比照 `include_regime` 的既有短路模式 `ladder_system.py:521-526`）。新參數**不得**傳入 `detect_market_structure` — FR-002/FR-004/FR-010
- [X] T008 [P] [A] 在 `tests/test_bos_volume_confirmation.py` 新增欄位集測試：關閉時輸出欄位集與實作前**逐字相同**（欄名與順序）、啟用時恰多 `bos_volume_ok` 一欄 — FR-002
- [X] T009 [A] 在 `ladder_system.PositionManager.check_entry_signal` 新增關鍵字參數 `volume_ok: bool = True`，判定式加入 `volume_conf_ok = volume_ok or ('bos_volume' in disabled_filters)`；更新 docstring 說明該維度僅由續勢分支傳入、反轉分支不傳（預設 True）— FR-005/FR-008，contracts §3

**Checkpoint**: 指標可算、參數可讀、進場層可接收判定值，且既有 14 個呼叫點零改動

---

## Phase 3: User Story 1 - 量化 BOS 量能確認的真實貢獻 (P1)

**Goal**: 讓「這道濾網貢獻期望值還是只殺樣本數」成為可量化的問題。

**Independent Test**: 執行消融測試，輸出表格出現「停用 BOS 量能確認」列，
其交易筆數與扣成本後期望值/PF/MDD 可與基準列直接比較。

- [X] T010 [A] 在 `backtester.py` 的 `run_backtest` 簽名新增三參數（預設值同 schema），穿線至 `build_indicator_frame`；並在指標框建構後比照 `regime_ok` 的既有處理補缺欄：`if 'bos_volume_ok' not in temp_df.columns: temp_df['bos_volume_ok'] = True`（`backtester.py:211-214` 慣例）— FR-002
- [X] T011 [A] 在 `backtester.py` 的**續勢（BOS）進場分支**傳入判定值：多方 `:265` 與空方 `:291` 兩處 `check_entry_signal` 加 `volume_ok=bool(sig_row['bos_volume_ok'])`；**反轉（MSS）分支 `:274`/`:301` 不傳**。取值一律用 `sig_row`（判定根）而非 `struct_row`（`iloc[i-2]`，會多一根延遲）— FR-005/FR-006，contracts §3 呼叫端契約
- [X] T012 [P] [A] 在 `run_backtest.py` 把 `params.use_bos_volume` / `params.bos_volume_mult` / `params.bos_volume_period` 穿線至 `engine.run_backtest`（比照既有 `volume_mult` 的傳法 `run_backtest.py:128`）
- [X] T013 [A] 在 `run_ablation.py` 的 `ABLATION_TARGETS` 新增 `("停用 BOS 量能確認", "bos_volume")`，並穿線新參數；**濾網未啟用時該列須明示「未啟用，略過」**，不得靜默輸出一列與基準相同的數字誤導判讀 — FR-008，data-model.md §4
- [X] T014 [P] [US1] [A] 在 `tests/test_bos_volume_confirmation.py` 新增 SC-003 測試：構造一根「續勢訊號成立、動能/趨勢/波動/全域四道皆通過、僅量能未達門檻」的 K 線，斷言啟用時不進場、關閉時進場（證明差異**僅**由量能造成）
- [X] T015 [P] [US1] [A] 新增 SC-005 測試：`bos_volume_period` 暖機區間內（前 N 根）啟用濾網時不產生任何進場
- [X] T016 [P] [US1] [A] 新增 SC-007 測試：斷言 `run_ablation.ABLATION_TARGETS` 含 `'bos_volume'` 鍵；並以合成資料實跑該消融列，斷言交易筆數/期望值/PF/MDD 皆有值（非 NaN、非空）
- [X] T017 [P] [US1] [A] 新增 SC-008 測試：反轉（MSS）進場分支的判定在濾網啟用前後完全相同——即使該根 `bos_volume_ok` 為 False，MSS 進場仍成立（證明 FR-005 未雙重套用）
- [ ] T018 [US1] [B] **[需真實資料]** SC-010 前後回測對照：依 [quickstart.md](quickstart.md) B 段步驟，對 `0050.TW`（現貨）與 `TXF`（期貨，含空方）跑啟用/停用兩組，記錄交易筆數、扣成本後期望值、Profit Factor、MDD、勝率（僅輔助）

**Checkpoint**: US1 的 A 段可獨立交付——消融器材就位、可量化；B 段待本機資料

---

## Phase 4: User Story 2 - 基準不被污染 (P1)

**Goal**: 保證濾網關閉時全系統行為與實作前逐筆一致，使消融比較有意義。

**Independent Test**: 濾網關閉時，同資料同參數的回測輸出與 T002 凍結的基準
逐項相等，差異數為 0。

- [X] T019 [US2] [A] 在 `tests/test_backtester.py`（或新測試檔）新增 SC-001 回歸測試：以 T001 序列跑濾網關閉的回測，與 T002 的期望檔逐筆比對進出場時點、方向、股數/口數、損益與權益曲線終值，差異數須為 0
- [X] T020 [P] [US2] [A] 新增 SC-002 測試：同一資料下以 `use_bos_volume` 為 True/False 各建一次指標框，斷言 `bos_signal` 與 `mss_signal` **逐值相等**（證明 FR-004：訊號層未被污染）
- [X] T021 [P] [US2] [A] 擴充 `tests/test_acceptance_parity.py`：把 `bos_volume_ok` 納入前綴一致性驗證（`PARITY_COLUMNS`），**僅在啟用參數的測試組**生效，不得讓預設組因缺欄而失敗 — spec 004 時序契約 §3
- [X] T022 [US2] [A] 擴充 `tests/test_lookahead_bias.py`：SC-006 防禦測試——篡改判定根**之後**的成交量（含在序列尾端追加資料），斷言所有進場判定不變；另加一則「移除 `.shift(1)` 即應失敗」的鑑別力對照（避免測試永遠為綠）
- [X] T023 [US2] [A] 在 `monitor_signals.py` 的 `check_new_signals` 取 `cfg.strategy.get_params_for_ticker(ticker)`，穿線**本案三個新參數**至 `build_indicator_frame`；濾網啟用時 BOS 告警（`:214-227`）額外要求 `latest_bar['bos_volume_ok']`。**既有硬編碼 `structure_period=10` / `use_fvg=True` / `fvg_lookback=3` 保持不動**（research.md D5——順手改會污染預設行為）— FR-010
- [X] T024 [P] [US2] [A] 新增 monitor 迴歸測試：濾網關閉時，對固定合成 df 的告警集合與實作前相同（沿用 spec 004 遷移契約的 monitor 判定迴歸範式）
- [ ] T025 [US2] [B] **[需真實資料]** SC-011：濾網啟用後實跑 `python monitor_signals.py --once`，比對其對續勢進場候選的判定與同一資料的回測一致（量能未達門檻的 BOS 不得推播為進場候選）

**Checkpoint**: 基準位元不變已由 CI 常態守門；backtest/live 對新濾網的判定一致

---

## Phase 5: User Story 3 - 空方鏡像對稱 (P2)

**Goal**: 多空兩側受同等對待，不出現「多方被量能濾掉、空方照樣進場」的不對稱。

**Independent Test**: 以價格鏡像資料執行多空兩側回測，量能條件對進場的影響逐項對稱。

> 空方**實作**已由 T011 完成（同一欄、同一參數傳入空方分支）。本階段為驗證，
> 確保對稱性被測試釘死而非偶然成立。

- [X] T026 [P] [US3] [A] 擴充 `tests/test_short_side.py` 的方向鏡像真值表（`:38-83`）：新增 `volume_ok` 維度，斷言 `volume_ok=False` 時多空兩側皆不進場、其餘維度真值表不變 — SC-004
- [X] T027 [US3] [A] 新增鏡像回測對稱測試：對一組合成序列與其價格鏡像分別跑多方（現貨）與空方（期貨 + `enable_short=True`）回測，斷言量能條件對兩側進場的影響逐項對稱

**Checkpoint**: 三個 user story 的 A 段全部完成，可合併

---

## Phase 6: Polish & Cross-Cutting

- [X] T028 [A] `pytest -q` 全綠；並跑 `pytest -rs` 逐條檢查 skip 理由——**本案新測試若因缺 `trendpoint.db` 而 skip，即代表 A 段設計失敗**（A 段測試一律以合成資料執行）
- [X] T029 [P] [A] 驗證 Numba 降級路徑：在無 Numba 環境重跑本案測試，輸出須一致（憲章原則 IV；CI 已有 uninstall-rerun 步驟）
- [X] T030 [P] [A] 更新 `CLAUDE.md` 專案地圖：於 specs 清單加入 `012`（BOS 量能確認濾網，預設關閉、待實測裁決）一行
- [X] T031 [P] [A] 在 `docs/reviews/2026-07-30-wma-strategy-review.md` 的「真正的缺口」第 1 項補上交叉引用連結至 `specs/012-bos-volume-confirmation/`
- [ ] T032 [B] **[需真實資料]** 把 T018 的實測數字回填至 `spec.md` 的 SC-010 條目，**無論結果有利與否**；若期望值未改善，維持 `use_bos_volume: false` 並於 spec 標註「實測無益」，Status 標為 `Implemented（濾網保留關閉）`
- [ ] T033 [B] **[需真實資料]** 僅當 T018 結果有利才執行：`python run_walk_forward.py` 取 out-of-sample 確認後，才可討論是否改為預設啟用（單次回測對照不足以支撐採用決策）

---

## Dependencies

```text
Phase 1 (T001-T002)  ← 必須最先；T002 錯過即無法重建基準
        ↓
Phase 2 (T003-T009)  ← 阻塞所有 user story
        ↓
   ┌────┴────┬─────────┐
   ↓         ↓         ↓
Phase 3    Phase 4   Phase 5
(US1)      (US2)     (US3，實作依賴 T011)
   └────┬────┴─────────┘
        ↓
Phase 6 (T028-T033)
```

**跨階段硬依賴**：

- T004 依賴 T003（先紅後綠）
- T007 依賴 T004、T005
- T010/T011 依賴 T007、T009
- T013 依賴 T010（消融需引擎已接參數）
- T019 依賴 T002（基準檔）＋ T010
- T026/T027 依賴 T011（空方實作）
- **T032/T033 依賴 T018**（無實測數字不得回填、不得討論預設啟用）

**B 段任務（T018、T025、T032、T033）全部依賴本機 `trendpoint.db`**，
在無資料環境一律不得標記完成，也不得以「合成資料跑過了」代替。

## Parallel Execution Examples

```text
# Phase 2 起手可平行（不同檔案）
T003（測試）｜T005（config schema）｜T006（config yaml）

# Phase 3 測試群可平行（同檔不同函式，或不同檔）
T014 ｜ T015 ｜ T016 ｜ T017

# Phase 4 可平行
T020 ｜ T021 ｜ T024

# Phase 6 文件類可平行
T029 ｜ T030 ｜ T031
```

**不可平行**：T010 → T011（同檔相鄰區塊）、T004 → T007（後者呼叫前者）。

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + Phase 3 的 A 段 + Phase 4 的 A 段**。

理由：US1（可量化）與 US2（基準不被污染）是同一枚硬幣的兩面——沒有 US2 的
位元不變保證，US1 的消融數字無法歸因。兩者的 A 段一起完成即可合併：
**濾網預設關閉，對既有行為零影響，但器材已就位**。

US3（空方鏡像）為 P2，可後續補；空方實作本身已隨 T011 完成，缺的只是對稱性測試。

**合併門檻**：A 段全綠（SC-001~009）即可合併。
**採用門檻**（是否改為預設啟用）：需 B 段 SC-010 有利 **且** T033 的
out-of-sample 確認——兩者缺一即維持關閉。

**禁止事項**：B 段未完成前，不得在任何文件（含 commit message、PR 說明）
宣稱本濾網「提升勝率」或「改善期望值」。


---

## 實作結果（2026-08-01，A 段完成）

**29/33 完成**；未完成的 4 項（T018、T025、T032、T033）全部是 B 段，需本機
`trendpoint.db`。`pytest -q` → **335 passed, 1 skipped**（新增 31 個測試；
唯一 skip 是既有的 `tests/test_portfolio_backtester.py:134`，缺 `trendpoint.db`，
**本案新測試零 skip**，T028 的 A 段設計檢查通過）。

### 新增／變更檔案

| 檔案 | 內容 |
|---|---|
| `ladder_system.py` | `calculate_volume_confirmation()` 純函式；`build_indicator_frame` 三參數 + `bos_volume_ok` 條件輸出欄；`check_entry_signal` 新增 `volume_ok` 維度 |
| `backtester.py` | 三參數穿線、缺欄回填 True、**只**在兩個續勢分支傳 `volume_ok` |
| `config/config.py` / `config.yaml` | 三欄位，預設關閉 |
| `run_backtest.py` | 穿線本案三參數 **＋補上 spec 013 漏接的四個閘門參數** |
| `run_ablation.py` | 新增 `bos_volume` 消融列；`OPT_IN_KEYS` 概括「需先啟用才有資訊量」的鍵 |
| `monitor_signals.py` | 三參數自 config 穿線；啟用時 BOS 告警額外要求量能確認 |
| `tests/bos_volume_fixtures.py`（新） | 主序列、MSS 反轉序列、期貨版、獨立參考實作 |
| `tests/fixtures/012_baseline_*`（新） | T002 凍結基準（trades / 摘要 / 指標欄位集），commit `9bfb0fc` |
| `tests/test_bos_volume_confirmation.py`（新，22 測） | §1 契約、欄位集、SC-001~009 |
| `tests/test_bos_volume_monitor.py`（新，3 測） | T024 監控端整合 |
| `tests/test_lookahead_bias.py` | +3 測（SC-006） |
| `tests/test_acceptance_parity.py` | 新增 `bos_volume_on` 變體，`bos_volume_ok` 納入前綴一致性 |
| `tests/test_short_side.py` | 鏡像真值表加入 `volume_ok` 維度（SC-004） |
| `.github/workflows/tests.yml` | 無 Numba 步驟加入本案測試檔 |

### 與計畫的偏差（理由見 spec.md「實作期間發現」）

1. **不是把 `displacement` 接到 BOS，而是新增獨立函式**——前者會在訊號層
   改寫 `~bos` 互斥語意（發現 1）。
2. **SC-008 改採「特定進場是否倖存」的直接證法**，不比對「全部 MSS 進場逐筆
   相同」——擋掉一筆會改變後續路徑，逐筆相同不是合理要求（發現 3，與 spec 013
   同一課）。
3. **SC-001 數值層採 1e-9 相對容差**，結構層（欄位、筆數、時點、action）維持
   完全相等。理由同 spec 013：合成序列的價格由 `np.exp` 產生，其 SIMD 路徑
   依 CPU 而異，凍結檔跨機器不具位元可攜性。
4. **另備 `mss_reversal_klines()` 第二個 fixture**：主 fixture 在預設參數下
   沒有 MSS 反轉進場，SC-008 無從驗證；把主 fixture 調成兩用會犧牲其量能鑑別力。
5. **T001 的產生器放在 `tests/bos_volume_fixtures.py`** 而非 `tests/fixtures/`
   ——後者是既有的樣本資料檔目錄，可匯入模組依 repo 慣例放在 `tests/` 下。
   T002 的凍結檔仍依原文放在 `tests/fixtures/012_baseline_*`。
6. **T029 兩條 Numba 路徑皆已本地驗證**（補裝 numba 後 335 passed、解除安裝後
   本案相關 67 passed），另已加入 CI 的降級步驟。
7. **範圍外的順手修補**：`run_backtest.py` 漏接 spec 013 的四個閘門參數
   （發現 4）。不補的話 013 的 config 開關對該入口無效。

### B 段（未完成，需 `trendpoint.db`）

- **T018 → T032 →（條件性）T033**，以及 T025（monitor 與回測判定一致性實測）。
- 在 T018 完成前，**不得**在任何文件、commit message 或 PR 說明宣稱本濾網
  「提升勝率」或「改善期望值」。目前的狀態只是：**器材就位、預設關閉、
  對既有行為零影響**。
