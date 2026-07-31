# Phase 1 Data Model: 排程與持久化

**Date**: 2026-07-31 | **Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

---

## 儲存位置的劃分

本 spec 的核心是把單一 `trendpoint.db` 拆成兩個生命週期獨立的儲存位置（FR-001）。

| | 累積儲存（Append-Only Store） | 行情儲存（Market Store） |
|---|---|---|
| 內容 | 推播去重紀錄；spec 013 起加入帳 | 行情 K 線與衍生表 |
| 可再生 | **否**——遺失即永久遺失 | 是——可由來源重抓 |
| 實體 | 託管 libSQL（有憑證）／本機檔（無憑證） | GitHub Actions cache 中的 `trendpoint.db`（不變） |
| 表名 | 固定字面值 | 動態（`stock_*` / `fut_*`，經 `db_security`） |
| 存取型態 | 單筆查詢／追加 | DataFrame 批次讀寫 |
| 存取層 | 新增的累積紀錄轉接層 | `db_security`（不改動） |

**不變式**：行情儲存的任何遺失或重建，MUST NOT 改變累積儲存的內容（SC-005）。

---

## 實體

### SentAlert（推播去重紀錄）

本 spec 的唯一租戶。**Schema 與既有 `sent_alerts` 表逐字相同**——本 spec 只改
「存在哪裡」，不改「長什麼樣」（FR-003）。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `ticker` | TEXT | 標的代號（現貨 ticker 或期貨 instrument id） |
| `bar_time` | TEXT | 產生該訊號的 K 線時間（字串化的時間戳） |
| `alert_type` | TEXT | 訊號型態（`BULLISH_MSS` / `BEARISH_MSS` / `BULLISH_BOS` / `BEARISH_BOS` / `BREAK_UPPER_BAND` / `BREAK_LOWER_BAND`） |
| `sent_time` | TEXT | 實際推播成功的時間 |

**主鍵**：(`ticker`, `bar_time`, `alert_type`) — 即去重鍵。

**驗證規則**：

- 三個主鍵欄位皆 MUST NOT 為空字串或 NULL。
- `bar_time` MUST 為字串化的時間戳（沿用既有 `str(bar_time)` 的轉換，不改變格式，
  否則既有紀錄的去重比對會失效）。
- 寫入語意為 **upsert**（既有行為為 `INSERT OR REPLACE`）——同一去重鍵重複寫入
  不得報錯，而是更新 `sent_time`。

**狀態轉移**：無狀態機。紀錄只有「不存在」→「存在」一種轉移，且不刪除。

### StorageBinding（儲存繫結）

啟動期一次性決定的執行期實體，非持久化資料。記錄本次執行使用哪個累積儲存。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `mode` | enum | `hosted` \| `local` |
| `target` | str | 託管 URL（**不含 token**）或本機檔路徑 |
| `resolved_at` | timestamp | 繫結建立時間 |

**狀態轉移**（R4 的故障語意，FR-005／FR-006／FR-007）：

```
啟動
 ├─ 憑證未設定 ─────────────────→ mode=local（輸出明示；不視為錯誤）
 └─ 憑證已設定
      ├─ 連線成功 ─────────────→ mode=hosted
      └─ 連線失敗 ─────────────→ 非零碼結束（不得轉為 local）
```

**不變式**：一次執行內 `mode` MUST NOT 改變；MUST NOT 同時寫入兩種儲存（FR-007）。

### Snapshot（快照）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `content` | 全量匯出 | 累積儲存所有租戶表的完整內容 |
| `taken_at` | timestamp | 產生時間，MUST 晚於該次所有寫入 |
| `content_digest` | str | 內容摘要，用於判斷是否與上一份相同 |

**驗證規則**：

- MUST 在該次執行所有寫入完成之後產生（FR-008；否則還原會遺失該次紀錄）。
- `content_digest` 與上一份相同時 MUST NOT 產生新的提交（FR-009）。
- 產生失敗 MUST 使該次執行以非零碼結束（FR-010）。
- MUST 可在無託管服務的乾淨環境還原，且還原結果與匯出時逐筆一致（US3 情境 1）。

**憑證安全**：快照內容 MUST NOT 含任何憑證；`target` 若寫入快照的中介資料，
MUST 為去除 token 的形式（FR-011）。

---

## 與後續 spec 的關係

spec 013 的**帳（Ledger）**將成為累積儲存的第二個租戶，沿用同一個轉接層與
同一套故障語意。本 spec MUST NOT 預先建立帳的表結構——但轉接層的介面
MUST 能容納「多個固定表名的租戶」，而非只綁死 `sent_alerts` 一張表。

階段二（issue #41）會把行情儲存也遷入託管服務。屆時動態表名與
`db_security` 才會進入這條路徑——本 spec 刻意不觸碰，以縮小變更面。
