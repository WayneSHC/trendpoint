# Implementation Plan: BOS 續勢進場的量能確認濾網（可消融、預設關閉）

**Branch**: `claude/wma-strategy-trendpoint-review-2kxfe0`（spec 目錄 `012-bos-volume-confirmation`） | **Date**: 2026-07-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/012-bos-volume-confirmation/spec.md`

## Summary

在 `ladder_system.build_indicator_frame` 新增一道**條件輸出**的布林欄
`bos_volume_ok`（`volume > rolling_mean(volume, N).shift(1) × mult`），
並在 `check_entry_signal` 增加一個預設為 `True` 的 `volume_ok` 參數。
回測引擎只在**續勢（BOS）分支**傳入該欄真值、反轉（MSS）分支維持不傳（＝恆 True），
FR-005 因此由呼叫點自然成立、無需額外分支判斷。

三個「不做什麼」的決定比做什麼更重要：
1. **不在訊號層 gating**——反轉訊號以 `~bos` 保證同根互斥，訊號層抑制會憑空生出 MSS。
2. **不改既有呼叫端**——`volume_ok` 預設 `True` 使 `portfolio_backtester.py`、
   `validate_ladder.py` 與 6 個既有測試檔零改動。
3. **預設關閉時不輸出該欄**——沿用 `include_regime=False` 的既有短路模式，
   使 spec 004 的 parity 欄位集在預設狀態下逐字不變。

## Technical Context

**Language/Version**: Python 3.10+（CI 矩陣 3.10 / 3.12）

**Primary Dependencies**: pandas（既有）。**不引入新依賴**。不涉及 Numba
（單一 `rolling().mean()`，向量化即最優）。

**Storage**: 無 schema 變更。`bos_volume_ok` 為記憶體內衍生欄，不入庫、不入
回測產物的必要欄位。

**Testing**: pytest。新增 `tests/test_bos_volume_confirmation.py`；擴充
`tests/test_lookahead_bias.py`（FR-011）、`tests/test_acceptance_parity.py`
（前綴一致性須涵蓋新欄）、`tests/test_short_side.py`（鏡像真值表增一維）。
既有 234 passed / 1 skipped 須維持。

**Target Platform**: 本機 CLI（`run_backtest.py` / `run_ablation.py` /
`monitor_signals.py`）＋ GitHub Actions CI（不出網）。

**Project Type**: 單一 Python 專案（扁平結構）

**Performance Goals**: 每次 `build_indicator_frame` 增加一次 `rolling(N).mean()`
＋一次 `.shift(1)`，皆為 pandas 向量化；引擎迴圈內僅多一次 O(1) 欄位取值。
憲章 IV 無疑慮，無新增純 Python 迴圈或 `apply()`。

**Constraints**:
- 預設關閉時**逐筆位元不變**（FR-002）——這是消融比較的前提，任何順手改動皆違反。
- 不得改動 `bos_signal` / `mss_signal` 數值（FR-004）。
- 平均量必須 `.shift(1)`；判定根自身成交量可用（FR-001）。
- 新參數必須進 `config/config.yaml` + Pydantic schema，且可 `ticker_overrides` 覆寫（FR-009）。
- **SC-010 / SC-011 需真實市場資料，無法於 CI 或無資料環境驗收**（見下方「驗收環境切分」）。

**Scale/Scope**: 觸碰 7 個既有檔案 + 1 個新測試檔。無新模組、無新抽象層、
無新目錄。`trading_costs.py`、`performance.py`、`app.py` 零改動。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 判定 | 依據 |
|------|------|------|
| I 看前偏誤（NON-NEGOTIABLE） | ✅ PASS | 平均量 `rolling(N).mean().shift(1)` 僅由判定根**之前**已收盤 K 線構成；判定根自身成交量可用（該根已收盤，成交於次根開盤，與既有 MSS displacement 同一慣例 `ladder_system.py:246-247`）。新增防禦測試（FR-011/SC-006）＋ spec 004 前綴一致性不變式涵蓋新欄（SC-006 的最強形式）。 |
| II 摩擦成本（NON-NEGOTIABLE） | ✅ PASS | 本案不觸碰費率，`trading_cost` 仍為唯一來源。消融/對照輸出一律走既有 `_calculate_metrics`（已含成本）。FR-012 明文禁止以勝率單獨作為採用理由。 |
| III 規格↔測試 | ⚠️ PASS with `[MANUAL]` | SC-001~009 有 pytest 對應（見 [quickstart.md](quickstart.md) 對照表）；SC-010（前後回測對照）與 SC-011（monitor 實跑）已依原則 III 明文標註 `[MANUAL]` 並附人工步驟。 |
| IV 效能紀律 | ✅ PASS | 一次 rolling + 一次 shift，pandas 向量化；引擎內 O(1) 取值。無 `apply()`、無新迴圈。 |
| V 組態集中 | ✅ PASS | 三個新參數全數進 `config/config.yaml` + Pydantic schema（含 `ticker_overrides`）。**不沿用**硬編碼的 `structure_period` 作為回看期預設（理由見 [research.md](research.md) D4）——避免把既有缺陷擴散到新參數。 |
| VI 可重現/資料衛生 | ✅ PASS | 無新增產物、無入庫欄位、無資料契約變更。 |

**Gate 結論**：無違反項，Complexity Tracking 留空。淨新增抽象為零——
新增的是一個布林欄與一個布林參數，不是新的層。

**Post-Design 複查（Phase 1 後）**：設計未新增模組、未改動任何既有函式的
必填參數、未新增目錄，上表判定不變。需持續盯的兩點：
1. **原則 I**：實作時若有人把 `.shift(1)` 省掉（「反正判定根自己的量可以用」），
   即構成違反——平均量與判定根量是兩件事，前者必須 shift。已由 SC-006 釘死。
2. **FR-002**：若實作順手「順便」修了 `monitor_signals.py` 既有的硬編碼
   `structure_period=10`，預設狀態的 monitor 行為就會改變，基準即被污染。
   本案明文將該修正列為範圍外（見 research.md D5）。

## Project Structure

### Documentation (this feature)

```text
specs/012-bos-volume-confirmation/
├── plan.md              # 本檔
├── research.md          # Phase 0：六個設計決策與被否決的替代方案
├── data-model.md        # Phase 1：新增欄位與參數的定義、值域、時序契約
├── quickstart.md        # Phase 1：驗收步驟與 SC↔測試對照表（含離線/需資料切分）
├── contracts/
│   └── bos-volume-filter.md   # build_indicator_frame 與 check_entry_signal 的擴充契約
├── checklists/
│   └── requirements.md  # 規格品質檢查（已全項通過）
├── spec.md
└── tasks.md             # Phase 2 輸出（/speckit-tasks，本命令不產生）
```

### Source Code (repository root)

```text
ladder_system.py        # 新增 calculate_volume_confirmation()（FR-001/FR-007）
                        # build_indicator_frame 條件輸出 bos_volume_ok（FR-002/FR-010）
                        # check_entry_signal 新增 volume_ok=True 參數 + 'bos_volume'
                        #   消融鍵（FR-008）

