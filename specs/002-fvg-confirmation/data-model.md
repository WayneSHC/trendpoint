# Data Model: MSS 之 FVG 確認（spec 002）

**Date**: 2026-07-12 | **Prerequisite**: [research.md](research.md)

本功能不新增持久化實體。「資料模型」= FVG 欄位語意、新參數、mss 閘門真值表。

## 1. FVG 布林欄位（`_detect_fvg` 的輸出）

於 bar t（皆用已收盤 bar，因果）：

| 概念 | 定義 | 向量化 |
| :--- | :--- | :--- |
| 向上 FVG（bullish gap） | `low(t) > high(t-2)` | `df['low'] > df['high'].shift(2)` |
| 向下 FVG（bearish gap） | `high(t) < low(t-2)` | `df['high'] < df['low'].shift(2)` |
| 近 M 根有向上 FVG | 任一 s∈[t−M+1, t] 成立 | `fvg_up.rolling(M).max().astype(bool)` |
| 近 M 根有向下 FVG | 同上 | `fvg_down.rolling(M).max().astype(bool)` |

序列前 2 根（`shift(2)` 為 NaN）：FVG 布林為 False（不足三根不成 FVG，
spec Edge Case）。`rolling(M)` 前 M−1 根 `min_periods` 沿用預設（不足窗回 NaN
→ `.astype(bool)` 前先 `.fillna(False)`）。

**這些是中間欄位**，不一定寫入 IndicatorFrame；可僅在 `detect_market_structure`
內部計算後即用即棄，或以 `fvg_up_signal`/`fvg_down_signal` 欄位輸出供偵錯
（實作可選，契約只要求 `mss_signal` 正確）。

## 2. mss_signal 閘門真值表（`use_fvg=True`）

| raw_mss | 近 M 根同向 FVG | 輸出 mss_signal |
| :---: | :---: | :---: |
| +1（看漲 MSS） | 有向上 FVG | **+1** |
| +1 | 無向上 FVG | **0**（假訊號被濾） |
| −1（看跌 MSS） | 有向下 FVG | **−1** |
| −1 | 無向下 FVG | **0** |
| 0 | — | **0** |

`use_fvg=False`：`mss_signal = raw_mss`（spec 001 基準，逐位元不變）。
**BOS 不受影響**：`bos_signal` 在任何 use_fvg 值下皆為現行邏輯輸出。

## 3. 新組態參數（`SingleStrategyParams` + config.yaml）

```yaml
# config/config.yaml, strategy.default（per-ticker override 自動生效）
strategy:
  default:
    use_fvg: true
    fvg_lookback: 3
```

```python
# config/config.py, SingleStrategyParams
use_fvg: bool = Field(default=True, description="MSS 是否需 FVG 確認")
fvg_lookback: int = Field(default=3, ge=1, description="FVG 回看根數 M")
```

穿線路徑（比照 `atr_period`，見 research.md R5）：
`config.yaml` → `get_params_for_ticker` → `run_backtest.py`/`run_ablation.py`
→ `run_backtest(..., use_fvg=, fvg_lookback=)` → `build_indicator_frame`
→ `detect_market_structure`。

**消融語意**：`run_backtest` 內
`effective_use_fvg = use_fvg and ('fvg' not in disabled_filters)`，
再傳給 `build_indicator_frame`（比照 `include_regime`）。

## 4. 受影響的既有欄位不變式

- `mss_signal`：仍 ∈ {−1, 0, +1}；FVG 只會把 ±1 收斂為 0，不新增值域。
- `bos_signal`、`ladder`、`chandelier_*`、三關價、`atr`、`vwap`：完全不變。
- 004 parity 不變式對 `mss_signal` 續成立（含 `use_fvg=True` 變體）——
  FVG 全因果，前綴一致性零容差維持。

## 5. 合成資料需求（測試用）

- FVG 單元測試：手工三根缺口 df（一組有向上 FVG + 看漲 raw_mss、
  一組有 raw_mss 但無 FVG），斷言閘門真值表。
- parity/tail-tamper：沿用既有合成產生器（`tests/acceptance_fixtures.py`
  的 `make_klines`、`test_lookahead_bias.py` 的 `_generate_mock_data`），
  FVG 欄位由隨機 OHLC 自然驅動。
