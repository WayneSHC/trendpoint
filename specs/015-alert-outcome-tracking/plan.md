# Implementation Plan: 推播訊號的事後表現追蹤（A 段：日線視窗）

**Branch**: `claude/trendpoint-video-analysis-nc8f4l`（spec 目錄 `015-alert-outcome-tracking`） | **Date**: 2026-08-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/015-alert-outcome-tracking/spec.md`

## Summary

新增純函式模組 `alert_outcomes.py`（參數識別值、紀錄組裝、upsert 合併、
前瞻報酬計算、分群統計）＋薄儲存層（JSONL 讀寫）。
在 `monitor_signals.check_new_signals` 的**七個告警分支**各插入一次紀錄收集，
於函式尾端一次寫回版本庫內的 `alert_log/YYYY-MM.jsonl`；
每輪監控開始時順帶回填已到期的 T+1／T+3／T+5 收盤報酬。
`app.py` 新增第五個唯讀分頁呈現分群分布。

本案的實作重心不在演算法（比大小與除法），而在**四條不可越界的線**：

1. **不新增 SQLite 表**（research.md D1）。JSONL 是單一真實來源。
   出現「要放寬 `db_security.py:19` 的 `TABLE_NAME_PATTERN`」的念頭，
   即代表偏離設計。
2. **不重構既有七個告警分支**（D4）。它們的重複是刻意留下的；
   SC-001 要求開關關閉時逐筆逐則逐欄相同，重構會讓該保證的驗證成本暴增。
3. **記錄點在去重判定之前，不在 `mark_alert_as_sent` 內**（D4）。
   後者只在推播成功時被呼叫，語意是「已通知使用者」（`alerts.py:137`）——
   放進去等於讓推播失敗的訊號永遠不被記錄。
4. **本模組永不進入訊號鏈**（FR-021）。它持有告警**發生之後**的價格，
   任何被回測或訊號模組 import 的路徑都是未來函數的入口。

## Technical Context

**Language/Version**: Python 3.10+（CI 矩陣 3.10 / 3.12）

**Primary Dependencies**: pandas（既有）、標準庫 `json`／`os`。**不引入新依賴**。

**Storage**: **不新增 SQLite 資料表**、不改 `sent_alerts` 主鍵
（`monitor_signals.py:44-51`）、不改任何行情表的資料契約。
唯一新增的持久化產物是版本庫內的 `alert_log/YYYY-MM.jsonl`（月分片、
原子寫入、排序後寫回使 diff 穩定）。回填讀取既有 `stock_*_daily` /
`fut_*_daily`，走既有 `safe_load_db_data`（`db_security.py:61`）。

**Testing**: pytest。新增 `tests/test_alert_outcomes.py`（純函式與 schema／
靜態檢查）與 `tests/test_alert_outcomes_monitor.py`（monitor 整合＋既有告警
不變的回歸，基準凍結於 fixture，比照 `tests/fixtures_014_baseline_alerts.json`）。

**Target Platform**: 本機 CLI（`monitor_signals.py --once` / `--backfill-only`）
＋ GitHub Actions（`alert_scheduler.yml`，交易時段每 30 分鐘）
＋ Streamlit（`app.py`）。

**Project Type**: 單一 Python 專案（扁平結構）

**Performance Goals**: 每輪多一次 JSONL 讀取（單月分片數十 KB）＋
每標的一次日線表讀取（回填時；該表本就在監控路徑上）。
輪詢間隔 30 分鐘，成本可忽略。憲章 IV 無疑慮。

**Constraints（本案特有）**:

- **產出不是策略績效**。無成本模型、無出場規則、未經樣本外驗證。
  這是刻意的，且由 FR-017／SC-016 的標示與「不得與回測 KPI 並列」承擔。
  見 Constitution Check 原則 II 一列——**這是本案最可爭議的閘門判定**。
- **持久化路徑不得置於 `data/`**。該目錄整體 gitignored，理由為
  「Yahoo Finance 資料公開再散布有授權疑慮」。本紀錄含價格快照，
  與該疑慮同源（規模與性質不同，spec Assumptions A-1）。
- **排程環境需 `contents: write`**。現行 `alert_scheduler.yml` 未宣告
  `permissions`，實作時須顯式加上（A-9）。commit 訊息須含 `[skip ci]`，
  否則每次告警都會觸發 `tests.yml`。
- **不進入訊號或回測路徑**：`backtester.py`、`ladder_system.py` 零改動，
  回測結果不受任何影響。故本案**不需要**前後回測對照與消融
  （與 spec 012／013 的性質差異，同 spec 014）。
- **樣本頻率未知**（A-6）：本案最大的不確定性，由 SC-022 於實作後量測。

**Scale/Scope**: 新增 1 個模組 + 2 個測試檔 + 1 個資料目錄；
修改 5 個既有檔案（`monitor_signals.py`、`config/config.py`、
`config/config.yaml`、`app.py`、`.github/workflows/alert_scheduler.yml`）。
`backtester.py`、`ladder_system.py`、`ma_lines.py`、`alerts.py`、
`db_security.py`、`trading_costs.py`、`performance.py`、`risk_gates.py`
**零改動**（完整清單見 [contracts/alert-outcomes.md](contracts/alert-outcomes.md) §6）。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 判定 | 依據 |
|------|------|------|
| I 看前偏誤（NON-NEGOTIABLE） | ✅ PASS（**不適用，但本案是該原則的守衛對象**） | 本案不產生交易訊號、不參與回測，故無 `.shift(1)` 適用面。但它**持有告警發生之後的價格**——這正是原則 I 要防的東西。故以 FR-021／SC-019 的**靜態零引用檢查**焊死：任何訊號或回測模組 import 本模組即測試失敗。這比人工審查強，因為違反是自動被抓到的 |
| II 摩擦成本（NON-NEGOTIABLE） | ⚠️ **PASS（附條件）——本案最可爭議的判定** | 原則 II 禁止「展示或提交零成本的績效數字」。本案輸出**確為**零成本的報酬數字。判定為 PASS 的理由：原則 II 的適用對象是「回測與績效報告」，而本案輸出的是**原始價格變化的觀察樣本**，不宣稱可執行、不含出場規則、基準價與衡量價甚至不同時基（A-3）。**條件**：FR-017／SC-016 必須落實——明確標示非策略績效，且不得與回測 KPI 並列。條件未落實即應視為違反。詳見 Complexity Tracking |
| III 規格↔測試 | ⚠️ PASS with `[MANUAL]` | SC-001~021 皆有 pytest 對應（對照見 [quickstart.md](quickstart.md) §2）；SC-022／023 標註 `[MANUAL]`（需真實資料與時間累積）。FR-009／SC-010 的措辭已於 Phase 0 精確化並**同步修訂 spec**，非沉默漂移（research.md D8） |
| IV 效能紀律 | ✅ PASS | 每輪一次數十 KB 的 JSONL 讀寫 + 既有日線表讀取。前瞻報酬為向量化的位置索引與除法，無 `apply()`、無 Python 迴圈於熱路徑。輪詢間隔 30 分鐘 |
| V 組態集中 | ✅ PASS | 新參數集中於既有 `alerts` 區塊下的 `outcome_tracking` 子區塊 + Pydantic schema（data-model.md §5）。**不放進 `SingleStrategyParams`**——它們是觀察層設定、不是策略參數（同 spec 014 research.md D3 的判準）。無硬編碼，SC-018 守門 |
| VI 可重現/資料衛生 | ✅ PASS（**附論證**） | 原則 VI 要求「可再生成的產物一律 gitignore」。本紀錄**不可再生成**——它是時點觀察，錯過即永久遺失，性質屬「原始輸入」，故追蹤於版本庫與原則 VI **一致**，非例外（spec A-2）。無新資料表、無資料契約變更、無新依賴 |
| 安全與運維約束 | ✅ PASS | 不新增 SQLite 表 ⇒ 無新 SQL 注入面；回填走既有 `safe_load_db_data`。憑證不進紀錄（FR-023 欄位白名單，SC-020 守門）。既有告警去重機制**不動** |

**Gate 結論**：無 NON-NEGOTIABLE 違反。原則 II 為附條件通過，
新增模組 `alert_outcomes.py` 屬新增抽象層，兩者皆已填 Complexity Tracking。

**Post-Design 複查（Phase 1 後）**：設計未新增依賴、未改動任何既有函式簽名、
未新增 SQLite 表。上表判定不變。需持續盯的四點：

1. **FR-019／SC-001（最高風險）**：實作時若順手合併七個告警分支的重複，
   或改動 `monitor_signals.py:167` 的 5 分線取數、`:194-199` 的
   `build_indicator_frame` 呼叫，既有告警行為會全部改變。SC-001 專門守門，
   基準須先凍結為 fixture 再開始改。
2. **FR-001／SC-003**：記錄點極容易被放進 `mark_alert_as_sent`（那裡「看起來」
   就是記錄的地方）。放進去 ⇒ 推播失敗的訊號永不被記錄。
3. **FR-014／SC-014**：回填時把「未到期」寫成 `0.0` 會讓分布出現大量假零。
   `null` 與 `0.0` 必須在序列化後仍可區分。
4. **FR-017／SC-016**：原則 II 的附條件即在此。UI 分頁若與回測 KPI 視覺並列，
   附條件不成立，該閘門判定應改為違反。

## Project Structure

### Documentation (this feature)

```text
specs/015-alert-outcome-tracking/
├── plan.md                    # 本檔
├── research.md                # Phase 0：D1~D10 設計決策與被否決的替代方案
├── data-model.md              # Phase 1：紀錄結構、三態、參數識別值、組態、狀態轉移
├── quickstart.md              # Phase 1：驗收步驟、SC↔測試對照、五個易踩的坑
├── contracts/
│   └── alert-outcomes.md      # 模組契約 + monitor/app/config/workflow 接線契約
├── checklists/
│   └── requirements.md        # 規格品質檢查（已全項通過）
├── spec.md
└── tasks.md                   # Phase 2 輸出（/speckit-tasks，本命令不產生）
```

### Source Code (repository root)

```text
alert_outcomes.py       # 【新模組】純函式核心：
                        #   build_fingerprint() 參數識別值（正規字串，禁用內建 hash）
                        #   make_record()       紀錄組裝（欄位白名單）
                        #   merge_record()      upsert 合併（notified 單向升級）
                        #   compute_outcomes()  前瞻報酬（交易日對齊、三態、冪等）
                        #   summarize()         分群統計（供 UI，樣本不足仍列出）
                        # 薄 I/O：load_month / upsert_records / load_all
                        #   月分片、排序後寫回、暫存檔+os.replace 原子置換
                        #   零變更即零寫入（FR-009）
                        # 不 import monitor_signals / backtester / ladder_system

