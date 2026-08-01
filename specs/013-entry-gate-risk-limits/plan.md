# Implementation Plan: 進場閘門（回撤上限 + 結算日封鎖）

**Branch**: `claude/wma-strategy-trendpoint-review-2kxfe0`（spec 目錄 `013-entry-gate-risk-limits`） | **Date**: 2026-07-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/013-entry-gate-risk-limits/spec.md`

## Summary

新增模組 `risk_gates.py`，內含兩個可獨立測試的元件：
`settlement_days(index)`（由資料自身交易日集合導出每月第三個週三，缺則後推）
與 `DrawdownGate`（兩狀態遲滯機：`dd <= -limit` → BLOCKED、`dd >= -resume` → OPEN）。

回測引擎在**開新倉判定區塊的尾端**、`if is_entry:` 之前，把閘門結果 AND 進
`is_entry` / `short_entry` 兩個布林：

```python
if not gate_ok:
    is_entry = False
    short_entry = False
```

這是本案最重要的實作選擇。它同時滿足三件事：**(a)** 位置在
`if not pm.is_active and position_shares == 0.0:`（`backtester.py:244`）區塊內，
所以出場路徑在結構上不可能被攔到（FR-002 由**程式結構**保證，而非靠註解提醒）；
**(b)** diff 極小、不需要重新縮排任何既有區塊，使 FR-009 的「逐筆位元不變」
易於人工複核；**(c)** `check_entry_signal` 完全不動——與 spec 012 不同，
本案不擴充該函式。

## Technical Context

**Language/Version**: Python 3.10+（CI 矩陣 3.10 / 3.12）

**Primary Dependencies**: pandas（既有）。**不引入新依賴**——結算日由資料索引
自行導出，刻意不引入交易日曆套件（離線 CI 不得依賴外部行事曆）。

**Storage**: 無 schema 變更、無入庫欄位。`block_reason` 為記憶體內的條件輸出欄。

**Testing**: pytest。新增 `tests/test_risk_gates.py`（純元件）與
`tests/test_entry_gate_integration.py`（引擎整合）；擴充
`tests/test_lookahead_bias.py`（FR-013）。既有 234 passed / 1 skipped 須維持。

**Target Platform**: 本機 CLI（`run_backtest.py` / `run_ablation.py`）＋ CI（不出網）。

**Project Type**: 單一 Python 專案（扁平結構）

**Performance Goals**: 結算日集合預計算 O(n) 一次；迴圈內每根為 O(1) 比較
＋一次 `set` 查找＋一次峰值更新。憲章 IV 無疑慮，無新增 `apply()` 或巢狀迴圈。

**Constraints（本案特有，與既有濾網根本不同）**:

- **閘門是路徑相依（path-dependent）的反饋迴路**。repo 現有的每一道濾網
  （regime / FVG / 量能 / 三關價）都能在迴圈前預先向量化成一欄，因為它們只依賴
  價量；**回撤閘門依賴權益，而權益依賴交易本身**。因此它無法預先算成欄位，
  必須以迴圈內狀態（running scalar）實作。
- 由此推論：**spec 004 的「前綴一致性」不變式不適用於本案**。那條契約是針對
  `build_indicator_frame` 的無狀態衍生欄；閘門狀態不是衍生欄。看前偏誤的防禦
  改由「只讀 `i-1` 為止的 running state」＋ SC-004 篡改測試承擔（見 research.md D2）。
- 預設關閉時**逐筆、逐根位元不變**（FR-009），且比對範圍須含**權益曲線逐根值**
  ——本案改變權益路徑，僅比摘要指標不足以證明。
- **SC-014 / SC-015 需真實資料，無法於 CI 或無資料環境驗收**（見「驗收環境切分」）。

**Scale/Scope**: 新增 1 個模組 + 2 個測試檔；修改 4 個既有檔案
（`backtester.py`、`config/config.py`、`config/config.yaml`、`run_ablation.py`）
＋ `run_backtest.py` 參數穿線。`ladder_system.py`、`trading_costs.py`、
`performance.py`、`monitor_signals.py`、`app.py` **零改動**。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 判定 | 依據 |
|------|------|------|
| I 看前偏誤（NON-NEGOTIABLE） | ✅ PASS | 閘門只讀**判定根（`i-1`）為止**的 running state：峰值與遲滯狀態在每根迴圈**尾端**（權益 append 處 `backtester.py:581`）更新，下一根開頭讀取——時序與 `sig_row = iloc[i-1]` 天然一致，無需 `.shift()`。結算日集合僅由日期導出，不含價量資訊。新增 SC-004 篡改防禦測試（含尾端追加資料）。**注意**：spec 004 前綴一致性不變式不適用（見 Technical Context），故不得以「parity 測試沒抓到」推論無偏誤。 |
| II 摩擦成本（NON-NEGOTIABLE） | ✅ PASS | 不觸碰任何費率；`trading_cost` 仍為唯一來源。FR-014 明訂裁決指標為風險調整後（Calmar/MDD/期望值），並明文「總報酬僅供輔助」——本案預期降低總報酬。 |
| III 規格↔測試 | ⚠️ PASS with `[MANUAL]` | SC-001~013 有 pytest 對應（見 [quickstart.md](quickstart.md) 對照表）；SC-014（前後回測對照）與 SC-015（門檻以 p95 回撤校準）依原則 III 標註 `[MANUAL]` 並附人工步驟。FR-015 為範圍排除條款、刻意無對應 SC（理由見 checklists/requirements.md Notes 3）。 |
| IV 效能紀律 | ✅ PASS | 預計算 O(n) 一次；迴圈內 O(1)。無 `apply()`、無新巢狀迴圈。閘門不觸及 Numba 路徑。 |
| V 組態集中 | ✅ PASS | 4 個新參數全數進 `config/config.yaml` + Pydantic schema（含 `ticker_overrides`），並含**跨欄位驗證**（恢復門檻須嚴格小於封鎖門檻，FR-005）。無硬編碼。 |
| VI 可重現/資料衛生 | ✅ PASS | 無新增產物、無入庫欄位、無資料契約變更。`block_reason` 採條件輸出（僅任一閘門啟用時存在），使預設狀態的 `equity_curve` 欄位集逐字不變。 |

**Gate 結論**：無違反項。但**新增模組 `risk_gates.py` 屬新增抽象層**，
依憲章 Governance「若新增抽象層或依賴，須回答為何更簡單的做法不可行」，
已填 Complexity Tracking（見文末）。

**Post-Design 複查（Phase 1 後）**：設計未新增依賴、未改動任何既有函式簽名
（`check_entry_signal` 零改動）、未新增目錄。上表判定不變。需持續盯的三點：

1. **FR-002（最高風險）**：閘門若被搬到迴圈更前面（例如以 `continue` 跳過整根），
   會同時跳過出場判定與權益更新——這是災難級誤實作。接線點必須留在
   `if not pm.is_active` 區塊內、`if is_entry:` 之前。SC-003 專門守門。
2. **原則 I**：峰值/狀態更新若被搬到迴圈**開頭**（在進場判定之前），就會用到
   當根資訊，構成看前偏誤。更新必須留在尾端。SC-004 守門。
3. **FR-009**：`block_reason` 欄若改為恆定輸出，預設狀態的 `equity_curve`
   欄位集即改變。已核對 `_calculate_metrics`（`backtester.py:602-616`）只取
   `equity` 與 `.get('position_value')`、測試只斷言 `["equity"]`，故條件輸出安全。

## Project Structure

### Documentation (this feature)

```text
specs/013-entry-gate-risk-limits/
├── plan.md              # 本檔
├── research.md          # Phase 0：七個設計決策與被否決的替代方案
├── data-model.md        # Phase 1：閘門狀態機、結算日集合、參數與輸出欄定義
├── quickstart.md        # Phase 1：驗收步驟與 SC↔測試對照（含 A/B 段切分）
├── contracts/
│   └── entry-gate.md    # risk_gates 元件契約 + 引擎接線契約
├── checklists/
│   └── requirements.md  # 規格品質檢查（已全項通過）
├── spec.md
└── tasks.md             # Phase 2 輸出（/speckit-tasks，本命令不產生）
```

### Source Code (repository root)

```text
risk_gates.py           # 【新模組】settlement_days() 純函式（FR-006）
                        # DrawdownGate 兩狀態遲滯機（FR-003/FR-005）
                        # 不 import backtester/ladder_system——單向依賴、可獨立測試

