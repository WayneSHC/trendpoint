# Quickstart: 排程與持久化驗收指引

**Feature**: 012-scheduling-persistence | **Date**: 2026-07-31

本檔是**驗收操作指引**，不含實作碼。細節見
[spec.md](spec.md) / [data-model.md](data-model.md) / [contracts/](contracts/append-only-store-contract.md)。

**本案無外部前置條件**——不需註冊任何帳號、不需任何 Secrets。
全部驗收皆可在本機與 CI 完成。

---

## 前置

```bash
pip install -r requirements.txt
```

`requirements.txt` **不應新增任何資料庫驅動**（research.md R1）。
若有 `libsql` 之類的項目出現，即代表實作偏離了設計。

---

## 本機驗收（US2）

### 全套測試

```bash
pytest -q
```

**預期**：全綠（憲章第 2 條硬性關卡）。SC-002 要求通過率 100% 且對外請求數 0。

### 本機模式不 push

```bash
python monitor_signals.py --once
```

**預期**：輸出明示「本機模式，紀錄未推送」（C3）。
帳的變更留在工作目錄，由你決定是否提交：

```bash
git status --short ledger/
```

**預期**：若有新訊號則顯示 `M` 或 `??`；**不得**已被自動 commit。

### 去重仍然正確

連跑兩次，第二次不得重複推播同一 (ticker, bar_time, alert_type)：

```bash
python monitor_signals.py --once && python monitor_signals.py --once
```

### 去重鍵格式未被改動（C1，最重要的一條）

遷移後比對既有紀錄的三個去重鍵欄位是否逐字相同：

```bash
python - <<'PY'
import json, sqlite3, pathlib
old = sqlite3.connect("trendpoint.db").execute(
    "select ticker, bar_time, alert_type from sent_alerts").fetchall()
new = {(r["ticker"], r["bar_time"], r["alert_type"])
       for p in pathlib.Path("ledger").glob("*.jsonl")
       for r in map(json.loads, p.read_text().splitlines())
       if r.get("kind") == "sent_alert"}
missing = [t for t in old if t not in new]
print("既有筆數:", len(old), "／帳中比對不到:", len(missing))
assert not missing, f"去重鍵格式已漂移，會導致歷史訊號重發一輪：{missing[:3]}"
PY
```

**預期**：比對不到的筆數為 **0**。任何非零值都代表 C1 被違反。

### 只追加、不重寫

```bash
git log -p --follow ledger/ | grep -c '^-[^-]' || echo "0 行被刪除（正確）"
```

**預期**：帳的歷史中不應出現刪除行（C2）。

---

## CI 驗收（US1、US3）

### US1：行情快取遺失後不重複推播

```bash
gh cache list | head
gh cache delete --all
gh workflow run alert_scheduler.yml -f mode=once
```

**預期**（SC-001）：先前已推播的訊號**不再推播**——重複 0 筆、漏發 0 筆。
對照組：在本變更前，同樣操作會使該訊號重新推播一次
（因為去重表與行情同住在被刪掉的那個快取裡）。

### SC-005：行情重建不影響帳

```bash
gh cache delete --all
gh workflow run daily_ingestion.yml     # TXF 全歷史回填約 300+ 請求，耗時
```

**預期**：帳的筆數與內容**不變**（`git log ledger/` 無新提交）。

### US3：帳未落地要紅燈

以下任一種注入方式皆可，重點是驗證「不會靜默成功」：

- 暫時移除工作流的 `contents: write` 權限
- 或在測試中讓 push 持續失敗

**預期**（SC-004）：工作流以**非零結束碼**結束，訊息明指帳未落地。
**不得**回報 success。

### SC-007：併發不覆蓋

同時手動觸發兩條工作流（兩者都會追加帳）：

```bash
gh workflow run alert_scheduler.yml -f mode=once
gh workflow run daily_ingestion.yml
```

**預期**：兩次執行的紀錄**皆存在**，最終筆數 = 兩次新增筆數之和，
無任何一方被覆蓋。`git log --oneline ledger/` 應見兩筆提交（或一筆含兩者的 rebase 結果）。

### SC-003：可逐次追溯

```bash
git log -p ledger/
```

**預期**：每一次帳的變更皆可見，且任一歷史版本可還原
（`git show <sha>:ledger/YYYY-MM.jsonl`）。

---

## 驗收對照表

| SC | 驗收方式 | 需 CI |
|---|---|---|
| SC-001 重複／漏推播 0 | 清快取後重跑 | ✓ |
| SC-002 測試全綠、對外請求 0 | `pytest -q` | — |
| SC-003 可由 git 逐次追溯 | `git log -p ledger/` | — |
| SC-004 帳未落地即非零碼 | 注入推送失敗 | ✓ |
| SC-005 行情重建不影響帳 | 清快取後回填 | ✓ |
| SC-006 `[MANUAL]` 文件可回答三問 | 人工閱讀 spec「現行行為」節 | — |
| SC-007 併發不覆蓋 | 同時觸發兩條工作流 | ✓ |

憲章 III 要求每條驗收標準對應至少一個 pytest 測試，無法自動化者標 `[MANUAL]`。
SC-001／SC-004／SC-007 的**機制**須以注入式測試在本機覆蓋
（模擬快取遺失、模擬推送失敗、模擬併發追加）；CI 上的那一次執行屬人工確認。

**驗收有賞味期**：CI 上的一次綠燈只證明當下那一次。git 推送行為、
`actions/checkout` 的預設深度都可能隨版本改變——契約 C4／C6 的每一條
都要有離線測試鎖住，真實執行只作為補充證據。此教訓來自 010 的驗收經驗。
