# Implementation Plan: 均線觸價通知（月／季／半年／年線）

**Branch**: `claude/wma-strategy-trendpoint-review-2kxfe0`（spec 目錄 `014-ma-touch-alerts`） | **Date**: 2026-07-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/014-ma-touch-alerts/spec.md`

## Summary

新增純函式模組 `ma_lines.py`（均線計算 + 穿越判定 + 現況彙整），
在 `monitor_signals.check_new_signals` 的**現貨分支尾端**加一段獨立的均線判定：
從 DB 讀 `stock_*_daily`（10 年歷史，本案為其第一個消費者）計算四條均線，
以既有 5 分線路徑的「最新已收盤棒」收盤價作比較價，判定向下穿越後推播。
`app.py` 單一標的檢視加一張現況表（US4），回答推播不回答的「現在是什麼狀態」。

本案的實作重心不在演算法（四條 SMA 而已），而在**三條不可越界的線**：

1. **兩條資料路徑必須並存**——既有六種告警走 5 分線即時路徑，本案走日線 DB。
   把監控整個改成日線會改變所有既有推播的行為（`CLAUDE.md` 已記錄該 5 分線
   路徑為刻意設計）。
2. **穿越是事件、不是狀態**——去重鍵含 `bar_time`，狀態式判定會每根發一次。
3. **資料不足必須明確不發**——**禁止**沿用 `calculate_regime_filter` 的
   `min_periods=1`（`ladder_system.py:463`），那會由 30 根日線算出一條假年線。

## Technical Context

**Language/Version**: Python 3.10+（CI 矩陣 3.10 / 3.12）

**Primary Dependencies**: pandas（既有）。**不引入新依賴**。

**Storage**: 無 schema 變更。讀取既有 `stock_*_daily`；去重沿用既有 `sent_alerts`
表（`monitor_signals.py:43-51`），不新增資料表、不改主鍵。

**Testing**: pytest。新增 `tests/test_ma_lines.py`（純函式）與
`tests/test_ma_alerts.py`（monitor 整合，含既有告警不變的回歸）。
既有 234 passed / 1 skipped 須維持。

**Target Platform**: 本機 CLI（`monitor_signals.py --once`）＋ GitHub Actions
（`alert_scheduler.yml`，交易時段每 30 分鐘）＋ Streamlit（`app.py`）。

**Project Type**: 單一 Python 專案（扁平結構）

**Performance Goals**: 每標的每輪多一次 DB 讀取（約 2,500 列日線）＋ 四次
`rolling().mean()`。輪詢間隔 30 分鐘，成本可忽略。憲章 IV 無疑慮。

**Constraints（本案特有）**:

- **混合時基**：均線來自日線（已收盤、前一交易日為止），比較價來自 5 分線
  （最新已收盤棒）。兩者時框不同是**刻意設計**，不是缺陷——使用者要知道的是
  「現在跌到均線了」，而非「昨天收在均線下」。
- **均線的資料新鮮度依賴既有 ingestion**（`daily_ingestion.yml:13`，每交易日
  17:00 台北）。盤中判定時 DB 最新日線為前一交易日——這正是均線該有的基準。
- **不進入訊號或回測路徑**：`backtester.py`、`ladder_system.py` 的訊號判定
  零改動，回測結果不受任何影響。故本案**不需要**前後回測對照與消融
  （與 012／013 的性質差異）。
- **SC-013 需真實資料**，其餘 SC 皆可離線驗收（見「驗收環境切分」）。

**Scale/Scope**: 新增 1 個模組 + 2 個測試檔；修改 4 個既有檔案
（`monitor_signals.py`、`config/config.py`、`config/config.yaml`、`app.py`）。
`backtester.py`、`ladder_system.py`、`trading_costs.py`、`performance.py`
**零改動**。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 判定 | 依據 |
|------|------|------|
| I 看前偏誤（NON-NEGOTIABLE） | ✅ PASS（**不適用但仍守**） | 本案是通知層、不產生交易訊號，故嚴格說無「回測看前偏誤」可言。但仍守同一紀律：均線只用**已收盤**日線（不含當日進行中），比較價取 `select_closed_bar_indices` 選出的已收盤棒（既有 repaint 防禦，`monitor_signals.py:94-106`）。無 rolling 結構參與訊號判定，故無 `.shift(1)` 適用面。 |
| II 摩擦成本（NON-NEGOTIABLE） | ✅ PASS（不適用） | 不產生交易、不計算績效、不展示任何報酬數字。 |
| III 規格↔測試 | ⚠️ PASS with `[MANUAL]` | SC-001~012 有 pytest 對應（見 [quickstart.md](quickstart.md)）；SC-013（實跑 monitor 觀察輸出）標註 `[MANUAL]`。FR-010 為範圍排除條款、刻意無對應 SC。 |
| IV 效能紀律 | ✅ PASS | 四次 `rolling().mean()` 為 pandas 向量化；每 30 分鐘一次、每標的一次 DB 讀取。無 `apply()`、無 Python 迴圈。 |
| V 組態集中 | ✅ PASS | 新參數集中於 `config/config.yaml` 新增的 `alerts` 區塊 + Pydantic schema。**不放進 `SingleStrategyParams`**——它們是通知偏好、不是策略參數（見 research.md D3）。 |
| VI 可重現/資料衛生 | ✅ PASS | 無新增產物、無新資料表、無資料契約變更。`sent_alerts` 沿用既有主鍵。 |

**Gate 結論**：無違反項。新增模組 `ma_lines.py` 屬新增抽象層，
依憲章 Governance 已填 Complexity Tracking（見文末）。

**Post-Design 複查（Phase 1 後）**：設計未新增依賴、未改動任何既有函式簽名、
未新增目錄。上表判定不變。需持續盯的三點：

1. **FR-008（最高風險）**：實作時若把 `check_new_signals` 的現貨取數改成日線，
   既有六種告警的行為會全部改變。新的 DB 讀取必須是**額外**的一段，
   不得取代 `monitor_signals.py:167` 的 5 分線 fetch。SC-001 專門守門。
2. **FR-006**：`min_periods=1` 在本 repo 有先例（`ladder_system.py:463`），
   實作者極可能「照抄既有寫法」而引入假均線。SC-005 專門守門。
3. **FR-004**：穿越判定需要「前一個比較價」。若誤用「前一日收盤」作為前值，
   跳空跌破會判失敗。前值應取自同一條 5 分線序列的前一根已收盤棒
   （與既有三關價判定同源，`monitor_signals.py:230`）。

## Project Structure

### Documentation (this feature)

```text
specs/014-ma-touch-alerts/
├── plan.md              # 本檔
├── research.md          # Phase 0：五個設計決策與被否決的替代方案
├── data-model.md        # Phase 1：均線組、穿越事件、去重鍵、參數定義
├── quickstart.md        # Phase 1：驗收步驟與 SC↔測試對照
├── contracts/
│   └── ma-alerts.md     # ma_lines 元件契約 + monitor/app 接線契約
├── checklists/
│   └── requirements.md  # 規格品質檢查（已全項通過）
├── spec.md
└── tasks.md             # Phase 2 輸出（/speckit-tasks，本命令不產生）
```

### Source Code (repository root)

```text
ma_lines.py             # 【新模組】compute_ma_set()：由日線收盤價算四條 SMA，
                        #   資料不足者回傳 None（不補 min_periods）
                        # detect_cross_below()：前值/現值 vs 均線 → 穿越事件清單
                        # 純函式、無 I/O、不 import monitor/backtester

