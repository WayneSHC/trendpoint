# Quickstart: 排程與持久化驗收指引

**Feature**: 012-scheduling-persistence | **Date**: 2026-07-31

本檔是**驗收操作指引**，不含實作碼。細節見
[spec.md](spec.md) / [data-model.md](data-model.md) / [contracts/](contracts/append-only-store-contract.md)。

---

## 前置

```bash
pip install -r requirements.txt
```

驗收分兩層：**無憑證層**（US2，本機檔，不需外部服務）與
**託管層**（US1／US3／US4，需 repo owner 先註冊帳號）。

無憑證層可完整跑完，且**不得**發出任何對外請求——這是 US2 的重點。

---

## 第一層：無憑證驗收（US2，無外部依賴）

### 確認環境乾淨

```bash
env | grep -E 'TURSO_' || echo "無 TURSO_* 環境變數（正確）"
```

### 全套測試

```bash
pytest -q
```

**預期**：全綠（憲章第 2 條硬性關卡）。SC-002 要求通過率 100% 且對外請求數 0。

### 去重行為仍然正確

```bash
python monitor_signals.py --once
```

**預期**：輸出明示「目前使用本機儲存」（C4.1）。連跑兩次，第二次不得重複推播
同一 (ticker, bar_time, alert_type) 的訊號。

### 組態錯誤要被擋下

只設其中一個環境變數，應以非零碼結束而**非**靜默退化（C6）：

```bash
TURSO_DATABASE_URL=libsql://example.invalid python monitor_signals.py --once; echo "exit=$?"
```

**預期**：`exit` 非 0，訊息指出組態不完整。

---

## 第二層：託管驗收（US1／US3／US4）

### 前置（需 repo owner 手動完成，無法自動化）

1. 註冊託管服務帳號並建立資料庫
2. 於 GitHub repo 設定 Secrets：`TURSO_DATABASE_URL`、`TURSO_AUTH_TOKEN`
3. 本機驗收時以環境變數注入同兩個值（**勿寫入任何檔案**，憲章 Security 節）

### 遷移既有紀錄

```bash
python -c "print('遷移入口見 tasks.md；MUST 保持去重鍵三欄位值逐字相同（C1）')"
```

**驗收**：遷移前後 `sent_alerts` 的列數相同，且逐列比對三個主鍵欄位**完全一致**。
任何格式正規化都會導致歷史訊號被判為未發送而重發一輪（C1 的警告）。

### US1：紀錄不再靜默回退

```bash
gh cache list | head            # 觀察行情快取
gh cache delete --all           # 模擬快取被淘汰
```

然後重跑一次訊號檢測。

**預期**（SC-001）：先前已推播的訊號**不再推播**——重複推播 0 筆、漏推播 0 筆。
對照組：在本變更前，同樣操作會使該訊號重新推播一次。

### US4：有憑證但不可達要紅燈

```bash
TURSO_DATABASE_URL=libsql://unreachable.invalid \
TURSO_AUTH_TOKEN=dummy \
python monitor_signals.py --once; echo "exit=$?"
```

**預期**（SC-004）：`exit` 非 0，訊息明指「持久化失敗」，且**未**退化為本機檔
（檢查本機檔的 mtime 未變動）。

### US3：快照可獨立還原

取一次成功執行留下的快照，在乾淨目錄還原後比對。

**預期**（SC-003）：還原結果與該次執行後的線上內容**逐筆一致**，差異 0 筆。
另驗 FR-009：內容無變動時再跑一次，不得產生新的提交。

### SC-005：行情遺失不影響累積紀錄

```bash
gh cache delete --all
python run_ingestion.py          # 重建行情（TXF 全歷史回填約 300+ 請求，耗時）
```

**預期**：累積紀錄的筆數與內容**不變**。

---

## 驗收對照表

| SC | 驗收方式 | 需憑證 |
|---|---|---|
| SC-001 重複／漏推播 0 | 快取淘汰後重跑 | ✓ |
| SC-002 無憑證測試全綠、對外請求 0 | `pytest -q` | — |
| SC-003 快照逐筆一致 | 還原後比對 | ✓ |
| SC-004 不可達時非零碼 | 注入無效 URL | ✓ |
| SC-005 行情重建不影響紀錄 | 清快取後回填 | ✓ |
| SC-006 `[MANUAL]` 文件可回答三問 | 人工閱讀 spec「現行行為」節 | — |

憲章 III 要求每條驗收標準對應至少一個 pytest 測試，無法自動化者標 `[MANUAL]`。
SC-001／SC-003／SC-004 的**機制**須以注入式測試在無憑證環境覆蓋
（模擬回退、模擬不可達），真實環境的那一次執行屬人工確認。
