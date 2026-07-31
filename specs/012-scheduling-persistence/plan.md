# Implementation Plan: 排程與持久化 as-built ＋ 帳遷移至 repo 內純文字檔

**Branch**: `012-scheduling-persistence` | **Date**: 2026-07-31 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/012-scheduling-persistence/spec.md`

---

## Summary

把單一 `trendpoint.db` 拆成兩個生命週期獨立的儲存位置：**不可再生的帳**
成為 repo 內按月分割的 JSONL（`ledger/YYYY-MM.jsonl`），
**可再生的行情資料**留在 GitHub Actions cache（不動）。
第一個租戶是推播去重紀錄——它今天就在承受快取回退造成的重複／漏推播，
故本案可獨立驗收，不依賴 spec 013 的影子部位。

**Phase 0 推翻了原本的託管資料庫方案**（ADR 0002 → [ADR 0004](../../docs/adr/0004-ledger-as-repo-text-file.md)）。
三條證據：驅動為 0.1.11／11 個月未發版／DB-API 相容性未載明；`db_security`
從未在這條路徑上，故「SQLite 方言」的論據不成立；且原方案已把「快照 commit 進 repo」
列為必要項——那本身就是一份耐久的帳，託管層只是疊在上面的工作副本。
加上量級事實：去重紀錄目前**總共 1 列**。

技術路線因此收斂為：

1. **無資料庫、無驅動、無憑證**。帳是純文字檔，讀寫只需標準庫（research.md R1）。
2. **不動 `db_security`**。去重路徑本來就直接用 `sqlite3`、表名是固定字面值、
   SQL 已參數化；改為純文字後連 SQL 都不涉及（R2）。
3. **故障語意不對稱**：同步階段 fail-fast（推送重試耗盡即紅燈），
   讀取階段沿用 fail-open（憲章「不得漏發」）（R4）。

## Technical Context

**Language/Version**: Python 3.10+（CI 用 3.10；本機 venv 為 3.13）

**Primary Dependencies**: **無新增**。帳的讀寫只用標準庫（`json`、`pathlib`）；
同步用 `git` CLI。`pydantic`（組態驗證）為既有依賴。
**刻意不新增**任何資料庫驅動——`requirements.txt` 若出現 `libsql` 之類的項目，
即代表實作偏離設計。

**Storage**: 帳＝`ledger/YYYY-MM.jsonl`（受版本控制）；
行情＝`trendpoint.db` on Actions cache（不變）

**Testing**: pytest。同步行為以注入式替身覆蓋（模擬衝突、推送失敗、併發追加），
不需外部服務；CI 上的實際執行屬人工確認

**Target Platform**: GitHub Actions（ubuntu-latest）＋開發者本機（macOS）

**Project Type**: 單一 Python 專案（既有扁平結構，非 src/ layout）

**Performance Goals**: 不適用。帳每日數筆、年增量以千筆計；
本案不在任何熱路徑上（憲章 IV 的向量化紀律與本案無交集）

**Constraints**: 帳必須是純文字（`.gitignore` 已排除 `*.db`／`*.sqlite3`，
且只有純文字才有 `git log -p` 的稽核價值）；CI 需 `contents: write`；
checkout 深度須足以 rebase

**Scale/Scope**: 6 個標的；去重紀錄目前 1 列，spec 013 起每日約 6 筆

## Constitution Check

*GATE: 通過。三項需要說明，其中一項要求後續修憲。*

| 原則 | 判定 | 說明 |
|---|---|---|
| I. 防禦看前偏誤 | **不適用** | 不觸碰訊號判定、指標計算或成交價決定。`select_closed_bar_indices`（repaint 防禦）不在變更面內 |
| II. 真實摩擦成本 | **不適用** | 不觸碰回測或績效計算 |
| III. 規格↔測試對應 | **通過** | 7 條 SC 皆有驗收方式；SC-006 標 `[MANUAL]`。SC-001／004／007 的機制以注入式測試覆蓋（見 quickstart.md 對照表） |
| IV. 效能紀律 | **不適用** | 非熱路徑 |
| V. 組態集中化 | **通過** | 帳的路徑與推送重試上限經 `config/config.yaml` + Pydantic（FR-012） |
| VI. 可重現性與資料衛生 | **通過，但需注意** | 見下方 V1 |
| Security：憑證只走環境變數／Secrets | **通過（且需求消失）** | 本案不需要任何 Secrets。帳的內容 MUST NOT 含 token（FR-011） |
| Security：參數化 SQL | **不適用** | 帳的路徑不涉及 SQL（R2）。行情路徑的既有防護不動 |
| Security：去重機制 | **超出要求** | 見下方 V2 — **需後續修憲** |
| Workflow：Spec Kit 流程 | **通過** | 本案即走此流程；as-built 部分是對「先前未走流程」的補償 |
| Workflow：`pytest` 全綠 | **通過** | 硬性關卡 |
| Workflow：訊號邏輯變更附回測對照 | **不適用** | 不改訊號邏輯 |
| Workflow：UI 不含演算法 | **不適用** | 不動 `app.py` |

### V1：帳提交進 repo 與憲章 VI 的關係

憲章 VI 要求「版本庫只追蹤原始輸入與程式碼／規格；所有可再生成的產物
（回測 CSV、SQLite 資料庫、日誌檔）一律 gitignore」。

帳**不是可再生成的產物**——它正是「不可再生」的那一份資料，這是本案存在的理由。
追蹤它不違反該原則的意圖（避免追蹤會腐化的可再生物），而是該原則的另一面：
不可再生的東西必須被追蹤。

為避免後人誤讀，`.gitignore` 中 `trendpoint.db`／`*.db` 的排除 MUST 保持不變
（行情庫仍是可再生產物），且帳落在與行情庫明確區隔的 `ledger/` 路徑。

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
ledger_store.py                 # JSONL 讀寫、月檔分割、追加語意（同鍵以最後一列為準）、
                                # has_pending 判定。只用標準庫。
ledger_sync.py                  # SyncMode 判定（ci/local）、pull --rebase / commit / push
                                # 重試、重試耗盡即非零。呼叫 git CLI。

tests/
├── test_ledger_store.py        # 追加語意、跨月邊界、同鍵最後一列為準、只追加不重寫
├── test_ledger_sync.py         # SyncMode 狀態機、衝突重試、重試耗盡即失敗、
│                               # local 模式不 push、無新紀錄不提交
└── test_dedup_migration.py     # C1 去重鍵逐字不變、遷移前後逐列比對

# 修改
monitor_signals.py              # 三個去重函式（行 37-92）改走 ledger_store；
                                # SyncMode 判定置於任何訊號判定之前
config/config.yaml + schema     # 帳路徑、推送重試上限（FR-012）
.github/workflows/
├── alert_scheduler.yml         # permissions: contents: write；帳同步步驟；
│                               # 同步失敗即紅燈。行情 cache 步驟不動（FR-004）
└── daily_ingestion.yml         # 同上
.gitignore                      # 確認 ledger/ 未被排除；*.db 的排除保持不變

# 明確不動
db_security.py                  # R2：本路徑不經過它；階段二才會用到
keepalive.yml                   # 仍然必要——帳的提交過於稀疏，撐不住 60 天計時器
app.py / backtester.py / ladder_system.py / data_sources/  # 不在變更面
requirements.txt                # 不新增任何依賴
```

