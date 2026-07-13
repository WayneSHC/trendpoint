# Phase 1 Contracts: 資料層介面 / 命名 / 組態 / 護欄

無對外 API；契約為新領域介面、DataFrame 欄位契約、config schema 與護欄行為。

## C1 — `DataSourceAdapter`（新，`data_sources/base.py`）

```python
class DataSourceAdapter(ABC):
    source_key: str
    def fetch(self, instrument: Instrument, timeframe: str) -> pd.DataFrame: ...
```
- **回傳契約**：`open/high/low/close/volume` 欄 + `DatetimeIndex`（名 `datetime`、tz-naive、遞增）；因果（ffill-only、無 bfill）；正價、量≥0。已 rollover 拼接、已正規化。
- **分派**：`data_sources/__init__.py` 提供 `get_adapter(source_key) -> DataSourceAdapter`；未知鍵 fail-fast。
- **實作**：`YfinanceAdapter`（包 `fetch_stock_data`+`clean_kline_dataframe`，行為與現行一致）、`CsvAdapter`、`MockAdapter`。

## C2 — `Instrument` + Registry（新，`instruments.py`）

```python
class AssetClass(str, Enum): EQUITY="equity"; FUTURES="futures"
class Instrument(BaseModel, frozen=True):
    id: str; asset_class: AssetClass; source: str
    display_name: str = ""; timeframes: list[str] = ["daily"]

class InstrumentRegistry:
    def resolve(self, id: str) -> Instrument: ...
    def all(self) -> list[Instrument]: ...
```
- 由 config 建構：`data.tickers`（str→equity/yfinance）+ `data.instruments`（結構化）。`id` 唯一（衝突 fail-fast）。

## C3 — 表命名（`db_security.py`）

```python
def table_name_for(instrument: Instrument, timeframe: str) -> str: ...
# equity → f"stock_{clean_id}_{timeframe}"（與現行逐字元相同）
# futures → f"fut_{clean_id}_{timeframe}"
TABLE_NAME_PATTERN = r"^(stock|fut)_[a-zA-Z0-9_]+_(daily|5m)$"
```
- **相容性**：既有 `stock_*` 表名不變（不需重抓）；`validate_table_name` 沿用、regex 放寬。
- ~7 處散落導出改用 `table_name_for`（唯一導出點）。

## C4 — Config schema（`config/config.py` + `config.yaml`）

- `DataConfig` 新增 `instruments: list[InstrumentSpec]`（`InstrumentSpec`：id/asset_class/source/display_name/timeframes）；`tickers` 維持。
- `DataQualityConfig` 擴充 per-asset-class 門檻（`max_close_jump_ratio` 維持 3.0；可選 `by_asset_class`）。
- 皆 Pydantic 驗證；`config.yaml` 加一個範例 futures instrument（source=mock）。

## C5 — 資料契約驗證（`data_ingestion.py`）

```python
def validate_data_contract(df, *, asset_class="equity", ...) -> None: ...
```
- 離群門檻依 `asset_class` 取值；其餘規則（欄位、遞增、正價、量≥0、無 NaN）不變。
- `clean_kline_dataframe` ffill-only 契約不變。

## C6 — 回測護欄（`run_*.py` + `backtester.py`/`portfolio_backtester.py`）

- **入口層**：`run_backtest` / `run_portfolio_backtest` 於載入/ dispatch 前，`if instrument.asset_class == FUTURES: raise <明確錯誤>`。
- **引擎層**：`BacktestEngine.run_backtest(..., asset_class: str = "equity")`；`if asset_class == "futures": raise <明確錯誤>`。組合引擎同。**僅拒絕、不做成本/sizing**。
- **相容性**：`asset_class` 預設 `"equity"` → 既有呼叫零影響（parity）。

## C7 — 測試契約（`tests/`）

- `test_instrument_registry.py`：純字串→equity/yfinance；結構化 instruments；id 衝突 fail-fast。
- `test_data_sources.py`：三 adapter 皆符合 fetch 回傳契約；未知 source fail-fast。
- `test_table_naming.py`：equity 表名與現行相同；futures→`fut_*`；regex 接受兩者、拒絕非法。
- `test_futures_pipeline_e2e.py`：mock futures（含 rollover 跳空）ingest→驗證→存→載→符合資料契約。
- `test_futures_backtest_guard.py`：入口層 + 引擎層對 futures 皆拋錯；equity 正常。
- 既有全套 pytest（含 spec 004 parity）維持綠（equity 位元不變）。