alert_log/              # 【新目錄】JSONL 月分片，**進版本庫**（非 gitignore）
└── YYYY-MM.jsonl       #   不得置於 data/（該目錄整體 gitignored）

monitor_signals.py      # 七個告警分支各插入一次紀錄收集（去重判定**之前**）
                        # send_alert 成功後標記 notified=True（與 mark_alert_as_sent 同處）
                        # 函式尾端一次寫回；輪詢開始時順帶回填
                        # 新增 --backfill-only 旗標（只回填、不取數、不推播）
                        # 既有 5 分線取數(:167)、指標組裝(:194-199)、
                        #   已收盤棒選取(:207-212)、七種告警判定與訊息字串 **完全不動**

config/config.py        # 新增 OutcomeTrackingConfig，掛於既有 alerts 模型下
config/config.yaml      # alerts 區塊新增 outcome_tracking 子區塊（預設 enabled: false）
app.py                  # 四分頁(:621)擴為五：新增「訊號事後表現」唯讀分頁
                        #   只呼叫 summarize() 呈現，不內嵌演算法（CLAUDE.md UI 規則）

.github/workflows/alert_scheduler.yml
                        # 新增 permissions: contents: write
                        # 推播後新增 commit 步驟（僅在 alert_log/ 有變更時、含 [skip ci]）

