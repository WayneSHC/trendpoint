# Phase 1 Data Model: 台指期資料管線 + Instrument 抽象

本功能不改持久化 schema（沿用 SQLite OHLCV 表）；「實體」為新的領域物件與其驗證規則。

## E1 — Instrument（值物件，Pydantic frozen）

| 欄位 | 型別 | 說明 | 驗證 |
|---|---|---|---|
| `id` | str | 識別碼（如 `2330.TW`、`TXF`） | 非空；registry 內唯一 |
| `asset_class` | Enum(equity｜futures) | 資產類別 | 必填 |
| `source` | str | adapter 鍵（`yfinance`/`csv`/`mock`…） | 須為已註冊 adapter |
| `display_name` | str | 顯示名 | 預設 = id |
| `timeframes` | list[str] | 支援時框 | 預設 `["daily"]`；元素 ∈ {daily,5m} |

**不變式**：`Instrument` 為 008a 資料相關欄位；008b 擴充 point_value/合約/成本（不在本規格）。

## E2 — InstrumentRegistry

| 方法/欄位 | 說明 |
|---|---|
| 來源 | config `data.tickers`（純字串→equity/yfinance）+ `data.instruments`（結構化） |
| `resolve(id) -> Instrument` | 依 id 取 Instrument |
| `all() -> list[Instrument]` | 全部（供 ingestion 迭代） |

**不變式/驗證**：合併後 `id` 唯一（衝突 fail-fast）；純字串 ticker MUST 解析為 `equity`/`yfinance`（SC-005）。

**狀態**：無狀態轉移（宣告式）。

## E3 — DataSourceAdapter（介面）

| 成員 | 說明 |
|---|---|
| `source_key: str` | 註冊鍵 |
| `fetch(instrument, timeframe) -> pd.DataFrame` | 回傳已 rollover 拼接、已正規化的連續 OHLCV |

**契約（回傳 DataFrame）**：欄位 `open/high/low/close/volume`，`DatetimeIndex`（名 `datetime`、tz-naive、遞增），因果（ffill-only、無 bfill），正價、量≥0。

**不變式**：rollover/正規化為 adapter 內部責任；框架不檢視合約月。

## E4 — TableName（導出鍵）

| 規則 | 值 |
|---|---|
| equity | `stock_{clean_id}_{tf}`（與現行逐字元相同） |
| futures | `fut_{clean_id}_{tf}` |
| `clean_id` | `id.replace('.', '_').replace('/', '_')` |
| regex 白名單 | `^(stock\|fut)_[a-zA-Z0-9_]+_(daily\|5m)$` |

**不變式**：equity 表名不變（SC-001/parity）；helper 為唯一導出點（SC-003）。

## E5 — AssetClassValidationProfile

| 欄位 | 說明 |
|---|---|
| `max_close_jump_ratio` | per-asset-class 離群跳動門檻（equity 預設 3.0，維持；futures 可另設） |

**不變式**：equity 門檻不變；正價/量≥0 不因資產類別改變。

## E6 — 護欄（Guard）

| 層 | 行為 |
|---|---|
| 入口（`run_*.py`） | dispatch 前檢查 `asset_class`，futures → 拋明確錯誤 |
| 引擎（`BacktestEngine.run_backtest` 等） | 新增 `asset_class="equity"` 參數；`=="futures"` 拋明確錯誤（僅拒絕、不做成本） |

**不變式（SC-004）**：任一層對 futures 皆 fail-fast、零績效數字產出；`asset_class` 預設 equity → 既有呼叫零影響。
