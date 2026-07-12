# Contract: `build_indicator_frame()`

**Module**: `ladder_system.py`（新增） | **Consumers**: `backtester.py`、`monitor_signals.py`、`tests/test_acceptance_parity.py`

## 簽名

```python
def build_indicator_frame(
    df: pd.DataFrame,
    *,
    structure_period: int,
    atr_period: int = 14,
    ladder_k: float = 2.0,
    chandelier_period: int = 22,
    chandelier_multiplier: float = 3.0,
    include_regime: bool = True,
) -> pd.DataFrame:
```

## 前置條件

- `df` 已通過 `validate_data_contract`：DatetimeIndex 嚴格遞增、
  欄位 `open, high, low, close, volume` 齊全、無 NaN、無非正價格。
- 參數值一律由呼叫端從 config（Pydantic 驗證後）取得——本函式不讀 config、
  不含任何硬編碼可調參數（憲法 V）。

## 後置條件

- 回傳**新** DataFrame（不就地修改輸入），含 [data-model.md](../data-model.md) §1 全部欄位。
- `include_regime=False` 時省略 `regime_ok`（monitor 現行不需要 regime；
  其餘欄位兩端一致）。
- 欄位名以 backtester 現行為正典：`mss_signal`/`bos_signal`
  （monitor 端的 `mss`/`bos` 別名於重構時移除）。

## 時序契約（憲法 I 的函式級表述）

1. 第 i 列的所有欄位值只依賴 `df.iloc[:i+1]`（含第 i 根本身，
   但結構訊號 rolling 已 `.shift(1)`、三關價只用昨日值）。
2. `chandelier_long/short` **不做 shift**——與現行引擎慣例一致，
   呼叫端（backtester 的 `struct_row = iloc[i-2]`、monitor 的已收盤列選取）
   負責 timebase；本函式不得擅自加 shift（會造成雙重延遲）。
3. **前綴一致性（Parity 不變式）**：
   `build_indicator_frame(df.iloc[:i], **p).iloc[-1]`
   必須等於 `build_indicator_frame(df, **p).iloc[i-1]`，零容差。

## 遷移契約（重構閘門）

- `backtester.py:118-163` 與 `monitor_signals.py:117-141` 的內聯邏輯
  **逐行搬移**，不得順手改語意（含看似冗餘的中間欄位）。
- 合併前必附：
  - 前後回測對照（同資料、同成本、同參數）：每檔交易筆數相同、
    每筆成交價逐筆相同、組合總報酬相同。
  - monitor 判定迴歸：對同一固定 df，重構前後 `check_new_signals`
    產出的訊號集合相同。
- 有／無 Numba 兩模式輸出一致（CI uninstall-rerun 覆蓋）。
