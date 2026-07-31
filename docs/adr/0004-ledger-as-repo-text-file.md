---
status: accepted
supersedes: ADR-0002
---

# 帳為 repo 內的 append-only 純文字檔，不用託管資料庫

[ADR 0002](0002-ledger-on-turso-market-data-stays-regenerable.md) 決定把帳搬到託管
libSQL（Turso）。spec 012 的 Phase 0 研究隨後推翻了該決定的技術前提。帳改為
repo 內按月分割的 JSONL（`ledger/YYYY-MM.jsonl`），零憑證、零新依賴。
託管資料庫的決策延到階段二——屆時真正需要資料庫的是行情資料，需求形狀與今天不同。

## 推翻 0002 的三項證據

1. **驅動不成熟**：`libsql` 為 0.1.11、2025-09-02 發版、PyPI 無專案說明，
   `cursor()`／`executemany()`／DB-API 2.0 相容性皆未載明。要用它保管專案
   唯一不可再生的資產。
2. **0002 的核心論據不成立**：0002 主張「libSQL 是 SQLite 方言，故 `db_security`
   一行不改」。但 as-built（`monitor_signals.py:37-92`）的去重路徑**直接呼叫
   `sqlite3.connect`、從未經過 `db_security`**——`db_security` 的價值是動態表名
   白名單，而 `sent_alerts` 是固定字面值、SQL 早已參數化。方言相容對本階段的
   好處是零，只在階段二（搬動態表名的行情表）才兌現。
3. **快照本身就是一份耐久的帳**：spec 012 已要求快照為「全量匯出、內容有變動即
   commit 進 repo、可獨立還原、逐筆一致」。那已是 append-only、永久、可 diff、
   不受 LRU 淘汰的帳。託管資料庫因此是疊在「反正要建的機制」上的一份工作副本。

加上量級事實：決策當時 `sent_alerts` 總共 **1 列**。託管資料庫對本階段的淨貢獻
是 SQL 查詢能力，而沒有任何東西需要查詢它。

## Considered Options

- **Turso（0002 的選擇）**：見上述三點。其「階段二遷移便宜」的論據仍然成立，
  但那是階段二的事，屆時可用更好的資訊決定，且屆時的需求（52,972 列、動態表名、
  DataFrame 存取、儀表板遠端讀取）與今天差異足夠大，今天的選擇大概也會被翻案。
- **Neon / Postgres**：既然 `db_security` 不在這條路徑上，SQLite 方言的優勢消失，
  改選成熟驅動（`psycopg`）本來是合理的。否決理由是它並未解決「為 1 列資料
  引入外部服務與憑證」這個根本問題。
- **維持 GitHub Actions cache**：即 0002 要解決的問題本身。cache 會 LRU 淘汰、
  7 天未用過期、branch-scoped，且兩條工作流互相覆寫時會靜默復活舊快照。

## Consequences

**取得**：零憑證、零新依賴、零驅動風險。驗收不再需要註冊外部帳號，
今天就能在真實環境完成。`git log -p` 成為稽核軌跡；回退會是顯性的 push 衝突，
而非靜默覆蓋——這正是 0002 要防的失敗模式，用更簡單的機制達成。

**帳與快照合併為同一個物件**，spec 012 因此少掉一個 user story。

**代價**：CI 需 `contents: write` 權限；兩條工作流同時 push 會衝突，需
`pull --rebase` 重試；重試耗盡即該次紀錄未落地，必須以非零碼結束而非靜默丟棄。
託管資料庫沒有這些問題——寫入就是寫入。這是真實的取捨。

**格式受 `.gitignore` 約束**：`*.db` / `*.sqlite3` 已被排除，故帳必須是純文字。
這個約束與「純文字才有 diff 價值」的需求同向，不衝突。

**ADR 0002 保留不刪**，僅標記為被本 ADR 取代。它記錄的推理與被否決的選項
（commit 回 repo、Postgres、Actions artifact、各家閒置政策的比較）仍有參考價值；
刪掉會讓後人重新踩一遍。
