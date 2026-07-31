# Phase 0 Research: 排程與持久化

**Date**: 2026-07-31 | **Spec**: [spec.md](spec.md) | **ADR**: [0002](../../docs/adr/0002-ledger-on-turso-market-data-stays-regenerable.md)

本階段的目的是驗證 ADR 0002 的技術前提是否成立。**三項發現改變了設計**，
其中一項是對 ADR 0002 措辭的修正（結論不變，理由更精確）。

---

## R1：libSQL Python 驅動的成熟度與 DB-API 相容性

**Decision**：採用 `libsql` 套件，但**不讓累積紀錄依賴 pandas 的 `to_sql`/`read_sql`**，
只依賴文件明確記載的 `connect()` / `execute(sql, params)` / `commit()` 三個操作。

**Rationale**：

查證結果：

| 項目 | 事實 |
|---|---|
| 套件名 | `libsql` |
| 版本／發布日 | **0.1.11，2025-09-02**（距今約 11 個月） |
| PyPI 說明 | 無（"The author of this package has not provided a project description"） |
| 官方文件宣稱 | 「Python `sqlite3`-compatible」API |
| 文件明確記載的操作 | `connect(database=..., auth_token=...)`、`execute()`、`commit()`、`sync()` |
| 文件**未**記載 | `cursor()`、`executemany()`、完整 DB-API 2.0 相容性、純本機檔連線語法、
不支援的 sqlite3 功能清單 |

這是一個 **0.1.x、十一個月未發版、無 PyPI 說明、DB-API 相容性未載明**的驅動，
而我們要用它保管專案唯一不可再生的資產。直接把它當 `sqlite3` 的 drop-in 替換
（例如丟給 pandas `to_sql`）是把賭注押在未經證實的相容面上。

避開的方法是**縮小依賴面**：累積紀錄的存取型態是「每日追加數筆、以主鍵查詢單筆」，
不是 DataFrame 批次載入。因此只需要 `execute(sql, params)` 與 `commit()`
——兩者都有文件記載。pandas 完全不進入這條路徑。

副效果是驅動可替換性變高：若 `libsql` 日後無人維護，替換面只有一個薄轉接層，
而不是散落各處的 `to_sql` 呼叫。

**Alternatives considered**：

- **把 `libsql` 當 sqlite3 drop-in、沿用 pandas 路徑**：最少程式碼，但賭在未載明的
  相容性上；且 pandas 對非 `sqlite3` 連線的處理路徑會因版本而異，屬於難以測出的脆弱點。
- **走 Turso HTTP API（不用 Python 驅動）**：消除驅動依賴，但要自行處理序列化、
  重試、型別對應，且失去「SQLite 方言」帶來的階段二遷移優勢。
- **改用 Postgres 驅動（psycopg）成熟度高**：已於 ADR 0002 否決，理由是會使階段二
  （行情資料遷移）被定價成資料層重寫而永不發生。

---

## R2：ADR 0002 的「db_security 一行不改」需要更精確的措辭

**Decision**：措辭修正為——**累積紀錄的存取路徑本來就不經過 `db_security`，
因此本 spec 不改動它；`db_security` 的價值在階段二（行情資料遷移）才會被兌現。**

**Rationale**：

查證 as-built（`monitor_signals.py:37-92`）發現，現行去重紀錄的三個函式
（`init_sent_alerts_db` / `is_alert_already_sent` / `mark_alert_as_sent`）**直接呼叫
`sqlite3.connect`，完全沒有經過 `db_security`**。

原因是合理的：`db_security` 的核心價值是**動態表名白名單**
（`^(stock|fut)_[a-zA-Z0-9_]+_(daily|5m)$`），用來防止「以標的代號組出表名」時的注入。
去重紀錄的表名是**固定字面值** `sent_alerts`，沒有動態表名，所以那道防線無事可做；
它需要的只是參數化查詢，而現行程式碼**已經**是參數化的（`WHERE ticker=? AND ...`）。

所以 ADR 0002 寫的「libSQL 是 SQLite 方言故 `db_security` 一行不改」在本 spec 為真，
但理由不是「因為方言相容所以能沿用」，而是「這條路徑從來沒用它」。
**方言相容的真正價值在階段二**——屆時要搬的是動態表名的行情表，那才會需要
`db_security` 原樣運作。ADR 0002 的結論（選 Turso 以保住階段二可行性）不變。

