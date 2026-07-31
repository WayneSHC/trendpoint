# 帳存於託管 Turso；行情資料留在可再生的快取

`trendpoint.db` 目前靠 GitHub Actions cache 在 workflow run 之間傳遞
（`key: trendpoint-db-${{ github.run_id }}` + `restore-keys` 前綴比對）。這個機制有
LRU 淘汰、7 天未用過期、branch-scoped、以及「兩條工作流互相覆寫時可能復活舊快照」
四個性質。今天的後果只是去重表回退造成重複推播；但帳是累積型資產，回退一次
就永久少掉那幾天的樣本外紀錄，**而且不會報錯**。

我們把帳搬到託管的 Turso（libSQL），行情資料留在 cache。只有不可再生的東西
需要付出外部依賴的代價；行情資料掉了重抓即可。

## Considered Options

- **commit 回 repo**：帳是 append-only 純文字、一天幾行，git 很適合。被否決是因為
  想要真正的查詢能力與單一資料庫。
- **Postgres（Supabase / Neon）**：能力更強，但會逼迫重寫整個資料層——
  `db_security.py` 的表名白名單、`safe_load_db_data`、所有 `to_sql`/`read_sql`
  都是 SQLite 形狀，而憲章要求 SQL 一律經該層防護。
- **Actions artifact**：90 天保留期與「累積多年紀錄」的目的直接衝突。

選 Turso 的決定性理由**不是免費層條款**（Turso 閒置 10 天封存、Supabase 閒置 7 天暫停、
Neon 只有 scale-to-zero——三家都可恢復，不構成差異），**而是遷移成本決定了第二階段
會不會發生**。libSQL 是 SQLite 方言，資料層一行不改；選 Postgres 的話第一階段一樣便宜，
但第二階段會被定價成「重寫資料層」而永遠不發生，專案將永久卡在
「帳在雲上、行情在 cache」的分裂狀態——而那個分裂正是本 ADR 要解決的問題根源。

## Consequences

**階段二另立 spec**：行情資料也搬上 Turso。觸發條件明確——當想把 Streamlit 儀表板
部署到本機以外時。目前 `app.py` 讀本機 DB 檔而該檔活在 Actions cache 裡，
所以儀表板實質上無法部署到任何地方；那才是這條路線最大的解鎖。

**快照備份是必要項，不是選項**：Turso 曾於 2023-12-04 發生部分免費層資料庫的
資料洩漏與遺失事故。每次 CI 跑完除了寫 Turso，另存一份帳的快照，
以保住「provider 掛掉也還在」的稽核軌跡。

**無憑證時退化為本機 SQLite 檔**，跑同一份程式碼——與 `alerts.py` 無憑證退成 Mock 同形。
