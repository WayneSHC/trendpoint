# Phase 0 Research: 台指期資料管線 + Instrument 抽象

解析設計未定項，逐條 Decision / Rationale / Alternatives。基於 code seam 分析（現行：`ticker` 裸字串 = yfinance symbol → `stock_*` 表 → 現貨、賣出課稅、long-only；無資產類別抽象）。

## D1 — Instrument 值物件形態

- **Decision**: `Instrument` 為 Pydantic model（frozen），欄位：`id`、`asset_class`（Enum: equity｜futures）、`source`（adapter 鍵字串）、`display_name`、`timeframes`（list，預設 `["daily"]`）。008b 再擴充 point_value/合約/成本檔。
- **Rationale**: 與既有 config 全走 Pydantic 一致（憲章 V）；frozen 值物件語意清楚、可測。
- **Alternatives**: dataclass（否決——與 config 驗證體系不一致）；dict（否決——無型別/驗證）。

## D2 — Registry 與向後相容

- **Decision**: config 的 `data.tickers`（純字串 list）維持，解析為 `asset_class=equity`、`source=yfinance` 的 Instrument；新增 `data.instruments`（結構化 list，宣告 futures/明確 instrument）。兩者合併為單一 registry；`id` 不得衝突（fail-fast）。registry 提供 `resolve(id) -> Instrument` 與 `all() -> list[Instrument]`。
- **Rationale**: 既有 config 零改即可載入（SC-005）；漸進式，不強迫一次遷移。
- **Alternatives**: 把 tickers 全遷入 instruments（否決——破壞向後相容、非必要）。

## D3 — DataSource adapter 介面

- **Decision**: 抽象 `DataSourceAdapter.fetch(instrument, timeframe) -> pd.DataFrame`，回傳**已 rollover 拼接、已正規化**的連續 OHLCV（標準欄位 + `datetime` 索引）。adapter 依 `source` 鍵註冊/分派（`data_sources/__init__.py`）。實作：`YfinanceAdapter`（包 `fetch_stock_data`+`clean_kline_dataframe`）、`CsvAdapter`（讀檔正規化）、`MockAdapter`（確定性序列）。
- **Rationale**: 框架保持資產類別無關（rollover 歸 adapter，已決）；介面窄、易測、易加來源。
- **Alternatives**: 框架內建 rollover 引擎（已於 brainstorm 否決）。

## D4 — 集中表命名 + regex 放寬

- **Decision**: 新 helper `table_name_for(instrument, timeframe) -> str`。equity → `stock_{clean_id}_{tf}`（`clean_id = id.replace('.', '_').replace('/', '_')`，**與現行完全相同**，不重抓）；futures → `fut_{clean_id}_{tf}`。`db_security.TABLE_NAME_PATTERN` 由 `^stock_[a-zA-Z0-9_]+_(daily|5m)$` 放寬為 `^(stock|fut)_[a-zA-Z0-9_]+_(daily|5m)$`。既有 ~7 處散落導出（`run_backtest`/`run_portfolio_backtest`/`optimizer`/`run_ablation`/`run_walk_forward`/`run_ingestion`/`app.py`）改用 helper。
- **Rationale**: equity 命名逐字元不變 → parity 保護（SC-001）；集中化消除散落漂移。
- **Alternatives**: 泛化為 `{asset}_{id}_{tf}`（否決——會改動 equity 既有表名、需重抓資料）。

## D5 — asset-aware 資料契約驗證

- **Decision**: `validate_data_contract` 的離群跳動門檻（`max_close_jump_ratio`）改為 per-asset-class 可查（config 提供 `data_quality.max_close_jump_ratio`（equity 預設 3.0，維持）與可選 `by_asset_class.futures`）。正價/量≥0 規則對指數期貨仍成立、不變。
- **Rationale**: 股票的 ±10% 漲跌停校準門檻不必然適用期貨連續序列（含 rollover 跳空）；per-asset 可調避免誤判。
- **Alternatives**: 單一全域門檻（否決——rollover 跳空恐被誤判為壞資料）。

## D6 — Fail-fast 護欄（雙層，clarify 決策）

- **Decision**: (a) **入口層**：`run_backtest.py` / `run_portfolio_backtest.py` 在 dispatch 到引擎前，檢查 instrument.asset_class，futures 即拋 `FuturesBacktestNotSupportedError`（或等效明確錯誤）。(b) **引擎層**：`BacktestEngine.run_backtest` / portfolio 引擎方法新增可選參數 `asset_class: str = "equity"`，若為 `"futures"` 立即拋同型錯誤——**僅拒絕、不引入成本/Instrument/sizing 依賴**。
- **Rationale**: 使用者選雙保險；引擎層護欄僅需一個字串旗標，不違反「成本 dispatch 順延 008b」。預設 `equity` 保證既有呼叫零影響。
- **Alternatives**: 只入口層（使用者否決）；引擎穿完整 Instrument（否決——008b 才做）。

## D7 — SQLite 整表覆蓋沿用

- **Decision**: 維持現行 `to_sql(if_exists="replace")` 整表覆蓋。
- **Rationale**: back-adjust 連續序列於換月時會回溯改動整段調整後歷史，本就需全量重算；整表覆蓋剛好正確。adapter 交付何種調整法（back-adjust/比例/不調）為 adapter 內部事，框架不需知。
- **Alternatives**: 增量 append（否決——對 back-adjust 序列不正確）。

## D8 — Mock adapter 的真實怪癖

- **Decision**: `MockAdapter` 產生確定性連續序列，**刻意含一段 rollover 跳空**（單日大幅缺口）與正常量能，讓抽象在「真形狀」資料上受測，而非乾淨隨機漫步。
- **Rationale**: de-risk——在真 TAIFEX adapter 到位前，先讓驗證/命名/e2e 面對期貨特性（跳空），確認 per-asset 驗證門檻（D5）與資料契約成立。
- **Alternatives**: 乾淨隨機序列（否決——測不到期貨怪癖）。
