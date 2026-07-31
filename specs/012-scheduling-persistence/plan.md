# Implementation Plan: 排程與持久化 as-built ＋ 累積紀錄遷移至託管儲存

**Branch**: `012-scheduling-persistence` | **Date**: 2026-07-31 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/012-scheduling-persistence/spec.md`

---

## Summary

把單一 `trendpoint.db` 拆成兩個生命週期獨立的儲存位置：**不可再生的累積紀錄**
遷入託管 libSQL，**可再生的行情資料**留在 GitHub Actions cache（不動）。
第一個租戶是推播去重紀錄——它今天就在承受快取回退造成的重複／漏推播，
故本案可獨立驗收，不依賴 spec 013 的帳。

技術路線由 Phase 0 研究收斂為三點：

1. **不讓累積紀錄依賴 pandas**。`libsql` 驅動為 0.1.11、2025-09-02 發版、
   DB-API 相容性未載明；只依賴文件明確記載的 `connect` / `execute(sql, params)` / `commit`，
   把驅動風險關進一個薄轉接層（research.md R1）。
2. **不動 `db_security`**。去重紀錄的 as-built 路徑本來就直接用 `sqlite3`、
   表名是固定字面值、SQL 已參數化——那道動態表名白名單在此無事可做（R2）。
3. **故障語意分兩層**：啟動期繫結 fail-fast（有憑證卻連不上就紅燈，不退化），
   執行期資料操作沿用 fail-open（憲章「不得漏發」）（R4）。

## Technical Context

**Language/Version**: Python 3.10+（CI 用 3.10；本機 venv 為 3.13）

**Primary Dependencies**: `libsql`（新增，0.1.x）、`sqlite3`（標準庫，本機退化路徑）、
`pydantic`（組態驗證，既有）。**刻意不新增** SQLAlchemy——本案不需要 ORM 或方言抽象。

**Storage**: 累積紀錄＝託管 libSQL（有憑證）／本機 SQLite 檔（無憑證）；
行情＝`trendpoint.db` on Actions cache（不變）

**Testing**: pytest。託管路徑以注入式替身在無憑證環境覆蓋；
真實端點的那一次執行屬人工確認（見下方「驗收有賞味期」）

**Target Platform**: GitHub Actions（ubuntu-latest）＋開發者本機（macOS）

**Project Type**: 單一 Python 專案（既有扁平結構，非 src/ layout）

**Performance Goals**: 不適用。累積紀錄每日數筆、年增量以千筆計；
本案不在任何熱路徑上（憲章 IV 的向量化紀律與本案無交集）

**Constraints**: 免費層額度充裕（現行整庫 3.4 MB／52,972 列，其中去重紀錄 1 列）；
閒置封存政策存在但每交易日皆有存取，正常不觸發

**Scale/Scope**: 6 個標的、每日至多數十筆去重紀錄；spec 013 起加入帳（每日 6 筆量級）

## Constitution Check

*GATE: 通過。三項需要說明，其中一項要求後續修憲。*

| 原則 | 判定 | 說明 |
|---|---|---|
| I. 防禦看前偏誤 | **不適用** | 本案不觸碰訊號判定、指標計算或成交價決定。`monitor_signals` 的 `select_closed_bar_indices`（repaint 防禦）不在變更面內 |
| II. 真實摩擦成本 | **不適用** | 不觸碰回測或績效計算 |
| III. 規格↔測試對應 | **通過** | 6 條 SC 皆有驗收方式；SC-006 標 `[MANUAL]`（見 quickstart.md 對照表）。SC-001／003／004 的機制以注入式測試覆蓋 |
| IV. 效能紀律 | **不適用** | 非熱路徑 |
| V. 組態集中化 | **通過** | 本機檔路徑與連線參數經 `config/config.yaml` + Pydantic（FR-012）。**憑證本身不進組態檔**，只走環境變數／Secrets |
| VI. 可重現性與資料衛生 | **通過，但需注意** | 見下方 V1 |
| Security：憑證只走環境變數／Secrets | **通過** | FR-011。token 不得落入日誌、指令字串、快照或錯誤訊息 |
| Security：參數化 SQL | **通過** | 全部語句為參數化字面值 SQL（C2）。不引入 `db_security` 的理由見 R2 |
| Security：去重機制 | **超出要求** | 見下方 V2 — **需後續修憲** |
| Workflow：Spec Kit 流程 | **通過** | 本案即走此流程；as-built 部分是對「先前未走流程」的補償 |
| Workflow：`pytest` 全綠 | **通過** | 硬性關卡 |
| Workflow：訊號邏輯變更附回測對照 | **不適用** | 不改訊號邏輯 |
| Workflow：UI 不含演算法 | **不適用** | 不動 `app.py` |

### V1：快照提交進 repo 與憲章 VI 的關係

憲章 VI 要求「版本庫只追蹤原始輸入與程式碼／規格；所有可再生成的產物一律 gitignore」。

快照**不是可再生成的產物**——它正是「不可再生」的那一份資料，這是本案存在的理由。
把它提交進 repo 不違反該原則的意圖（避免追蹤會腐化的可再生物），
而是該原則的另一面：不可再生的東西需要被追蹤。

但為避免後人誤讀，`.gitignore` 中 `trendpoint.db` 的排除 MUST 保持不變
（行情庫仍是可再生產物），且快照 MUST 落在與行情庫明確區隔的路徑。

### V2：憲章去重條款需修訂（不在本案範圍）

憲章 Security 節現行文字允許「快取失效時的重複告警視為可接受的降級行為」。
本案 SC-001 要求重複與漏發皆為 0，**嚴於憲章**——不構成違憲。

但該豁免是在「快取是唯一持久化機制」的前提下寫的。本案落地後前提消失，
那句話會變成一條誤導後人的過期豁免，屬憲章 III 所禁止的沉默漂移。

**處置**：憲章 Governance 明定修訂需以獨立 PR 提出，故本案 MUST NOT 夾帶修憲。
於 012 合併後另開 PR 修訂該條款。（詳見 research.md R3）

## Project Structure

### Documentation (this feature)

```text
specs/012-scheduling-persistence/
├── plan.md              # 本檔
├── spec.md              # /speckit-specify 產出
├── research.md          # Phase 0：R1-R5
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1：驗收指引
├── contracts/
│   └── append-only-store-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2（/speckit-tasks 產出，本命令不建立）
```

### Source Code (repository root)

沿用既有扁平結構，不引入 `src/` layout。變更面刻意極小：

```text
# 新增
append_only_store.py            # 轉接層：AppendOnlyStore protocol + 託管/本機兩實作
                                # + 啟動期繫結（fail-fast）+ 全量匯出（供快照）