ladder_system.py        # 【零改動】
backtester.py           # 【零改動】——回測結果不受任何影響
db_security.py          # 【零改動】——不新增 SQLite 表，TABLE_NAME_PATTERN 不得放寬
ma_lines.py             # 【零改動】
alerts.py               # 【零改動】

tests/
├── test_alert_outcomes.py          # 新檔：純函式、schema、靜態零引用檢查
├── test_alert_outcomes_monitor.py  # 新檔：monitor 整合 + 既有告警回歸
└── fixtures_015_baseline_alerts.json  # 新檔：開關關閉時的凍結基準
```

**Structure Decision**: 沿用扁平單一專案結構。紀錄邏輯刻意**不**放進
`ladder_system.py`（該檔是訊號指標組裝入口，對外契約為回測服務）、
也不放進 `ma_lines.py`（那是通知用參考價位的純函式）。
`alert_outcomes.py` 是**觀察層**——它讀的是告警發生**之後**的價格，
與前兩者的資料方向相反，混入任何一個都會讓「這個值有沒有進訊號」變得不明確，
而答案必須是明確的「沒有」。

## 關鍵設計決策摘要

完整論證見 [research.md](research.md)，此處列出對實作最有約束力的五條：

1. **不新增 SQLite 表，JSONL 為單一真實來源**（D1）。雙儲存體必然發散，
   且「先寫進快取再想辦法搬出來」正是本案要解決的問題本身。
   單儲存體使 SC-009 由**結構保證**而非測試保證。

2. **記錄點在去重判定之前、不重構既有分支**（D4）。前者是 FR-001 的落點，
   後者是 SC-001 的前提。兩者都容易在實作時「順手」違反。

3. **參數識別值用可讀正規字串而非雜湊**（D5）。8 個參數、約 40 字元、自我說明。
   若日後改用雜湊，**必須走 `hashlib`**——內建 `hash()` 對 `str` 有
   per-process 隨機化，跨輪次不穩定，且同行程內的測試抓不到。

4. **前瞻視窗以「日線表中日期嚴格大於告警日的第 N 根」定義**（D6）。
   一條規則同時適用 5 分線與日線告警，且自動略過假日停牌，不需交易日曆。

5. **回填搭既有輪詢，不新增排程**（D7）。行情資料可重抓，回填晚幾天無損正確性。
   這是 FR-012 的落點，也讓本案的基礎設施增量為零。

## 驗收環境切分

| 段 | 內容 | 驗收條件 | 本環境可否 |
|---|---|---|---|
| **A. 離線可完成** | 模組、monitor 接線、config、app 分頁、workflow、全部自動化測試 | SC-001 ~ SC-021（`pytest -q` 全綠；合成資料即足） | ✅ 可 |
| **B. 需真實資料與時間** | 實跑累積、告警頻率量測 | SC-022／SC-023（`[MANUAL]`） | ❌ 需本機 + 一週 |

**A 段用合成資料優於真實資料**：核心測試需**精確控制**「假日缺口」
「T+5 尚未到期」「推播失敗」「同一根 K 線重複偵測」「已通知後被去重擋下」
等情境，真實資料無法保證這些一定出現在測試窗內。

**B 段的性質與 012／013 不同**：那兩案的 B 段是「決定要不要改為預設啟用」；
本案的 B 段是「**確認這個功能值不值得留著**」——若告警頻率低到樣本累積
不具意義（A-6），正確處置是據此收手，而非默默保留一個永遠湊不齊樣本的功能。

## Complexity Tracking

> 憲章 Governance 要求：新增抽象層或依賴須回答「為何更簡單的做法不可行」；
> 附條件通過的閘門須說明條件與其失效後果。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 新增模組 `alert_outcomes.py` | 五個需獨立單元測試的純函式（參數識別值、紀錄組裝、upsert 合併、前瞻報酬、分群統計），SC-006/007/011/013/014/015/017 皆直接針對它們。寫成純函式測試比透過整支 monitor 觸發便宜且穩定得多 | **內聯於 `monitor_signals.py`**：五個函式將只能間接測試，需 mock DB、mock 推播管道、mock 時鐘；且 FR-021 的靜態零引用檢查失去明確標的（無法斷言「沒有模組 import 它」）。**放 `ladder_system.py` 或 `ma_lines.py`**：兩者的資料方向是「告警之前」，本模組是「告警之後」，混入會讓「這個值有沒有進訊號」變得不明確 |
| 拆成「純函式」與「儲存」兩個模組 | — | **已否決**：I/O 僅讀檔／原子寫檔／讀日線表三件事，約 200 行的功能散成兩檔無對應收益 |
| 版本庫追蹤 `alert_log/`（資料檔進 repo） | FR-008 要求紀錄超出排程環境生命週期。排程環境的 DB 存活於 `actions/cache`（`alert_scheduler.yml:35-41`），有逐出機制；紀錄一旦遺失**不可再生成** | **artifact**：有保留期限、不能累加。**外部儲存**：新增外部相依與憑證，破壞目前「無外部狀態」的簡潔性。**留在快取**：等於不解決 FR-008 |
| 原則 II 附條件通過（展示零成本報酬數字） | 本案輸出的是原始價格變化的觀察樣本，非回測績效；加上成本模型反而會讓它**更像**績效數字，與原則 II 的意圖背道而馳 | **套用成本模型**：見上，反效果。**不展示任何數字**：則本案無存在意義。**條件**：FR-017／SC-016 必須落實（明確標示 + 不與回測 KPI 並列）；**條件失效即應視為違反該原則**，而非事後補標示 |
