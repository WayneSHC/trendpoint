# Contract: 累積紀錄儲存（呼叫端 ↔ 儲存轉接層邊界）

**Feature**: 012-scheduling-persistence | **Date**: 2026-07-31

本案不新增對外 API。此契約規範兩件最容易被後續改動悄悄破壞的事：
**(a) 去重鍵語意必須逐字不變**，**(b) 故障語意的 fail-fast／fail-open 分界**。
兩者一旦漂移，症狀都是「安靜地重複或漏發推播」——不會有錯誤訊息。

---

## C1：去重鍵語意凍結

去重鍵為 (`ticker`, `bar_time`, `alert_type`) 三元組，
`bar_time` 為 `str(bar_time)` 的字串化結果。

**本案 MUST NOT 改變上述任一項**，包含字串化格式。理由：既有紀錄以此格式寫入，
任何格式變動都會使既有紀錄比對不到，導致**歷史訊號全部被判為未發送而重發一輪**
——一個看起來像「上線成功」的災難。

遷移時既有紀錄的搬遷 MUST 保持這三個欄位的值逐字相同（不做正規化、不改時區表示）。

---

## C2：轉接層介面

呼叫端只依賴下列操作。**刻意不含 DataFrame 批次讀寫**——理由見
[research.md](../research.md) R1（驅動的 DB-API 相容性未載明，故縮小依賴面至
文件明確記載的 `execute` / `commit`）。

```python
class AppendOnlyStore(Protocol):
    def ensure_schema(self, table: str) -> None: ...
    def exists(self, table: str, key: Mapping[str, str]) -> bool: ...
    def upsert(self, table: str, row: Mapping[str, str]) -> None: ...
    def export_all(self) -> bytes: ...          # 供快照
```

**約束**：

- `table` MUST 為固定字面值（呼叫端以常數傳入），MUST NOT 由使用者輸入或標的代號組成。
  因此不需要 `db_security` 的動態表名白名單（見 research.md R2）。
- 所有 SQL MUST 為參數化（憲章 Security 節）。`key` / `row` 的值一律經參數綁定，
  MUST NOT 以字串拼接進 SQL。
- 介面 MUST 能容納多個租戶表（spec 013 的帳為第二個），
  MUST NOT 把 `sent_alerts` 寫死進介面。

## C3：實作對應

| 情境 | 實作 | 連線方式 |
|---|---|---|
| 憑證已設定 | 託管 libSQL | `libsql.connect(database=URL, auth_token=TOKEN)` |
| 憑證未設定 | 本機檔 | 標準 `sqlite3`（本機路徑經集中組態，FR-012） |

兩個實作**共用同一份呼叫端邏輯**；差異僅止於連線建立。此形狀與 `alerts.py`
的 LINE／Telegram／Mock 三實作藏在 `AlertManager` 之後完全一致（FR-005）。

---

## C4：故障語意（本契約的核心）

### C4.1 啟動期繫結：fail-fast

繫結在**任何訊號判定之前**一次性決定，決定後不再改變：

| 憑證 | 連線 | 結果 |
|---|---|---|
| 未設定 | — | `mode=local`，輸出明示「目前使用本機儲存」，**不視為錯誤** |
| 已設定 | 成功 | `mode=hosted` |
| 已設定 | 失敗 | **立即非零碼結束**，訊息明指「持久化失敗」 |

**MUST NOT** 在憑證已設定的情況下因連線失敗而退化為本機檔（FR-006／FR-007）。
這正是 `daily_ingestion` 當初要防的假綠燈形狀：工作流全綠、資料卻沒進來。

### C4.2 執行期資料操作：保留 fail-open

`exists()` 遇例外時回傳 `False`（視為未發送 → 會重發），`upsert()` 吞掉寫入例外
——**沿用 as-built 行為**（`monitor_signals.py:71-73`、`89-91`）。

理由：憲章 Security 節明定「不得漏發」。若資料階段也 fail-fast，單一標的的
暫時性錯誤會擴散成全部標的漏發。C4.1 已保證進入此階段時儲存必定可用，
故 fail-open 從常態路徑降級為最後一道防線。

**兩者的分界是本契約最不直觀之處**：同一種「儲存壞了」的現象，
在啟動期要硬失敗、在執行期要容忍。分界的正當性來自「啟動期能區分系統性故障與
偶發錯誤，執行期不能」。

---

## C5：快照契約

| 項目 | 約定 |
|---|---|
| 時序 | MUST 在該次所有寫入完成之後產生（FR-008） |
| 內容 | 累積儲存所有租戶表的**全量**匯出 |
| 還原 | MUST 可在無託管服務的乾淨環境還原，結果與匯出時**逐筆一致** |
| 去重 | 內容摘要與上一份相同時 MUST NOT 產生新提交（FR-009） |
| 失敗 | 產生失敗 MUST 使該次執行以非零碼結束（FR-010） |
| 憑證 | 快照及其中介資料 MUST NOT 含 token；URL 若記錄，MUST 去除憑證部分（FR-011） |

---

## C6：工作流層契約

| 環境變數 | 用途 | 缺少時 |
|---|---|---|
| `TURSO_DATABASE_URL` | 託管儲存位置 | 退化本機（與下者同時缺少才算「未設定」） |
| `TURSO_AUTH_TOKEN` | 憑證 | 同上 |

**只設其中一個** MUST 視為設定錯誤並以非零碼結束——這是組態錯誤，
不是「未設定」，不得靜默退化。

`alert_scheduler` 與 `daily_ingestion` 的行情快取 restore／save 步驟 **MUST 保持不變**
（FR-004）。行情資料的遷移屬階段二（issue #41）。