憲章 Security 節「SQLite 存取一律使用參數化查詢」的要求，本 spec 以「所有語句
皆為參數化字面值 SQL」滿足，不需引入 `db_security`。

**Alternatives considered**：

- **把去重紀錄也納入 `db_security`**：為固定表名套上動態表名白名單，
  是把防護當儀式，且會把 `db_security` 與託管驅動耦合、增加階段二的變更面。

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
兩者都是「向重複告警傾斜」的 fail-open 設計，與該憲章條款一致。
本 spec 的 FR-006（有憑證但不可達須硬失敗）要改的正是這個傾斜——
做法是**在初始化時就失敗**，使後續流程永遠不會在儲存已壞的情況下走到 fail-open 分支
（見 R4）。

**Alternatives considered**：

- **順手改憲章**：憲章 Governance 明定「修訂需以 PR 形式提出，說明動機與影響範圍」，
  在 feature spec 裡夾帶修憲違反該程序。
- **把 SC-001 降級以符合憲章**：本末倒置——消除重複告警正是本 spec 的目的。

---

## R4：故障語意——fail-fast 與 fail-open 的分界

**Decision**：**連線階段 fail-fast，資料階段沿用 fail-open。**
分界點是「憑證是否存在」與「連線是否建立成功」，在任何訊號判定發生**之前**決定。

**Rationale**：

FR-005（無憑證退化本機）與 FR-006（有憑證但不可達硬失敗）看似衝突，實則是
兩個不同時機的決策。設計為單一啟動期判定：

| 憑證 | 連線 | 行為 |
|---|---|---|
| 未設定 | — | 用本機檔，輸出明示「目前使用本機儲存」，**不視為錯誤**（FR-005） |
| 已設定 | 成功 | 用託管儲存 |
| 已設定 | 失敗 | **立即以非零碼結束**，訊息明指持久化失敗；**不退化本機**（FR-006、FR-007） |

如此一來，程式進入訊號判定時，儲存必定可用。既有的 fail-open 例外處理
（查詢失敗視為未發送）得以保留為最後一道防線而非常態路徑——它防的是執行期
偶發錯誤，不再是「整個儲存機制壞掉」這種系統性故障。

**Alternatives considered**：

- **全程 fail-fast（含資料階段）**：任何查詢失敗即中止整輪推播。會讓單一標的的
  暫時性錯誤擴散為全部標的漏發，違反憲章「不得漏發」。
- **全程 fail-open**：即現狀，FR-006 無從成立。

---

## R5：快照的形式與落點

**Decision**：快照為**單一檔案的完整匯出**（累積紀錄的所有列），
於該次所有寫入完成後產生；內容有變動才提交進 repo，並每次執行另存一份可下載副本。

**Rationale**：

- **形式**：累積紀錄年增量以千筆計，全量匯出成本可忽略，且全量快照的還原邏輯
  比增量快照簡單得多（US3 情境 1 要求「逐筆一致」，全量匯出可直接比對）。
- **落點**：提交進 repo 取得永久保存與 `git log -p` 可追溯性；可下載副本
  （Actions artifact）保留期有限，作為近期的快速取用。兩者互補而非重複。
- **變動才提交**（FR-009）：避免每個交易日一筆 bot commit 的歷史雜訊。
- **時序**（FR-008）：必須在寫入之後——若快照先於寫入，還原會遺失該次紀錄
  （spec Edge Cases 已列）。

**已知副作用**：這類提交會重置 GitHub 的 60 天儲存庫活動計時器，
使 `keepalive` 的**保活**職責變為冗餘。但該工作流的另兩項職責——
偵測並重新啟用被停用的工作流、以及回報推送身分——仍有價值，故不移除。
`keepalive.yml` 現行的 `THRESHOLD_DAYS: 45` 會使它在有快照提交的期間什麼都不做，
行為正確、無須改動。

**Alternatives considered**：

- **只存 artifact**：保留期與「累積多年紀錄」的目的衝突（ADR 0002 已否決同一論點）。
- **只提交 repo**：失去「隨時可下載最近一份」的便利，且沒有第二個副本。
- **每次執行都提交**：每年約 250 筆 bot commit 的雜訊。

---

## 未解事項

無 `NEEDS CLARIFICATION`。唯一的外部依賴是 repo owner 需自行註冊託管服務帳號
並設定 `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN`（spec Assumptions 已載明）；
US2 的本機退化路徑使開發與單元測試不受此阻塞。
