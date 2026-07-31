# Phase 0 Research: 排程與持久化

**Date**: 2026-07-31 | **Spec**: [spec.md](spec.md) | **ADR**: [0004](../../docs/adr/0004-ledger-as-repo-text-file.md)（取代 [0002](../../docs/adr/0002-ledger-on-turso-market-data-stays-regenerable.md)）

本階段的目的是驗證 ADR 0002「帳搬託管 libSQL」的技術前提是否成立。
**結論是不成立**——三項發現合起來推翻了託管資料庫方案，改為 repo 內純文字檔，
並產出 ADR 0004。這正是 Phase 0 存在的意義：在寫任何程式碼之前推翻自己的設計。

---

## R1：不需要資料庫，也不需要資料庫驅動

**Decision**：帳為 repo 內按月分割的 JSONL（`ledger/YYYY-MM.jsonl`），
不引入任何資料庫驅動或外部服務。

**Rationale**：三條證據，任一條單獨都不足以推翻 ADR 0002，合起來足夠。

### 證據一：驅動不成熟

| 項目 | 事實 |
|---|---|
| 套件名 | `libsql` |
| 版本／發布日 | **0.1.11，2025-09-02**（距今約 11 個月） |
| PyPI 說明 | 無（"The author of this package has not provided a project description"） |
| 官方文件宣稱 | 「Python `sqlite3`-compatible」API |
| 文件明確記載 | `connect(database=..., auth_token=...)`、`execute()`、`commit()`、`sync()` |
| 文件**未**記載 | `cursor()`、`executemany()`、完整 DB-API 2.0 相容性、純本機檔連線語法、不支援的 sqlite3 功能清單 |

一個 0.1.x、十一個月未發版、無說明、DB-API 相容性未載明的驅動，
要用來保管專案唯一不可再生的資產。

### 證據二：需求量級與方案量級差三個數量級

`sent_alerts` 目前**總共 1 列**——`('MTX', '2021-02-24 00:00:00', 'BULLISH_BOS',
寫於 '2026-07-18')`，還是 MOCK 資料。spec 013 的影子部位加入後為每日數筆、
年增量以千筆計。相對地，行情庫是 52,972 列。

託管資料庫對本階段的淨貢獻是「SQL 查詢能力」，而**沒有任何東西需要查詢它**。
儀表板若日後要顯示帳，用 pandas 讀 JSONL 是一行。

### 證據三：原方案已內含一份耐久的帳

ADR 0002 把「快照」列為**必要項而非選項**（因該服務曾於 2023-12-04 發生免費層
資料洩漏與遺失事故），且 spec 012 原 FR-008 要求快照為「全量匯出、內容有變動即
commit 進 repo、可獨立還原、逐筆一致」。

那已經是一份 append-only、永久、可 diff、不受 LRU 淘汰的帳。
託管資料庫因此是**疊在「反正要建的機制」上面的一份工作副本**——
移除它，剩下的那一層就是完整的解答。這也讓帳與快照收斂為同一個物件，
spec 因而少掉一個 user story。

**Alternatives considered**：

- **Turso / libSQL（ADR 0002 的選擇）**：見上。其「階段二遷移便宜」的論據仍成立，
  但那是階段二的事；且屆時的需求（動態表名、DataFrame 存取、儀表板遠端讀取）
  與今天差異足夠大，今天的選擇大概也會被翻案。
- **Neon / Postgres**：既然 R2 證明 `db_security` 不在這條路徑上，SQLite 方言的
  優勢消失，改選成熟驅動（`psycopg`）本來合理。否決理由是它並未解決
  「為 1 列資料引入外部服務與憑證」這個根本問題。
- **維持 Actions cache**：即本 spec 要解決的問題本身（LRU 淘汰、7 天過期、
  branch-scoped、靜默復活舊快照）。
- **SQLite 檔提交進 repo**：`.gitignore` 已排除 `*.db`，且二進位檔無 diff 價值。

---

## R2：`db_security` 不在這條路徑上（ADR 0002 核心論據的證偽）

**Decision**：本 spec 不改動 `db_security`；其價值在階段二才會被兌現。

**Rationale**：

ADR 0002 的核心論據是「libSQL 是 SQLite 方言，故 `db_security` 一行不改」。
但查證 as-built（`monitor_signals.py:37-92`）發現，現行去重紀錄的三個函式
（`init_sent_alerts_db` / `is_alert_already_sent` / `mark_alert_as_sent`）
**直接呼叫 `sqlite3.connect`，完全沒有經過 `db_security`**。

原因是合理的：`db_security` 的核心價值是**動態表名白名單**
（`^(stock|fut)_[a-zA-Z0-9_]+_(daily|5m)$`），防的是「以標的代號組出表名」時的注入。
去重紀錄的表名是**固定字面值** `sent_alerts`，沒有動態表名，那道防線無事可做；
它需要的只是參數化查詢，而現行程式碼**已經**是參數化的（`WHERE ticker=? AND ...`）。

因此 ADR 0002 那句話為真，但**理由不是「方言相容所以能沿用」，而是「這條路徑
從來沒用它」**——這使得該論據無法支撐「必須選 SQLite 方言的供應商」這個結論。
方言相容的真正價值在階段二（搬動態表名的行情表）。

改為純文字檔後，本 spec 連 SQL 都不再涉及，憲章 Security 節
「SQLite 存取一律使用參數化查詢」對帳的路徑自然不適用
（該要求對行情路徑仍然完全有效，而行情路徑本 spec 不動）。