**Structure Decision**：兩個新模組、一個修改的呼叫端、兩條工作流。
不建立套件目錄——本專案既有 24 個頂層模組皆為扁平佈局，為兩個檔案引入
套件結構會與既有慣例不一致。`ledger_store` 與 `ledger_sync` 分開是因為前者是
純函式的檔案讀寫（易測、無副作用），後者要呼叫 `git` 並處理重試（需替身）；
混在一起會讓「同步必須在所有寫入之後」這個時序約束變得不明顯，
也會讓純讀寫的測試被迫處理 git。

## Complexity Tracking

> 憲章 Governance 要求：新增抽象層須回答「為何更簡單的做法不可行」。

| 新增 | 為何需要 | 更簡單的做法為何不可行 |
|---|---|---|
| `ledger_sync.py` 的 rebase 重試 | 兩條工作流可能同時追加帳（手動 `workflow_dispatch`）；直接 push 會因 non-fast-forward 失敗 | 「直接 push、失敗就算了」會讓該次紀錄靜默消失（FR-010 禁止）；`push --force` 會覆蓋另一次執行的紀錄（FR-002 禁止的靜默覆蓋）；引入分散式鎖是為每年可能零次的事件付出常態複雜度 |
| 帳按月分割 | 避免單檔無限增長，並把衝突面收斂到當月 | 單一檔案在多年後會成為長檔且每次衝突都涉及全檔；一檔一次執行（`<timestamp>.jsonl`）完全不衝突，但每年產生 2,500+ 個小檔，目錄不可讀 |
| `ledger_store` 與 `ledger_sync` 分為兩個模組 | 時序約束（同步必須在所有寫入之後）需要明顯；且純讀寫要能在無 git 的環境測試 | 合併為一個模組會讓讀寫測試被迫準備 git 環境，並使時序約束隱形 |

**不新增的東西**（相對 ADR 0002 方案的簡化）：資料庫驅動、外部服務、憑證管理、
連線繫結狀態機、獨立的快照機制。

**範圍護欄**：

- 不搬行情資料（階段二，issue #41；**託管方案屆時再定**）
- 不建影子部位的紀錄型別（spec 013）
- 不引入鎖或交易隔離
- 不改任何訊號判定、訊息格式或推播管道

---

## 驗收有賞味期（給實作階段的提醒）

CI 上的一次綠燈只證明「當下那一次」。git 推送行為、`actions/checkout` 的預設深度、
`pull --rebase` 在特定衝突形狀下的結果，都可能隨版本或情境改變。

因此契約 C4／C6 的每一條 MUST 靠**離線替身／注入式測試**鎖住
（模擬衝突、模擬推送失敗、模擬併發追加、模擬跨月邊界），
真實執行只作為「這一刻確實接通」的補充證據，不得作為唯一防線。
此教訓來自 010 的驗收經驗（TAIFEX 曾在驗收當天稍晚改格式）。

**特別注意 C1**：去重鍵格式若在遷移中被正規化，症狀是「歷史訊號全部重發一輪」
——看起來像上線成功，實則災難。quickstart.md 有一段可執行的比對腳本，
遷移後 MUST 跑過且比對不到的筆數為 0。
