# 元件契約：推播訊號的事後表現追蹤（A 段）

**Feature**: `specs/015-alert-outcome-tracking` | **Date**: 2026-08-06

本檔定義新模組 `alert_outcomes.py` 的對外契約，以及對既有檔案的接線契約。
簽名為契約層描述，實作細節屬 `/speckit-implement` 階段。

---

## 1. `alert_outcomes.py`（新模組）

### 1.1 純函式核心（無 I/O，可獨立單元測試）

#### `build_fingerprint(**params) -> str`

由監控端結構參數產生參數識別值。

- **輸入**：`structure_period`、`use_fvg`、`fvg_lookback`、`swing_n`、
  `volume_mult`、`use_bos_volume`、`bos_volume_mult`、`bos_volume_period`
- **輸出**：正規字串（data-model.md §3）
- **保證**：同參數 ⇒ 同字串（跨行程、跨執行）；異參數 ⇒ 異字串
- **禁止**：使用內建 `hash()`（per-process 隨機化，違反 SC-007）

#### `make_record(...) -> dict`

組裝一筆告警紀錄（尚未寫入）。

- **輸入**：ticker、bar_time、alert_type、timeframe、告警當下的 bar（含
  `close`／`ladder`／`upper_price`／`lower_price`／`atr`）、fingerprint
- **輸出**：符合 data-model.md §1.2 欄位白名單的 dict，
  `notified=False`、`outcomes` 三個 `null`
- **保證**：`direction` 由 `alert_type` 導出；輸出欄位集合**恆等於**白名單
  （多一欄或少一欄皆為契約違反，由測試斷言）
- **缺值**：bar 中不存在的指標欄位填 `null`，**不得**填 0

#### `merge_record(existing: dict | None, incoming: dict) -> dict`

upsert 的合併規則（D3）。

- `existing is None` → 回傳 `incoming`
- 否則：不可變欄位取 `existing`；`notified` 取 `existing or incoming`
  （**單向升級**）；`outcomes` 逐視窗取「`existing` 非 `null` 者優先」
- **保證**：冪等——`merge(merge(a,b),b) == merge(a,b)`

#### `compute_outcomes(record, daily_df, horizons) -> dict`

計算前瞻結果（D6）。

- **輸入**：紀錄、該標的日線 DataFrame（DatetimeIndex 遞增）、視窗清單
- **輸出**：`outcomes` 物件（data-model.md §2）
- **規則**：T+N = 日線索引中日期**嚴格大於** `bar_time` 日期的第 N 根；
  `ret = close_N / record["close"] − 1`；`ret_adj = ret × direction`
- **三態**：不足 N 根 → 該視窗 `null`；`daily_df` 為空或 `None` → 全 `null`
- **冪等**：已為物件的視窗**不重算**、原值回傳
- **禁止**：任何網路存取、任何對 `record` 的就地修改

#### `summarize(records, min_samples) -> DataFrame`

供 UI 呈現的分群統計（演算法留在模組內，UI 只呈現——CLAUDE.md 規則）。

- **分群鍵**：`alert_type` × `timeframe`（可再依 `param_fingerprint` 篩選）
- **輸出**：每群每視窗的樣本數、`ret_adj` 中位數、正報酬比例，
  以及 `sufficient: bool`（`樣本數 >= min_samples`，FR-018）
- **保證**：樣本數不足的群仍出現在輸出中，但統計量欄位標示為不足
  （**不得**靜默丟棄該群）

### 1.2 儲存層（薄 I/O）

#### `load_month(log_dir, year_month) -> list[dict]`

讀取單月分片。檔案不存在 → 回傳 `[]`（非例外）。

#### `upsert_records(log_dir, records) -> int`

依主鍵 upsert 並原子寫回，回傳**實際變更的列數**。

- **分片**：依 `bar_time` 年月分檔（非寫入時間）
- **排序**：寫回前依 `(bar_time, ticker, alert_type)` 排序（diff 穩定）
- **原子性**：暫存檔 + `os.replace`
- **零變更即零寫入**：無任何欄位改變時**不觸碰檔案**（FR-009／SC-010）
- **回傳 0** 即代表本輪無需 commit

#### `load_all(log_dir) -> list[dict]`

讀取全部分片，供回填與 UI 使用。

### 1.3 硬約束（FR-021／SC-019）

本模組 **MUST NOT** 被下列任一者 import：
`ladder_system.py`、`backtester.py`、`portfolio_backtester.py`、
`walk_forward.py`、`optimizer.py`、`monte_carlo.py`、`performance.py`、
`trading_costs.py`、`risk_gates.py`，及回測入口 `run_backtest.py`、
`run_portfolio_backtest.py`、`run_walk_forward.py`、`run_optimization.py`、
`run_ablation.py`、`run_b_segment.py`、`monte_carlo.py`。

**理由**：本模組持有告警**發生之後**的價格。任何進入訊號鏈的讀取路徑
都是未來函數的入口。由靜態檢查測試守門，不倚賴人工審查。