snapshot.py                     # 快照產生、內容摘要比對、還原

tests/
├── test_append_only_store.py   # 繫結狀態機、fail-fast/fail-open 分界、參數化 SQL
├── test_dedup_migration.py     # C1 去重鍵逐字不變、遷移前後逐列比對
└── test_snapshot.py            # 時序、去重、還原逐筆一致、失敗即非零

# 修改
monitor_signals.py              # 三個去重函式改走轉接層（行 37-92）
                                # 啟動期繫結置於任何訊號判定之前
config/config.yaml + schema     # 本機檔路徑與連線參數（FR-012；憑證不進此處）
.github/workflows/
├── alert_scheduler.yml         # 注入 Secrets；快照步驟；持久化失敗即紅燈
└── daily_ingestion.yml         # 同上（行情 cache 步驟不動，FR-004）

# 明確不動
db_security.py                  # R2：本路徑不經過它；階段二才會用到
keepalive.yml                   # R5：保活職責變冗餘，但健康檢查職責仍有價值
app.py / backtester.py / ladder_system.py / data_sources/  # 不在變更面
```

**Structure Decision**：兩個新模組、一個修改的呼叫端、兩條工作流。
不建立套件目錄——本專案既有 24 個頂層模組皆為扁平佈局，為兩個檔案引入
套件結構會與既有慣例不一致，且無實際收益。轉接層與快照分開是因為前者是
執行期依賴、後者是工作流層職責，混在一起會讓「快照必須在寫入之後」
這個時序約束變得不明顯。

## Complexity Tracking

> 憲章 Governance 要求：新增抽象層須回答「為何更簡單的做法不可行」。

| 新增 | 為何需要 | 更簡單的做法為何不可行 |
|---|---|---|
| `AppendOnlyStore` 轉接層（一個 protocol、兩個實作） | 同一份呼叫端邏輯要能跑託管與本機兩種儲存（FR-005／US2） | 「直接把 `libsql` 當 `sqlite3` 用」看似最簡，但驅動的 DB-API 相容性未載明（R1），且無憑證時測試就必須連外部服務，違反憲章 III 的可測試要求 |
| `snapshot.py` 獨立模組 | 快照的時序約束（MUST 在所有寫入之後）與去重（內容相同不重複提交）是獨立職責 | 內嵌進轉接層會讓時序約束隱形；內嵌進工作流 YAML 則無法以 pytest 覆蓋 |
| 新依賴 `libsql` | 託管儲存的唯一官方 Python 途徑 | 走 HTTP API 可免依賴，但要自行處理序列化／重試／型別對應，且失去 SQLite 方言帶來的階段二遷移優勢（ADR 0002 的核心理由） |

**刻意不做的簡化以外的事**（範圍護欄）：

- 不搬行情資料（階段二，issue #41）
- 不建帳的表結構（spec 013）
- 不引入鎖或交易隔離（現行排程時段不重疊，寫入頻率每日數筆）
- 不引入 SQLAlchemy
- 不改任何訊號判定、訊息格式或推播管道

---

## 驗收有賞味期（給實作階段的提醒）

真實端點的一次綠燈只證明「當下那個回應」。`libsql` 0.1.x 的行為若日後改變，
或託管服務的連線語意變動，靠一次人工驗收不會被發現。

因此格式與行為契約 MUST 靠**離線替身／fixture** 鎖住（C2／C4 的每一條都要有
對應的注入式測試），真實端點驗收只作為「這一刻確實接通」的補充證據，
不得作為唯一防線。此教訓來自 010 的驗收經驗（TAIFEX 曾在驗收當天稍晚改格式）。
