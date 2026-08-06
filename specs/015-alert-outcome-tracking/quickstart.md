# 驗收指南：推播訊號的事後表現追蹤（A 段）

**Feature**: `specs/015-alert-outcome-tracking` | **Date**: 2026-08-06

---

## 前置

```bash
pytest -q                      # 基準：實作前須全綠
```

本案 **A 段全部驗收可離線完成**（合成資料即足），
僅 SC-022／SC-023 需真實資料與時間累積（見 §3）。

---

## 1. 驗收指令

```bash
# 全案測試
pytest -q tests/test_alert_outcomes.py tests/test_alert_outcomes_monitor.py

# 既有行為未退化（最關鍵的一項）
pytest -q                      # 須與實作前同樣全綠

# 開關關閉時的基準比對
pytest -q -k baseline

# 手動回填（不取數、不推播）
python monitor_signals.py --backfill-only

# 單次監控（開關開啟時會產生 alert_log/ 內容）
python monitor_signals.py --once

# 儀表板檢視
streamlit run app.py           # → 第五分頁「訊號事後表現」
```

---

## 2. SC ↔ 驗收方式對照

| SC | 驗收方式 | 測試檔 |
|---|---|---|
| SC-001 開關關閉逐筆一致 | 對固定資料跑監控，告警產出與訊息字串比對凍結基準；斷言 `alert_log/` 未被建立 | `test_alert_outcomes_monitor.py` |
| SC-002 記錄/回填故障不阻斷推播 | monkeypatch 使紀錄層拋例外，斷言推播仍送出、既有告警產出不變 | 同上 |
| SC-003 通知失敗仍記錄 | 令 `send_alert` 回傳 `False`，斷言紀錄存在且 `notified=false` | 同上 |
| SC-004 重複偵測單列 | 同一根 K 線重跑 N 次，斷言該主鍵筆數恆為 1 | 同上 |
| SC-005 `notified` 不降級 | 先成功推播（`true`），再令去重擋下重跑，斷言仍為 `true` | 同上 |
| SC-006 欄位完整性 | 斷言每列的鍵集合**恆等於**白名單（多一少一皆失敗） | `test_alert_outcomes.py` |
| SC-007 參數識別值 | 兩組參數 → 相異；同組跨多次呼叫 → 相同（含新行程） | 同上 |
| SC-008 時框分群與篩選 | 混入 `5m` 與 `daily` 紀錄，斷言分群互不混入；`summarize` 可篩選 | 同上 |
| SC-009 工作 DB 重建後紀錄完整 | 刪除並重建 `trendpoint.db`，斷言 `load_all` 結果不變 | `test_alert_outcomes_monitor.py` |
| SC-010 無事發生則零變更 | 無新告警且無可回填視窗時，斷言檔案 **mtime 與位元組**皆未變 | 同上 |
| SC-011 回填數值正確 | 構造含假日缺口的日線，斷言 T+N 取**交易日**而非日曆日、基準價為紀錄的 `close` | `test_alert_outcomes.py` |
| SC-012 回填不對外請求 | monkeypatch 網路層使任何呼叫即失敗，執行 `--backfill-only` 應成功 | 同上 |
| SC-013 回填冪等 | 重跑 N 次，斷言已回填值逐欄不變 | 同上 |
| SC-014 三態可區分 | 未到期／不足／缺漏皆為 `null`，且與 `ret=0.0` 可區分（序列化後仍成立） | 同上 |
| SC-015 方向調整對稱 | 空方下跌 → `ret_adj > 0`；以鏡像資料驗證與多方逐項對稱 | 同上 |
| SC-016 非策略績效標示 | 斷言分頁含標示字串，且不含任何回測 KPI 欄位名 | `test_alert_outcomes.py` |
| SC-017 樣本不足標示 | 樣本數 < `min_samples` 的群標示為不足且不顯示統計量，但**仍出現在輸出中** | 同上 |
| SC-018 參數集中/schema | 移除欄位或填非法值（`horizons` 非遞增、`log_dir` 以 `data/` 開頭）→ 載入即失敗 | 同上 |
| SC-019 零引用靜態檢查 | 掃描契約 §1.3 清單所列各檔，斷言**零**引用 `alert_outcomes` 與 `alert_log` | 同上 |
| SC-020 參數化查詢/無憑證 | 斷言無字串拼接 SQL；斷言欄位白名單不含憑證類鍵名 | 同上 |
| SC-021 `pytest -q` 全綠 | 全案跑完 | — |
| SC-022 **[MANUAL]** 頻率量測 | 見 §3 | — |
| SC-023 **[MANUAL]** 樣本門檻前不判讀 | 見 §3 | — |

---

## 3. `[MANUAL]` 步驟（需真實資料）

### SC-022：實跑一週量測告警頻率

**這是本案價值的第一個檢驗點**（spec Assumptions A-6）。

```bash
# 1. 開啟總開關
#    config/config.yaml → alerts.outcome_tracking.enabled: true
# 2. 確認排程 workflow 具備 contents: write 權限（A-9）
# 3. 等待一個完整交易週
# 4. 檢視累積結果
python monitor_signals.py --backfill-only
wc -l alert_log/*.jsonl
```

**須回填至 spec.md**：每週筆數、依 `alert_type` 的分布、依 `timeframe` 的分布。
**無論結果有利與否皆須如實記錄。** 若頻率低到樣本累積不具意義，
據此決定是否繼續投入——**而非默默保留**。

### SC-023：樣本門檻前不得判讀

在任一分群的樣本數達到 `min_samples` 之前，**不得**對前瞻報酬分布做結論性判讀。
首次判讀時須同時記錄樣本期間、標的清單與 `param_fingerprint`。

---

## 4. 實作時最容易踩的五個坑

依 research.md 的風險排序，實作與 review 時優先檢查：

1. **重構了七個告警分支**（D4）。看似該合併的重複是刻意留下的；
   合併會讓 SC-001 的驗證從「讀 diff」變成「證明重構等價」。
2. **把記錄點放進 `mark_alert_as_sent`**（D4）。該函式只在推播成功時被呼叫，
   語意是「已通知使用者」（`alerts.py:137`）。放進去 ⇒ 推播失敗的訊號永遠不被記錄
   ⇒ 直接違反 FR-001，且 SC-003 會抓到。
3. **用內建 `hash()` 做參數識別值**（D5）。per-process 隨機化，
   跨輪次不穩定，SC-007 會抓到——但只有在**新開行程**的測試中才會抓到，
   同一行程內測試會誤過。
4. **回填時把 `null` 寫成 `0.0`**（D6）。「還沒發生」與「報酬為零」混為一談，
   會讓分布統計出現大量假零。SC-014 專門守門。
5. **動了 `db_security.py:19` 的 `TABLE_NAME_PATTERN`**（契約 §6）。
   本案不新增 SQLite 表，出現這個念頭即代表偏離 D1 的設計。

---

## 5. 驗收環境切分

| 段 | 內容 | 驗收條件 | 本環境可否 |
|---|---|---|---|
| **A. 離線可完成** | 模組、monitor 接線、config、app 分頁、全部自動化測試 | SC-001 ~ SC-021（合成資料即足） | ✅ 可 |
| **B. 需真實資料與時間** | 實跑累積、頻率量測 | SC-022／SC-023（`[MANUAL]`） | ❌ 需本機 + 一週 |

**A 段用合成資料優於真實資料**：核心測試需要**精確控制**「假日缺口」
「T+5 尚未到期」「推播失敗」「同一根 K 線重複偵測」等情境，
真實資料無法保證這些一定出現在測試窗內。
