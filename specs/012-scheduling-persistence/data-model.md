# Phase 1 Data Model: 排程與持久化

**Date**: 2026-07-31 | **Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

---

## 儲存位置的劃分

本 spec 的核心是把單一 `trendpoint.db` 拆成兩個生命週期獨立的儲存位置（FR-001）。

| | 帳（Ledger） | 行情儲存（Market Store） |
|---|---|---|
| 內容 | 推播去重紀錄；spec 013 起加入影子部位 | 行情 K 線與衍生表 |
| 可再生 | **否**——遺失即永久遺失 | 是——可由來源重抓 |
| 實體 | `ledger/YYYY-MM.jsonl`，受版本控制 | `trendpoint.db` on GitHub Actions cache（不變） |
| 耐久性來源 | git 歷史（不會 LRU 淘汰、不受 branch 隔離、衝突為顯性） | 無保證（可淘汰、可過期、可靜默回退） |
| 格式 | JSONL 純文字 | SQLite |
| 表／鍵名 | 固定字面值 | 動態（`stock_*` / `fut_*`，經 `db_security`） |
| 存取型態 | 全檔讀入／檔尾追加 | DataFrame 批次讀寫 |
| 存取層 | 新增的帳存取模組 | `db_security`（不改動，見 research.md R2） |
| `.gitignore` | **不得排除** | `*.db` 排除**保持不變** |

**不變式**：行情儲存的任何遺失或重建，MUST NOT 改變帳的內容（SC-005）。

---

## 實體

### SentAlert（推播去重紀錄）

本 spec 的唯一租戶。**欄位與既有 `sent_alerts` 表逐字相同**——本 spec 只改
「存在哪裡」，不改「長什麼樣」（FR-003）。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `ticker` | str | 標的代號（現貨 ticker 或期貨 instrument id） |
| `bar_time` | str | 產生該訊號的 K 線時間（字串化的時間戳） |
| `alert_type` | str | 訊號型態（`BULLISH_MSS` / `BEARISH_MSS` / `BULLISH_BOS` / `BEARISH_BOS` / `BREAK_UPPER_BAND` / `BREAK_LOWER_BAND`） |
| `sent_time` | str | 實際推播成功的時間 |

**去重鍵**：(`ticker`, `bar_time`, `alert_type`)。

**驗證規則**：

- 三個去重鍵欄位皆 MUST NOT 為空字串或 null。
- `bar_time` MUST 沿用既有 `str(bar_time)` 的轉換，**不得改變格式**
  ——否則既有紀錄比對不到，會使歷史訊號全部被判為未發送而重發一輪。
- 語意為 **upsert**：同一去重鍵重複出現不得報錯。JSONL 為追加式，
  故同鍵可能出現多列——**讀取時以最後一列為準**，且此行為 MUST 有測試鎖住。

**狀態轉移**：無狀態機。紀錄只有「不存在」→「存在」一種轉移，且不刪除。

### LedgerFile（帳檔）

| 屬性 | 說明 |
|---|---|
| 路徑 | `ledger/YYYY-MM.jsonl`，`YYYY-MM` 為 **UTC** 月份 |
| 格式 | 一列一筆 JSON 物件，UTF-8，換行結尾 |
| 順序 | 追加順序即寫入順序，不排序、不重寫既有列 |

**驗證規則**：

- 讀取 MUST 涵蓋判定所需的所有月檔（跨月邊界時至少含當月與前月）。
- 新月份的首次寫入 MUST 能建立新檔而非失敗（spec Edge Cases）。
- MUST NOT 重寫或刪除既有列——只追加。任何「整檔重寫」的實作都會
  在 rebase 時造成不必要的衝突，並失去 `git log -p` 的逐筆可讀性。

### SyncMode（同步模式）

啟動期一次性判定的執行期實體，非持久化資料。

| 值 | 條件 | 行為 |
|---|---|---|
| `local` | 非 CI 環境 | 只寫工作目錄的檔案；輸出明示「本機模式，紀錄未推送」；不視為錯誤 |
| `ci` | CI 環境 | 寫檔後 rebase／commit／push；重試耗盡即非零碼結束 |

**狀態轉移**：

```
啟動
 ├─ 非 CI ──────────────→ local
 └─ CI ─────────────────→ ci
                            ├─ 無新紀錄 ──→ 不提交（不視為錯誤）
                            ├─ 推送成功 ──→ 正常結束
                            ├─ 衝突 ──────→ pull --rebase 後重試（上限 N）
                            └─ 重試耗盡 ──→ 非零碼結束（訊息明指帳未落地）
```

**不變式**：一次執行內 `SyncMode` MUST NOT 改變。同步 MUST 發生在該次
所有紀錄寫入完成之後、一次性進行（FR-008）——這是「同步階段能整批失敗」
因而得以 fail-fast 的前提（research.md R4）。

---

## 無獨立的「快照」實體

ADR 0002 的設計是「託管儲存 + 另存快照」兩層；ADR 0004 之後兩層收斂為一層——
**帳本身即耐久紀錄**，每次同步就是一次提交，`git log -p` 即其歷史。
spec 因而少掉一個 user story（原 US3）。

---

## 與後續 spec 的關係

spec 013 的**影子部位**將成為帳的第二個租戶，沿用同一組檔案格式與同步機制。
本 spec MUST NOT 預先定義影子部位的欄位——但帳的存取介面
MUST 能容納「多種紀錄型別共存」，而非只綁死 `SentAlert`。

階段二（issue #41）會處理行情資料的遷移。屆時動態表名與 `db_security`
才會進入該路徑；**託管方案屆時再定**，本 spec 不預先綁定任何供應商。
