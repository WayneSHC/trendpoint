# Data Model: 驗收標準自動化測試套件（spec 004）

**Date**: 2026-07-12 | **Prerequisite**: [research.md](research.md)

本功能不新增持久化實體；「資料模型」指三樣東西：
指標框架的欄位契約、資料品質組態、測試用合成 K 線模型。

## 1. IndicatorFrame（`build_indicator_frame()` 的輸出契約）

輸入：符合資料契約的 K 線 DataFrame（DatetimeIndex 遞增，
欄位 `open, high, low, close, volume`）。輸出：原 df 加上下列欄位
（完整時序語意見 [contracts/indicator-frame.md](contracts/indicator-frame.md)）：

| 欄位 | 型別 | 來源函式 | 時序語意 |
| :--- | :--- | :--- | :--- |
| `atr` | float64 | `calculate_atr(tr, period)` | Wilder 遞迴；前 `period-1` 根為 NaN（warm-up） |
| `vwap` | float64 | `calculate_vwap(df)` | 依日期分組累計；volume=0 → NaN → ffill |
| `mss_signal` | bool | `detect_market_structure(df, period)` | rolling `.shift(1)`——只看已收盤前 N 根 |
| `bos_signal` | bool | 同上 | 同上 |
| `ladder` | float64 | `calculate_ladder_levels(df, atr, k)` | 遞迴內取 `atr[i-1]`；只隨 BOS 方向階梯移動 |
| `chandelier_long` | float64 | `calculate_chandelier_exit(df, atr, ...)` | **不 shift**——呼叫端負責取前一根（引擎 timebase 慣例） |
| `chandelier_short` | float64 | 同上 | 同上 |
| `date` | date | groupby 輔助欄 | — |
| `daily_open` | float64 | 當日第一根 open | 盤中趨勢濾網用 |
| `yesterday_high` / `yesterday_low` | float64 | 日高低 groupby 後 `.shift(1)` 對映 | 只用昨日完成值 |
| `mid_price` / `upper_price` / `lower_price` | float64 | 三關價公式（中=(H+L)/2、上=L+(H−L)×1.382、下=H−(H−L)×1.382） | 輸入為 yesterday_high/low，天然無看前 |
| `regime_ok` | bool | `calculate_regime_filter(...)`（`include_regime=True` 時） | 沿用現行 backtester 語意 |

**不變式（Parity 測試斷言的對象）**：對任意截斷點 i，
`build_indicator_frame(df.iloc[:i]).iloc[-1]` ==
`build_indicator_frame(df).iloc[i-1]`（上表所有數值/布林欄位，零容差）。

## 2. DataQualityConfig（新 Pydantic 模型 + config.yaml 區塊）

```yaml
# config/config.yaml 新增
data_quality:
  max_close_jump_ratio: 3.0   # 相鄰收盤 |pct_change| 超過即判資料離群，拒絕整批
```

```python
class DataQualityConfig(BaseModel):
    max_close_jump_ratio: float = Field(3.0, gt=0.0)
```

驗證規則（強化後的 `validate_data_contract`，完整見
[contracts/data-contract.md](contracts/data-contract.md)）：

| 規則 | 現行 | 強化後 |
| :--- | :--- | :--- |
| 價格欄位負值 | 拒絕（`< 0`） | 拒絕（改 `<= 0`，價格為 0 亦拒絕；volume 允許 0） |
| 相鄰收盤跳動 | 無檢查 | `abs(pct_change) > max_close_jump_ratio` → raise + logging.warning |
| 缺漏（NaN） | 拒絕 | 不變（清洗在 `clean_kline_dataframe` 先行） |
| 欄位齊全 / DatetimeIndex / 非空 | 拒絕 | 不變 |

狀態轉移：`fetch_stock_data` 的既有 try/except 使「驗證失敗」降級為
「該標的本輪跳過（回傳 None）」——強化不改變此降級路徑。

## 3. SyntheticKlines（tests/acceptance_fixtures.py 的合成資料模型）

固定 seed 的隨機漫步 K 線建構器，沿用既有測試檔的慣例並集中為共用模組：

| 變體 | 用途 | 形狀 |
| :--- | :--- | :--- |
| `make_klines(n, freq)` | Parity / 延遲基底 | 隨機漫步 close ± 日內雜訊生成 OHLC；volume 對數常態；`freq="5min"` 或 `"1D"` |
| `make_klines_with_gap(n, gap_at, gap_len=3)` | US3 場景 1 | 中段 `gap_len` 根整列 NaN |
| `make_klines_with_outlier(n, at, kind)` | US3 場景 2 | `kind="zero"`：該根收盤=0；`kind="spike"`：×1000 |
| 10,000 根版 | US2 壓力情境 | `make_klines(10_000, "5min")` |

約束：所有變體 `np.random.seed` 固定、不觸網、生成時間 < 1s；
OHLC 滿足 `low <= min(open, close) <= max(open, close) <= high`（離群變體除外）。