**允許的 import 方向**：`monitor_signals.py` → `alert_outcomes.py`、
`app.py` → `alert_outcomes.py`。反向 import 一律禁止
（`alert_outcomes.py` 不得 import `monitor_signals`／`backtester`／`ladder_system`）。

---

## 2. `monitor_signals.py`（行為擴充）

### 2.1 七個告警分支的插入點

於每個分支中 `alert_type` 確定後、`is_alert_already_sent(...)` 判定**之前**，
插入一次紀錄收集（D4）。

```
if latest_bar['mss_signal'] == 1:
    alert_type = "BULLISH_MSS"
    <── 收集紀錄（不論去重與推播結果）
    if not is_alert_already_sent(ticker, latest_time, alert_type):
        ...
        if alert_mgr.send_alert(...):
            mark_alert_as_sent(...)
            <── 標記 notified = True
```

**硬約束**：

- **不得重構這七個分支的既有結構**。既有重複看似該合併，但 SC-001 要求
  開關關閉時逐筆逐則逐欄相同，重構會讓該保證的驗證成本大幅上升（D4）。
- **不得改動** `monitor_signals.py:167` 的 5 分線取數、
  `:194-199` 的 `build_indicator_frame` 呼叫、`:207-212` 的已收盤棒選取，
  以及既有七種告警的判定條件與訊息字串。
- 紀錄的寫回在**函式尾端一次完成**（單次檔案 I/O），
  而非每個分支各寫一次。

### 2.2 回填接線

輪詢開始時對既有紀錄執行回填（D7）；新增 `--backfill-only` 旗標，
只回填、不取數、不推播。

### 2.3 故障隔離（FR-010／SC-002）

紀錄層與回填層的**任何**例外都必須被捕捉且不向上傳播——
推播與既有告警判定不受影響。捕捉後印一行提示即可，比照既有
`init_sent_alerts_db` 的例外處理風格（`monitor_signals.py:53-55`）。

### 2.4 總開關（FR-019／SC-001）

`alerts.outcome_tracking.enabled` 為 `False` 時：
**不 import 成本以外的任何行為**——不建立目錄、不讀檔、不寫檔、不回填。
既有行為逐筆逐則逐欄與實作前相同。

---

## 3. `app.py`（新增唯讀分頁）

現行四分頁（`app.py:621`）擴充為五：
`["價格與訊號", "績效分析", "風險分析", "交易日誌", "訊號事後表現"]`。

**契約**：

- 呼叫 `alert_outcomes.summarize(...)` 取結果，UI **只負責呈現**
  （CLAUDE.md：UI 層不得內嵌演算法邏輯）
- **必須**顯示「非策略績效」標示：不含手續費／稅／滑價，無出場規則，
  未經樣本外驗證（FR-017／SC-016）
- **不得**在此分頁呈現任何回測 KPI 欄位（總報酬、Sharpe、MDD、Calmar、
  Profit Factor 等）——與回測分頁視覺並列即為違反
- 樣本不足的群顯示為「樣本不足（n/min_samples）」而非統計數值（FR-018／SC-017）
- 提供依 `timeframe` 與 `param_fingerprint` 的篩選（FR-016／SC-008）
- 紀錄為空時顯示引導訊息，不報錯

---

## 4. `config` schema（新增子區塊）

見 data-model.md §5。契約要點：

- 掛於既有 `alerts` 區塊下，**不動** `ma_alerts_enabled`
- `enabled` 預設 `false`
- `log_dir` 驗證**不得**以 `data/` 開頭（該目錄整體 gitignored，D2）
- `horizons` 須為遞增正整數、非空
- 非法值 → 載入即失敗（fail-fast，SC-018）

---

## 5. `.github/workflows/alert_scheduler.yml`（新增 commit 步驟）

- 宣告 `permissions: contents: write`（現行未宣告，A-9）
- 推播步驟之後新增 commit 步驟，**僅在 `alert_log/` 確有變更時**執行
- commit 訊息含 `[skip ci]`（避免每次告警觸發 `tests.yml`，D9）
- push 前 `pull --rebase`；失敗時**不阻斷**該輪推播，留待下輪一併提交
  （upsert 冪等，重覆提交無害）

---

## 6. 零改動清單

以下檔案在本案中**不得**有任何改動：

`ladder_system.py`、`backtester.py`、`portfolio_backtester.py`、
`walk_forward.py`、`optimizer.py`、`monte_carlo.py`、`performance.py`、
`trading_costs.py`、`risk_gates.py`、`instruments.py`、`data_ingestion.py`、
`db_security.py`、`data_sources/`、`ma_lines.py`、`alerts.py`。

**特別注意 `db_security.py`**：本案不新增 SQLite 表，
故 `TABLE_NAME_PATTERN`（`db_security.py:19`）**不需要**也**不得**放寬。
若實作過程中出現「要改這個 regex」的念頭，代表偏離了 D1 的設計。
