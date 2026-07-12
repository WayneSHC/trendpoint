# Contract: FVG 偵測與 MSS 閘門

**Module**: `ladder_system.py` | **Consumers**: `build_indicator_frame`、`detect_market_structure`、測試

## `_detect_fvg(df, direction) -> pd.Series`（新增，模組私有）

```python
def _detect_fvg(df: pd.DataFrame, direction: str) -> pd.Series:
    """回傳 bar t 是否形成 FVG 的布林序列（direction ∈ {"up","down"}）。"""
```

### 契約

- `direction="up"`：`df['low'] > df['high'].shift(2)`
- `direction="down"`：`df['high'] < df['low'].shift(2)`
- 回傳 dtype bool、與 `df.index` 對齊、前 2 根為 False（`shift(2)` NaN → False）
- 純向量化，無 Python 迴圈（憲法 IV）
- **因果**：bar t 的值只依賴 `df.iloc[:t+1]`（用 t 與 t−2）

## `detect_market_structure(df, period, *, use_fvg=False, fvg_lookback=3)`（擴充）

### 簽名變更

新增兩個 keyword-only 參數，**baseline-preserving 預設**：
- `use_fvg: bool = False`
- `fvg_lookback: int = 3`

回傳仍為 `Tuple[mss_signal, bos_signal]`（值域、dtype 不變）。

### 後置條件

- `use_fvg=False`：輸出與現行**逐位元相同**（raw MSS/BOS）。這是迴歸閘門
  的錨點——spec 001 基準重現。
- `use_fvg=True`：`mss_signal` 依 [data-model.md](../data-model.md) §2 真值表
  閘門（`+1` 需近 `fvg_lookback` 根有向上 FVG；`−1` 需向下 FVG；否則 0）。
  `bos_signal` **不受影響**。
- 時序契約：mss_signal 第 t 列只依賴 `df.iloc[:t+1]`（raw MSS 已 shift(1)、
  FVG 用 t/t−2）；引擎在 `iloc[i−2]` 消費，維持原一根延遲。

## `build_indicator_frame(..., use_fvg=False, fvg_lookback=3)`（擴充）

### 簽名變更

新增兩個 keyword-only 參數（預設同上），原樣轉發給 `detect_market_structure`。
`use_fvg` 預設 `False` 確保 004 既有 parity 呼叫（不帶 use_fvg）行為不變。

## 消融契約（`run_backtest` / `run_ablation.py`）

- `run_backtest` 新增 `use_fvg`、`fvg_lookback` kwargs；計算
  `effective_use_fvg = use_fvg and ('fvg' not in disabled_filters)`，
  傳入 `build_indicator_frame`（比照 `include_regime`，backtester.py:120）。
- `'fvg' in disabled_filters` ⇒ `effective_use_fvg=False` ⇒ mss 走原路徑 ⇒
  該次回測 = spec 001 基準（SC / Acceptance Scenario 3）。
- `run_ablation.py` 的 `ABLATION_TARGETS` 新增 `("停用 FVG 確認", "fvg")`。

## 測試契約（SC 對應）

- **SC-001**：五檔標的 `use_fvg=True` 下 MSS 計數 <（同標的）`use_fvg=False` 計數，
  且 > 0（實跑驗證，M 可調；見 research.md R3）。
- **SC-002**：`run_ablation.py` 輸出含「停用 FVG 確認」列，報告其對報酬/筆數/EV 的 delta。
- **SC-003**：`test_lookahead_bias.py` 新增 FVG tail-tamper 案例並通過；
  `test_acceptance_parity.py` 加 `use_fvg=True` 變體；`test_fvg_confirmation.py`
  驗證偵測邏輯與 `use_fvg=False` 零差異。