monitor_signals.py      # check_new_signals 現貨分支**尾端新增**一段均線判定：
                        #   讀 stock_*_daily → compute_ma_set → detect_cross_below
                        #   → 逐條推播（去重鍵 bar_time = 交易日）
                        # 既有 5 分線取數（:167）與六種告警**完全不動**

config/config.py        # 新增 MaAlertConfig 模型 + SystemConfig.alerts 欄位
config/config.yaml      # 新增 alerts 區塊（總開關預設 false、四條線各自開關與週期）
app.py                  # 單一標的檢視新增「均線現況」表（US4/FR-013）

backtester.py           # 【零改動】
ladder_system.py        # 【零改動】——本案不新增指標、不動任何訊號判定
trading_costs.py        # 【零改動】
performance.py          # 【零改動】

tests/
├── test_ma_lines.py    # 新檔：純函式（SC-002/003/005/006）
└── test_ma_alerts.py   # 新檔：monitor 整合 + 既有告警回歸（SC-001/004/007~012）
```

**Structure Decision**: 沿用扁平單一專案結構，不新增目錄。均線計算刻意**不**放進
`ladder_system.py`——該檔是「交易訊號的指標組裝入口」，其對外契約（spec 004
的前綴一致性、無狀態）為回測服務；本案的均線是**通知用的參考價位**，
與訊號路徑無關。混入會擴大該入口的職責並讓「這條均線有沒有進訊號」變得不明確。

## 關鍵設計決策摘要

完整論證見 [research.md](research.md)，此處列出對實作最有約束力的四條：

1. **均線讀日線 DB，比較價用 5 分線——兩條路徑並存**。新增的是一段**額外**邏輯，
   不是既有取數的替換。這是 FR-008 的落點，也是本案最高風險。

2. **穿越判定的「前值」取自同一條 5 分線序列的前一根已收盤棒**，
   與既有三關價判定同源（`monitor_signals.py:230` 的 `prev_bar`）。
   若改用「前一日收盤」，開盤跳空跌破會判定失敗。

3. **去重鍵的 `bar_time` 填入交易日（date），而非 5 分線棒的時間戳**。
   這使「每標的每線每交易日至多一則」由既有主鍵天然保證，
   零 schema 變更、零額外狀態。

4. **資料不足時回傳 `None` 而非 NaN**。`None` 迫使呼叫端顯式處理，
   NaN 則會靜默地讓比較恆為 False——後者「看起來剛好正確」，
   但那是實作巧合（本 repo 已有此教訓，`ladder_system.py:645-649`）。

## 驗收環境切分

| 段 | 內容 | 驗收條件 | 本環境可否 |
|---|---|---|---|
| **A. 離線可完成** | `ma_lines.py`、monitor 接線、config、app.py 面板、全部測試 | SC-001~SC-012（`pytest -q` 全綠；合成資料即足） | ✅ 可 |
| **B. 需真實資料** | 實跑觀察輸出格式與數值合理性 | SC-013（`[MANUAL]`） | ❌ 需本機 |

**A 段用合成資料比真實資料更好**：本案的核心測試都需要**精確控制**價格與均線的
相對位置——「自上方跌破」「持續低於」「同日反覆穿越」「僅 100 根日線」，
真實資料無法保證這些情境一定出現在測試窗內。

**A 段涵蓋率高於 012／013**：本案不需要真實資料來裁決「功能有沒有用」
（它是通知功能，不是策略），B 段僅剩「實跑看一眼輸出長相」。

## Complexity Tracking

> 憲章 Governance 要求：新增抽象層須回答「為何更簡單的做法不可行」。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 新增模組 `ma_lines.py` | 兩個需獨立單元測試的純函式：均線計算（含資料不足的判定）與穿越偵測。SC-002/003/005/006 皆針對它們，寫成純函式測試比透過整支 monitor 觸發便宜且穩定得多 | **內聯於 `monitor_signals.py`**：兩個函式將只能透過 monitor 間接測試，需要 mock DB、mock 推播管道、mock 時鐘，測試成本與脆弱度都高得多。**放 `ladder_system.py`**：該檔語意為「交易訊號的指標組裝」，其對外契約為回測服務（spec 004 前綴一致性）；通知用的參考價位混入會擴大職責，且讓「這條均線有沒有進訊號」變得不明確——而答案必須是明確的「沒有」 |