backtester.py           # run_backtest 新增三參數並穿線至 build_indicator_frame
                        # 缺欄以 True 回填（沿用 regime_ok 慣例 backtester.py:211-214）
                        # 僅 BOS 分支（:265 多方 / :291 空方）傳 volume_ok（FR-005/FR-006）

config/config.py        # 三個 Pydantic 欄位 + 值域驗證（FR-003/FR-009）
config/config.yaml      # strategy.default 顯式寫出三參數（預設關閉）

run_backtest.py         # params → run_backtest 穿線
run_ablation.py         # ABLATION_TARGETS 新增「停用 BOS 量能確認」列（FR-008）
monitor_signals.py      # check_new_signals 取 per-ticker params、穿線新參數
                        # BOS 告警在濾網啟用時消費 bos_volume_ok（FR-010）

portfolio_backtester.py # 【零改動】check_entry_signal 新參數預設 True
trading_costs.py        # 【零改動】
app.py                  # 【零改動】

tests/
├── test_bos_volume_confirmation.py  # 新檔：SC-002/003/005/007/008
├── test_lookahead_bias.py           # 擴充：SC-006（篡改未來量不改判定）
├── test_acceptance_parity.py        # 擴充：PARITY_COLUMNS 涵蓋新欄
├── test_short_side.py               # 擴充：鏡像真值表增 volume_ok 維度（SC-004）
└── test_backtester.py               # 擴充：預設關閉逐筆回歸（SC-001）
```

**Structure Decision**: 沿用扁平單一專案結構，不新增目錄。改動集中於
「指標產生端（`ladder_system.py`）→ 參數傳遞鏈（config → 各 `run_*.py`）→
消費端（`backtester.py` / `monitor_signals.py`）」三段。
`portfolio_backtester.py` 之所以能零改動，純粹因為 `volume_ok` 預設 `True`——
這是刻意選擇的相容策略（見 research.md D3），不是巧合。

## 關鍵設計決策摘要

完整論證（含被否決的替代方案）見 [research.md](research.md)，此處列出對實作
最有約束力的四條：

1. **濾網作用於進場判定層，不作用於訊號產生層**。反轉訊號的定義含
   `& (~bear_bos)` / `& (~bull_bos)`（`ladder_system.py:254-258`）以保證同根互斥；
   若在訊號層抑制續勢訊號，該根原本被互斥排除的反轉訊號會轉為成立，
   等於「少一個 BOS 進場、多一個 MSS 進場」的無聲行為改變。

2. **`check_entry_signal` 新增 `volume_ok: bool = True`，而非改用 `disabled_filters` 傳值**。
   `disabled_filters` 的語意是「這道濾網視為通過」，無法承載「傳入實際判定值」；
   兩者並用——值走 `volume_ok`、消融走 `'bos_volume'` 鍵。預設 `True` 換來
   14 個既有呼叫點零改動。

3. **預設關閉時不輸出 `bos_volume_ok` 欄**，引擎對缺欄以 `True` 回填。
   完全比照 `include_regime=False` 省略 `regime_ok` + 引擎回填的既有模式
   （`ladder_system.py:521-526`、`backtester.py:211-214`），使 spec 004 的
   parity 欄位集在預設狀態下逐字不變。

4. **回看期為獨立參數、預設 20，不沿用 `structure_period`**。後者目前硬編碼於
   三處呼叫端（`backtester.py:194`、`monitor_signals.py:180`、
   `portfolio_backtester.py:99`）且值為 10，而函式宣告預設為 20——本 repo
   對「結構訊號的滾動窗」實際有兩個值。隱式綁定會把這個既有的憲章 V 缺陷
   擴散到新參數，故獨立設定並在 `config.yaml` 顯式寫出。

## 驗收環境切分（使用者明示約束）

實作可完成度受環境限制，明確切為兩段。**第一段可在無市場資料的環境（含 CI）
完整交付並驗收**；第二段必須在有 `trendpoint.db` 的本機執行。

| 段 | 內容 | 驗收條件 | 本環境可否 |
|---|---|---|---|
| **A. 離線可完成** | 全部程式碼、參數、消融清單列、單元/回歸/look-ahead/parity/鏡像測試 | SC-001~SC-009（`pytest -q` 全綠；合成資料即足） | ✅ 可 |
| **B. 需真實資料** | 前後回測對照與採用決策；monitor 一致性實跑 | SC-010、SC-011（`[MANUAL]`） | ❌ 需本機 |

**A 段的 SC-001 如何在無市場資料下驗收**：以合成 OHLCV（既有
`data_sources/mock_source.py` 或測試 fixture）產生固定序列，比對「實作前」
（`git stash` 或 main 分支）與「實作後、濾網關閉」的逐筆交易記錄，差異數須為 0。
這驗證的是**位元不變性**，不需要真實資料——真實資料只在 B 段裁決「濾網有沒有用」時才需要。

**B 段未完成前的狀態約束**：濾網維持 `false`，本規格 Status 不得標為
Implemented，且不得在任何文件宣稱「已提升勝率／期望值」。SC-010 完成後，
無論結果有利與否，都把實測數字回填至 spec 的 SC-010 條目。

## Complexity Tracking

> 無憲章違反項，本節留空。