---

## R3：憲章與本 spec 的去重保證強度不一致（需修憲）

**Decision**：本 spec 提供比憲章更強的保證；**憲章該條款應在 012 落地後修訂**，
另以 PR 提出（憲章 Governance 要求）。本 spec 不擅自改憲章。

**Rationale**：

憲章 Security & Operational Constraints 第 3 條現行文字：

> 排程監控（GitHub Actions）必須具備告警去重機制，
> **且允許快取失效時的重複告警視為可接受的降級行為，但不得漏發。**

而本 spec 的 SC-001 要求**重複推播與漏推播皆為 0**。

兩者不衝突（更嚴格不算違憲），但那句「允許快取失效時的重複告警」是在
「快取是唯一持久化機制」的前提下寫的。012 落地後該前提消失，這句話會變成
一條**誤導後人的過期豁免**——正是憲章 III 所禁止的「沉默漂移」的另一種形態。

同時查到一個相關的 as-built 行為：`is_alert_already_sent` 在任何例外時
`return False`（視為未發送 → 會重發），`mark_alert_as_sent` 則吞掉寫入例外。
兩者都是「向重複告警傾斜」的 fail-open 設計，與該憲章條款一致。本 spec
保留這個傾斜（見 R4），但把系統性故障從中分離出來。

**Alternatives considered**：

- **順手改憲章**：憲章 Governance 明定「修訂需以 PR 形式提出，說明動機與影響範圍」，
  在 feature spec 裡夾帶修憲違反該程序。
- **把 SC-001 降級以符合憲章**：本末倒置——消除重複告警正是本 spec 的目的。

---

## R4：故障語意——同步失敗紅燈，讀取失敗 fail-open

**Decision**：**同步階段（寫檔後的 rebase／commit／push）失敗即紅燈；
帳的讀取失敗沿用既有 fail-open。** 同步模式（`ci` / `local`）於啟動時判定一次。

**Rationale**：

ADR 0002 時期的設計是「憑證存在但連線失敗 → fail-fast」。改為純文字檔後
憑證消失，該分界不再適用。新的分界如下：

| 情境 | 行為 |
|---|---|
| 本機執行 | 只寫工作目錄的檔案，不 commit／push；輸出明示「本機模式，紀錄未推送」；**不視為錯誤** |
| CI、無新紀錄 | 不產生提交；**不視為錯誤**（FR-009） |
| CI、推送成功 | 正常 |
| CI、衝突 | `pull --rebase` 後重試（上限可組態） |
| CI、重試耗盡 | **非零碼結束**，訊息明指帳未落地；MUST NOT 靜默丟棄（FR-010） |
| 讀取帳失敗 | 沿用 fail-open（視為未發送 → 會重發）。憲章「不得漏發」 |

保留讀取端 fail-open 的理由：單一標的的暫時性錯誤不應擴散為全部標的漏發。
而寫入端之所以能夠 fail-fast，是因為同步發生在**所有紀錄寫入完成之後**、
一次性進行——它要嘛整批成功、要嘛整批失敗，不存在「部分標的受影響」的中間態。

**這個不對稱是本設計最不直觀之處**：同一種「帳出問題」，讀取要容忍、同步要紅燈。
正當性在於同步階段能明確區分系統性故障，讀取階段不能。已寫入契約
[C4](contracts/append-only-store-contract.md)，並註明勿「順手統一」。

**Alternatives considered**：

- **同步也 fail-open（失敗就算了）**：帳會靜默出洞，正是本 spec 要消除的失敗模式，
  且與 `daily_ingestion` 額外加驗證步驟所防的假綠燈同形。
- **讀取也 fail-fast**：任何讀取錯誤即中止整輪推播，會使暫時性錯誤造成全面漏發，
  違反憲章「不得漏發」。

---

## R5：併發與衝突

**Decision**：`pull --rebase` + 重試（上限可組態，預設 3 次），不引入鎖。
帳按 UTC 月分割以收斂衝突面。

**Rationale**：

- **衝突頻率極低**：`alert_scheduler` 排程於 01:00–05:30 UTC、`daily_ingestion` 於
  09:00 UTC，兩者不重疊。衝突只來自手動 `workflow_dispatch` 造成的同時執行。
- **JSONL 的追加式衝突易解**：兩邊都在檔尾追加，rebase 後重新追加即可
  （新紀錄在記憶體中，重放成本為零）。
- **月分割**的作用是避免單檔無限增長，同時讓衝突面只涉及當月數十至數百列。
- **不引入鎖**：以此寫入頻率（每日數筆）引入分散式鎖，是為極罕見事件付出
  常態複雜度，且違反憲章 Governance「複雜度必須被證成」。

**已知風險**：CI 的 `actions/checkout` 若為 shallow clone，rebase 可能失敗
——帳同步所需的 git 歷史深度必須在實作時明確（spec Edge Cases 已列）。

**Alternatives considered**：

- **一檔一次執行**（`ledger/<timestamp>.jsonl`）：完全不會衝突，但每年產生
  2,500+ 個小檔，目錄不可讀。
- **強制 `push --force`**：會覆蓋另一次執行的紀錄，正是 FR-002 禁止的靜默覆蓋。

---

## 未解事項

無 `NEEDS CLARIFICATION`。**本 spec 無外部前置條件**——不需註冊任何帳號、
不需任何 Secrets，US1／US2／US3 皆可在本機與 CI 完整驗收。
（此為相對 ADR 0002 方案的主要改善之一。）
