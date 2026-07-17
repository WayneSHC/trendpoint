# Interface Contracts: 真實台指期資料源（010）

**Phase 1 產出** | 值語意見 [data-model.md](../data-model.md)。

## `data_sources/rollover.py`（純函式，兩 adapter 共用）

```python
def select_front_month(raw: pd.DataFrame) -> pd.Series: ...
    # 輸入 raw（date×contract 長表）；輸出 date→contract 之近月序列。
    # 規則：第 k 日近月 = 依第 k−1 日各契約成交量判定（次月量>現行近月 → 換）；
    # 單向不回切；首日取當日量最大月（唯一允許用當日資訊的初始化點）。
def compute_roll_events(raw, front: pd.Series) -> list[RollEvent]: ...
    # (roll_date, from_c, to_c, adjustment=new(k−1 close)−old(k−1 close))
def build_continuous(raw, front, events) -> pd.DataFrame: ...
    # 連續 OHLCV：各日取近月列，roll 事件差額回溯累積平移（O/H/L/C 同步平移、量不動）。
```

- **契約測試錨定例**（SC-002）：3 契約手造量序列——交叉日 k → k+1 起換月；
  差額平移後 Δclose 逐日等於同契約真實 Δ；截斷任意尾段 → 前段 front 序列不變；
  量回落不回切。

## `data_sources/taifex_source.py`

```python
class TaifexAdapter(DataSourceAdapter):
    source_key = "taifex"
    def fetch(self, instrument, timeframe) -> pd.DataFrame: ...
        # 008a 契約不變：回傳連續序列（內部：fetch_raw 全區間 → rollover 三步）。
        # timeframe 僅支援 "daily"（其餘 ValueError）。
    def fetch_raw(self, instrument, timeframe, start: date, end: date) -> pd.DataFrame: ...
        # 逐月 POST futDataDown（Big5 解碼、過濾一般時段與週契約、正規化欄位）；
        # 每請求間 sleep(throttle_seconds)；單請求失敗重試 max_retries 次後拋 RuntimeError。
    def fetch_latest(self, instrument) -> pd.DataFrame: ...
        # OpenAPI /v1/DailyMarketReportFut（JSON）→ 當日 raw 列（欄位同 fetch_raw）。
```

- 解析 fail-fast：預期欄位缺失 → ValueError 含實際欄位清單（SC-003）。
- 建構參數自 `config.data.futures_source`（工廠/ingestion 注入，不讀全域）。

## `data_sources/finmind_source.py`

```python
class FinMindAdapter(DataSourceAdapter):
    source_key = "finmind"
    def fetch(self, instrument, timeframe) -> pd.DataFrame: ...      # 連續序列（同 rollover）
    def fetch_raw(self, instrument, timeframe, start, end) -> pd.DataFrame: ...
        # GET api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesDaily&data_id=TX...
        # token = os.environ["FINMIND_TOKEN"]；缺失 → MissingTokenError（驗證器捕捉後跳過）
```

## `verify_futures_data.py`

```python
def cross_verify(start: date, end: date, tolerance: float) -> VerifyReport: ...
    # 兩源 fetch_raw 重疊區間 → 逐（date×contract×欄位）比對；|diff|>tolerance → 告警列。
    # FinMind 不可用 → VerifyReport(skipped=True, reason=...)，退出碼 0。
# CLI: python verify_futures_data.py --start 2023-01-01 --end 2023-03-31
# run_ingestion --verify 呼叫同一函式。
```

## `run_ingestion.py` futures 真源分流

- instrument.source == "taifex"：raw 表空 → 回填（backfill_start～今日）；非空 →
  自 raw 最後日期 +1 補至今日（缺口 ≤ 可用 fetch_raw 區間補、當日可用 fetch_latest）。
- 每次 raw 更新後：重建連續序列 → `validate_data_contract`（futures 連續放寬正價）→
  `save_to_sqlite` 整表覆蓋 `fut_TXF_daily`；raw 以（date×contract）冪等寫入
  `raw_table_name_for(inst, tf)`。
- mock/csv/yfinance instrument 路徑**逐字不變**。

## `db_security.raw_table_name_for(instrument, timeframe)`

- 回傳 `fut_{clean_id}_raw_{tf}`（如 `fut_TXF_raw_daily`）；經現行
  `TABLE_NAME_PATTERN` 驗證（已容納，零 regex 改動）；equity → ValueError（raw 層僅期貨）。

## SC ↔ 契約對映

| SC | 契約段落 | 測試 |
|----|----------|------|
| SC-001 | TaifexAdapter + ingestion 分流 | `test_taifex_source.py`（network 標記）|
| SC-002 | rollover 三函式錨定例 | `test_rollover.py` |
| SC-003 | 解析 + fail-fast（離線 fixture） | `test_taifex_source.py` / `test_finmind_source.py` |
| SC-004 | cross_verify + skipped 語意 | `test_verify_futures.py` |
| SC-005 | mock 路徑零觸碰 | 既有全套 pytest |
| SC-006 | fetch 契約不變 + 無 MOCK 前綴 | `test_real_data_integration.py`（離線注入） |
| SC-007 | validate_data_contract 放寬面 | `test_real_data_integration.py` |
| SC-008 | 冪等 + 重試 fail-fast | `test_taifex_source.py`（離線 mock HTTP） |
