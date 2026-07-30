# Contract: BOS 量能確認濾網

**Modules**: `ladder_system.py` | **Consumers**: `backtester.py`、`monitor_signals.py`、`run_ablation.py`

本契約定義三個介面的擴充。原則：**所有新增參數皆有預設值，且預設值使既有行為
逐字不變**——這是 FR-002 在介面層的表述。

---

## 1. `calculate_volume_confirmation()`（新增函式）

```python
def calculate_volume_confirmation(df: pd.DataFrame,
                                  period: int = 20,
                                  mult: float = 1.5) -> pd.Series:
```

### 前置條件

- `df` 含 `volume` 欄，無負值（既有資料契約保證，`data_ingestion.py:157`）。
- `period >= 2`、`mult > 0`（呼叫端已由 Pydantic 驗證；函式不自行讀 config）。

### 後置條件

- 回傳 `Series[bool]`，index 與 `df` 相同，**無 NaN**。
- 第 i 個值只依賴 `df['volume'].iloc[:i+1]`——平均量部分只依賴 `iloc[:i]`
  （`.shift(1)`），判定根自身量可用。
- 前 `period` 個值恆為 `False`（rolling 未滿）。
- `vol_ma <= 0` 時為 `False`（不得退化為「一律通過」）。
- 不就地修改 `df`。

### 禁止事項

- **禁止**省略 `.shift(1)`（憲章原則 I，SC-006 釘死）。
- **禁止**依賴「與 NaN 比較恰好為 False」的隱性行為——須顯式 `notna()`
  （沿用 `ladder_system.py:645-649` 的既有明文教訓）。
- **禁止**在此函式內讀取 config 或硬編碼可調參數（憲章原則 V）。

---

## 2. `build_indicator_frame()`（簽名擴充）

```python
def build_indicator_frame(df, *,
                          structure_period: int,
                          ...,                        # 既有參數不變
                          volume_mult: float = 1.5,   # 既有（MSS displacement）
                          use_bos_volume: bool = False,      # 新增
                          bos_volume_mult: float = 1.5,      # 新增
                          bos_volume_period: int = 20) -> pd.DataFrame:
```

### 後置條件

- `use_bos_volume=True` → 輸出多一欄 `bos_volume_ok`（定義見
  [data-model.md](../data-model.md) §1）。
- `use_bos_volume=False` → **不輸出該欄**，其餘欄位與本案實作前**逐字相同**
  （欄名、順序、數值）。比照 `include_regime=False` 省略 `regime_ok` 的既有模式。
- **`mss_signal` / `bos_signal` 在兩種設定下數值完全相同**（FR-004、SC-002）。
  新參數不得傳入 `detect_market_structure`。
- 新欄滿足 spec 004 的前綴一致性不變式
  （`specs/004-acceptance-tests/contracts/indicator-frame.md` §時序契約 3）。

### 遷移契約

- 既有呼叫端（`backtester.py:192`、`monitor_signals.py:179`、
  `tests/test_acceptance_parity.py`）不傳新參數時，行為與實作前逐字相同。
- 新欄加入 `tests/test_acceptance_parity.py` 的 `PARITY_COLUMNS` 時，
  須僅在「啟用參數」的測試組生效，不得讓預設組因缺欄而失敗。

---

## 3. `PositionManager.check_entry_signal()`（簽名擴充）

```python
def check_entry_signal(self, close, open_val, daily_open, vwap, atr,
                       candle_high, candle_low, structure_sig,
                       global_filter_ok,
                       is_daily: bool = False,
                       disabled_filters: frozenset = frozenset(),
                       direction: int = 1,
                       volume_ok: bool = True) -> bool:      # 新增
```

### 語意

```text
volume_conf_ok = volume_ok or ('bos_volume' in disabled_filters)
return structure_ok and momentum_ok and trend_ok and volatility_ok \
       and global_ok and volume_conf_ok
```

### 後置條件

- `volume_ok=True`（預設）→ 回傳值與本案實作前**逐字相同**，對兩個 `direction`
  皆成立。這保證 `portfolio_backtester.py:385,390`、`validate_ladder.py:135`
  與六個既有測試檔零改動。
- `volume_ok` **無方向性**：多空兩側套用同一參數、同一語意（FR-006）。
  空方鏡像真值表（`tests/test_short_side.py:38-83`）須增此維度並保持對稱。
- `'bos_volume' in disabled_filters` → 該維度視為通過（消融語意，與既有五個
  消融鍵一致）。

### 呼叫端契約（FR-005 的落點）

| 呼叫點 | 傳入 | 理由 |
|---|---|---|
| `backtester.py:265`（BOS 多方） | `volume_ok=bool(sig_row['bos_volume_ok'])` | 續勢進場，套用 |
| `backtester.py:291`（BOS 空方） | 同上 | 續勢進場，鏡像套用 |
| `backtester.py:274`（MSS 多方） | **不傳** | 反轉分支已內建位移量能確認 |
| `backtester.py:301`（MSS 空方） | **不傳** | 同上 |
| `portfolio_backtester.py`、`validate_ladder.py` | **不傳** | 不套用此濾網（零改動） |

引擎取值前須確保欄位存在，缺欄以 `True` 回填（沿用 `backtester.py:211-214`
對 `regime_ok` 的既有處理）。

**取值的 timebase 必須與其餘四道確認一致**：既有 BOS 分支的價格類欄位取自
`sig_row`（判定根），故 `bos_volume_ok` 亦取自 `sig_row`，不得取 `struct_row`
（後者為 `iloc[i-2]`，會造成額外一根延遲）。

---

## 4. `run_ablation.py`（清單擴充）

```python
ABLATION_TARGETS = [
    ...,                                    # 既有 8 列不變
    ("停用 BOS 量能確認", "bos_volume"),    # 新增
]
```

### 契約

- 執行消融時 `use_bos_volume` 須為 `True`，否則該列與基準列完全相同、無資訊量。
- 濾網未啟用時，該列 **MUST** 明示「未啟用」而非靜默輸出與基準相同的數字
  （避免誤判為「這道濾網沒有影響」）。
- 該列的績效指標與基準列以同一資料、同一成本假設產出（既有 `run_ablation.py`
  流程已保證，本案不改動其成本路徑）。

---

## 5. `monitor_signals.check_new_signals()`（行為擴充）

### 契約

- 取 `cfg.strategy.get_params_for_ticker(ticker)`，將**本案三個新參數**穿線至
  `build_indicator_frame`。
- 濾網啟用時，BOS 告警（`monitor_signals.py:214-227`）須額外要求
  `latest_bar['bos_volume_ok']` 為真；MSS 告警不受影響。
- **`structure_period=10` / `use_fvg=True` / `fvg_lookback=3` 的既有硬編碼
  保持不動**（research.md D5）——順手改會污染預設行為，違反 FR-002。
- 濾網關閉時（預設），monitor 行為與實作前逐字相同。