backtester.py           # run_backtest 新增 4 參數；迴圈前預計算結算日集合
                        # 迴圈尾端更新峰值與閘門狀態（FR-004 的時序落點）
                        # 開新倉區塊尾端 AND 掉 is_entry/short_entry（FR-001/FR-002）
                        # 任一閘門啟用時 equity_curve 增 block_reason 欄（FR-010）

config/config.py        # 4 個 Pydantic 欄位 + 跨欄位驗證（FR-005/FR-012）
config/config.yaml      # strategy.default 顯式寫出 4 參數（兩道閘門皆 false）
run_backtest.py         # params → run_backtest 穿線
run_ablation.py         # ABLATION_TARGETS 增兩列（FR-011）

ladder_system.py        # 【零改動】——本案不新增指標、不動 check_entry_signal
trading_costs.py        # 【零改動】
performance.py          # 【零改動】——MDD 計算沿用，只是這次終於有人消費它
monitor_signals.py      # 【零改動】——監控端無權益追蹤，回撤閘門無等價物
portfolio_backtester.py # 【零改動】——關閉時行為不變即滿足 FR-009（見 research.md D7）
app.py                  # 【零改動】

tests/
├── test_risk_gates.py              # 新檔：純元件（SC-002/005/006/007）
├── test_entry_gate_integration.py  # 新檔：引擎整合（SC-001/003/008/009/010/012）
└── test_lookahead_bias.py          # 擴充：SC-004（篡改判定根之後的價格/權益）
```

**Structure Decision**: 沿用扁平單一專案結構，不新增目錄。唯一的結構性新增是
`risk_gates.py`——理由與被否決的替代方案見 Complexity Tracking。
`ladder_system.py` 之所以零改動，是因為閘門**不是指標**：它路徑相依、有狀態，
放進「正典指標組裝入口」會破壞該入口「無狀態、可前綴一致」的既有契約。

## 關鍵設計決策摘要

完整論證見 [research.md](research.md)，此處列出對實作最有約束力的四條：

1. **接線點在 `is_entry` / `short_entry` 兩個布林上**，而非重新縮排進場區塊、
   也非在迴圈開頭 `continue`。前者 diff 巨大且不利於位元不變複核；
   後者會連出場與權益更新一起跳過（災難級）。AND 掉布林是唯一同時滿足
   「結構上擋不到出場」與「最小 diff」的選項。

2. **峰值與遲滯狀態在迴圈尾端更新**（權益 append 處），下一根開頭讀取。
   這使「只用已收盤資訊」由**執行順序**保證，而非靠 `.shift()`。
   搬到開頭即構成看前偏誤——此為本案第二高風險。

3. **回撤閘門無法預先向量化**，因為權益是交易的函數（路徑相依反饋迴路）。
   這是本案與 repo 所有既有濾網的根本差異，也是 spec 004 前綴一致性不變式
   不適用的原因——不能因為「parity 測試沒抓到」就認為沒有偏誤。

4. **兩道閘門共用出口，但各自獨立開關與獨立消融鍵**。共用出口省掉重複實作；
   獨立開關使 FR-008 的分別歸因成立。封鎖原因以條件輸出欄記錄，
   讓消融差異可被解釋，而不是只看到一個變小的數字。

## 驗收環境切分（使用者明示約束）

| 段 | 內容 | 驗收條件 | 本環境可否 |
|---|---|---|---|
| **A. 離線可完成** | `risk_gates.py`、引擎接線、參數、消融清單、全部單元/整合/look-ahead 測試 | SC-001~SC-013（`pytest -q` 全綠；合成資料即足） | ✅ 可 |
| **B. 需真實資料** | 前後回測對照與門檻校準 | SC-014、SC-015（`[MANUAL]`） | ❌ 需本機 |

**A 段的 SC-001 如何在無市場資料下驗收**：同 spec 012——以固定合成序列
（`data_sources/mock_source.py` 或測試 fixture）在改碼前擷取逐筆交易與
**權益曲線逐根值**存為入版控的期望檔，改碼後（兩道閘門關閉）逐項比對，
差異數須為 0。這驗證位元不變性，不需要真實資料。

**A 段的 SC-002/003 用合成資料比真實資料更好**：構造連續虧損序列即可精確觸發
回撤封鎖；構造「封鎖中且觸發停損」的情境即可驗證出場不被攔。真實資料反而
難以保證這些邊界情境一定出現。

**B 段未完成前的狀態約束**：兩道閘門維持 `false`、spec Status 不得標為
Implemented、不得宣稱「改善了風險」。SC-014 完成後無論結果如何皆回填數字；
SC-015 的門檻值須附選擇依據，不得憑感覺填一個好看的數字。

## Complexity Tracking

> 憲章 Governance 要求：新增抽象層須回答「為何更簡單的做法不可行」。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 新增模組 `risk_gates.py` | 閘門有兩個需要獨立單元測試的元件：一個純函式（結算日推導）與一個有狀態的遲滯機。二者皆與價量指標無關、與成本模型無關 | **塞進 `ladder_system.py`**：語意不符——該檔是「指標組裝」，其對外契約含無狀態與前綴一致性（spec 004），放入有狀態閘門會破壞該契約，且該檔已 758 行。**內聯於 `backtester.py` 迴圈**：遲滯機與結算日推導將無法單獨測試（只能透過整支回測間接觸發），SC-005/006/007 會被迫寫成昂貴且脆弱的整合測試。**放 `trading_costs.py`**：該檔語意為成本與部位大小，閘門不屬於任何一者（且 sizing 要到 spec 014 才動） |
