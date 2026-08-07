# Contract: CLI 命令

**Feature**: `016-intraday-evaluation-protocol`

入口為 `run_intraday_eval.py`。它**只做編排**——取數、合併、評估、渲染皆
委派給三個模組，CLI 本身不含判定邏輯（便於單元測試繞過 CLI 直接測邏輯）。

## 子命令

### `accumulate` —— 取數並併入累積歷史

```bash
python run_intraday_eval.py accumulate \
    --tickers "2330.TW 2454.TW" \
    --period 60d \
    --state-dir accumulated/ \
    [--offline-csv-dir data/]
```

| 參數 | 必要 | 說明 |
|---|---|---|
| `--tickers` | ✅ | 空白分隔。**取數目標**，非納入清單——納入由準則決定 |
| `--period` | | 預設 `60d`（Yahoo 對 5m 的硬上限） |
| `--state-dir` | ✅ | 累積歷史目錄；不存在時視為鏈結起點 |
| `--offline-csv-dir` | | 改由本機 CSV 併入，供測試與無網路環境使用 |

**行為**：對每個 ticker 取數 → 正規化 → 與既有累積合併（先到者為準）→
寫回 `--state-dir` → 更新 `chain_state.json`。
一檔失敗不影響其他檔；全部失敗時以非零碼結束。

**退出碼**：`0` 成功；`1` 有標的失敗；`2` 參數錯誤。

---

### `evaluate` —— 對累積歷史產出報告

```bash
python run_intraday_eval.py evaluate \
    --state-dir accumulated/ \
    --out-json artifacts/report.json \
    [--out-text artifacts/report.txt] \
    [--scale-sweep] \
    [--data-only]
```

| 參數 | 說明 |
|---|---|
| `--state-dir` | 累積歷史來源 |
| `--out-json` | 權威產出（契約見 `evaluation-report.md`） |
| `--out-text` | 由 JSON 渲染的人類可讀報表；省略時輸出至 stdout |
| `--scale-sweep` | 加跑參數尺度掃描（FR-017）；預設不跑（耗時） |
| `--data-only` | 只跑資料體質與納入準則，略過訊號與回測 |

**行為**：載入累積 → 套用納入準則 → 逐標的評估 → 切分窗口（不足則記錄差距）
→ 決定效力標籤 → 產出 JSON → 渲染文字。

**退出碼**：`0` 成功；`1` 無標的通過納入準則（明確失敗，不產出空報告）；
`2` 參數錯誤；`3` 累積歷史損毀（索引非遞增等後置條件違反）。

---

### `universe` —— 只印納入決定（除錯用）

```bash
python run_intraday_eval.py universe --state-dir accumulated/
```

輸出逐標的的 `included` / `failed_criteria` / `measured`，
供人工檢驗準則是否合理（FR-011）。

---

## 不變式（跨所有子命令）

1. **不寫 `config/config.yaml`**。組態覆寫全在記憶體內
   （沿用 `run_b_segment.py` 的既有慣例）。
2. **不寫 `trendpoint.db`**、不建立任何 SQLite 表。
3. **不觸發推播**。本工具與 `alerts.py` 無任何呼叫關係。
4. 相同 `--state-dir` 內容 + 相同參數 ⇒ 相同 `--out-json` 的
   `inputs` 與 `results` 區（SC-001）。

## 與既有入口的關係

`run_5m_evaluation.py` 保留為**單檔快速診斷**工具，但其 `verdict()`
的既定處方改為量測驅動（FR-018）——不得在無尺度掃描結果時輸出
「先做參數時框化」。跨標的、累積、切分、標籤一律走 `run_intraday_eval.py`。
