# Contract: 帳（呼叫端 ↔ 帳存取層邊界）

**Feature**: 012-scheduling-persistence | **Date**: 2026-07-31

本案不新增對外 API。此契約規範三件最容易被後續改動悄悄破壞的事：
**(a) 去重鍵語意必須逐字不變**、**(b) 同步失敗紅燈與讀取失敗容忍的不對稱**、
**(c) 只追加不重寫**。三者一旦漂移，症狀都是「安靜地重複、漏發或遺失紀錄」
——不會有錯誤訊息。

---

## C1：去重鍵語意凍結（本契約最重要的一條）

去重鍵為 (`ticker`, `bar_time`, `alert_type`) 三元組，
`bar_time` 為 `str(bar_time)` 的字串化結果。

**本案 MUST NOT 改變上述任一項**，包含字串化格式。理由：既有紀錄以此格式寫入，
任何格式變動都會使既有紀錄比對不到，導致**歷史訊號全部被判為未發送而重發一輪**
——一個看起來像「上線成功」的災難。

遷移既有紀錄時 MUST 保持這三個欄位的值逐字相同：不做正規化、不改時區表示、
不轉為 ISO 8601、不 strip。

---

## C2：帳存取介面

呼叫端只依賴下列操作。**刻意不含任何 SQL 或資料庫連線**
——理由見 [research.md](../research.md) R1。

```python
class Ledger(Protocol):
    def load(self, kinds: Sequence[str] | None = None) -> list[Mapping]: ...
    def exists(self, kind: str, key: Mapping[str, str]) -> bool: ...
    def append(self, kind: str, row: Mapping[str, str]) -> None: ...
    def has_pending(self) -> bool: ...      # 是否有尚未同步的新紀錄
```

**約束**：

- `kind` 為紀錄型別的固定字面值（本案僅 `sent_alert`；spec 013 加入影子部位）。
  介面 MUST 能容納多種型別共存，MUST NOT 把 `sent_alert` 寫死進介面。
- `append` **只追加**，MUST NOT 重寫或刪除既有列。整檔重寫的實作會在 rebase
  時造成不必要的衝突，並失去 `git log -p` 的逐筆可讀性。
- 同一去重鍵可能因追加而出現多列；`exists` 與 `load` MUST 以**最後一列為準**，
  且此行為 MUST 有測試鎖住。
- 讀取 MUST 涵蓋判定所需的所有月檔（跨月邊界時至少含當月與前月）。

## C3：單一實作，兩種同步模式

與 ADR 0002 時期不同，**帳的讀寫只有一個實作**——就是讀寫 `ledger/*.jsonl`。
本機與 CI 的差異只在「寫完之後要不要同步」，不在讀寫本身：

| 模式 | 判定 | 寫檔 | 同步 |
|---|---|---|---|
| `local` | 非 CI 環境 | ✓ | ✗（輸出明示「本機模式，紀錄未推送」） |
| `ci` | CI 環境 | ✓ | ✓（rebase／commit／push＋重試） |

此形狀與 `alerts.py` 把 LINE／Telegram／Mock 藏在 `AlertManager` 之後同源，
但更簡單——那裡有三個實作，這裡只有一個實作加一個開關。

---

## C4：故障語意（不對稱是刻意的）

### C4.1 同步階段：fail-fast

同步發生在該次**所有紀錄寫入完成之後**、一次性進行：

| 情境 | 結果 |
|---|---|
| 無新紀錄 | 不產生提交；**不視為錯誤**（FR-009） |
| 推送成功 | 正常 |
| 衝突 | `pull --rebase` 後重試（上限為組態值） |
| 重試耗盡 | **非零碼結束**，訊息明指「帳未落地」（FR-010） |
| `local` 模式 | 不同步；**不視為錯誤**（FR-007） |

**MUST NOT** 在重試耗盡後靜默丟棄該次紀錄並回報成功。這正是
`daily_ingestion` 額外加驗證步驟所防的假綠燈形狀：工作流全綠、資料卻沒進來。

**MUST NOT** 使用 `push --force` 解決衝突——那會覆蓋另一次執行的紀錄，
即 FR-002 禁止的靜默覆蓋。

### C4.2 讀取階段：保留 fail-open

`exists()` 遇例外時回傳 `False`（視為未發送 → 會重發）
——**沿用 as-built 行為**（`monitor_signals.py:71-73`、`89-91`）。

理由：憲章 Security 節明定「不得漏發」。若讀取也 fail-fast，單一標的的
暫時性錯誤會擴散成全部標的漏發。

### C4.3 為何不對稱

同一種「帳出問題」，讀取要容忍、同步要紅燈。**分界的正當性**在於：
同步是整批一次性操作，要嘛全成功要嘛全失敗，能明確辨識為系統性故障；
讀取是逐標的進行，無法區分「整個機制壞了」與「這一筆偶發錯誤」。

**請勿「順手統一」這兩者。** 統一為 fail-open → 帳會靜默出洞；
統一為 fail-fast → 暫時性錯誤造成全面漏發。

---

## C5：帳的檔案契約

| 項目 | 約定 |
|---|---|
| 路徑 | `ledger/YYYY-MM.jsonl`，`YYYY-MM` 為 **UTC** 月份 |
| 格式 | 一列一筆 JSON 物件，UTF-8，換行結尾 |
| `.gitignore` | 帳的路徑 MUST NOT 被排除；`*.db` / `*.sqlite3` 的排除 MUST 保持不變 |
| 內容 | MUST NOT 含任何憑證或 token |
| 新月份 | 首次寫入 MUST 能建立新檔而非失敗 |
| 可追溯 | 每次變更皆可由 `git log -p ledger/` 逐次追溯（SC-003） |

---

## C6：工作流層契約

| 項目 | 約定 |
|---|---|
| 權限 | `alert_scheduler` 與 `daily_ingestion` MUST 具 `permissions: contents: write`（最小必要範圍） |
| checkout 深度 | MUST 足以進行 `pull --rebase`；shallow clone 會使 rebase 失敗（spec Edge Cases） |
| 提交身分 | 沿用 `keepalive.yml` 既有慣例（`github-actions[bot]`） |
| 重試上限 | 經集中組態，MUST NOT 硬編碼（FR-012） |
| 行情快取 | `restore`／`save` 步驟 **MUST 保持不變**（FR-004）；行情遷移屬階段二（issue #41） |

**無環境變數憑證需求**——本案不需要任何 Secrets。這是相對 ADR 0002 方案的
主要改善之一：驗收不再受「repo owner 尚未註冊帳號」阻塞。
