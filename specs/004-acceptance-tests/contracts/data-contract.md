# Contract: 資料契約強化（`validate_data_contract` / `clean_kline_dataframe`）

**Module**: `data_ingestion.py` | **Consumers**: `fetch_stock_data`、`tests/test_acceptance_data_quality.py`

## `validate_data_contract(df, *, quality: DataQualityConfig) -> bool`

現行簽名 `validate_data_contract(df) -> bool` 增加 keyword-only 參數
`quality`（預設從全域 config 載入的 `DataQualityConfig`，保留向後相容）。

### 規則（依序檢查；任一違反即 `raise ValueError`，不靜默過濾）

| # | 規則 | 現行 | 本規格 |
| :--- | :--- | :--- | :--- |
| 1 | 非空、DatetimeIndex、時序嚴格遞增 | ✅ | 不變 |
| 2 | 欄位 `open, high, low, close, volume` 齊全 | ✅ | 不變 |
| 3 | 上述欄位無 NaN | ✅ | 不變 |
| 4 | 價格欄位（open/high/low/close）非正值 | `< 0` 拒絕 | **改 `<= 0` 拒絕**（價格為 0 = 資料錯誤）；volume 仍允許 0 |
| 5 | 相鄰收盤跳動 | 無 | **新增**：`abs(df['close'].pct_change().iloc[1:]) > quality.max_close_jump_ratio` 任一成立 → 拒絕 |

### 警告契約

- 規則 4/5 違反時，raise 之前先
  `logger.warning(...)`，訊息含：標的無關的違規類型、首個違規時間戳、違規值。
- 測試以 `caplog` 斷言 `WARNING` 級紀錄存在且含違規時間戳。

### 降級路徑（不變）

`fetch_stock_data` 既有 try/except 捕捉 ValueError → 回傳 `None` →
呼叫端跳過該標的本輪處理。強化不新增任何降級分支。

## `clean_kline_dataframe(df) -> pd.DataFrame`

行為不變（reshape → ffill-only → dropna 頭部），僅警告機制遷移：

| 事件 | 現行 | 本規格 |
| :--- | :--- | :--- |
| 缺漏 K 線被 ffill | `print` | `logger.warning`，訊息含填補根數 |
| 頭部無法填補被 drop | `print` | `logger.warning`，訊息含捨棄根數 |

### 清洗後不變式（US3 場景 1 的斷言）

- 索引嚴格遞增、無重複。
- 輸出無 NaN（價格與 volume 欄位）。
- 對「中段缺 3 根」的輸入：填補值 == 缺漏前最後一根的值（ffill 語意，
  **禁止 bfill**——憲法 VI 與 2026-07-10 Critical 修復的回歸防線）。
- 清洗後餵入 `calculate_atr` 全鏈無 NaN（warm-up 段除外）。

## 組態契約

- `config/config.yaml` 新增 `data_quality.max_close_jump_ratio`（float，
  預設 `3.0`，`gt=0`），對應 Pydantic `DataQualityConfig`。
- `data_ingestion.py` 不得出現字面量閾值（憲法 V）。
