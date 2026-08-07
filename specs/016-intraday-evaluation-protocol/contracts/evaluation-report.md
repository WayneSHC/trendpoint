# Contract: 評估報告 JSON

**Feature**: `016-intraday-evaluation-protocol`

JSON 是報告的**權威格式**；文字報表由它渲染，不獨立計算
（兩條計算路徑必然漂移）。SC-001 的逐欄比對以本結構為對象。

## 頂層結構

```json
{
  "schema_version": "1",
  "inputs":     { ... },
  "results":    { ... },
  "provenance": { ... }
}
```

| 區 | 納入確定性比對（SC-001） |
|---|---|
| `inputs` | ✅ |
| `results` | ✅ |
| `provenance` | ❌ —— 執行時間戳、run id、主機、git sha |

## inputs

```json
{
  "accumulated_fingerprints": {"2330.TW": "<sha256>"},
  "chain_origin": "2026-05-14 09:05:00",
  "chain_broken": false,
  "actual_span": {"2330.TW": {"first_ts": "...", "last_ts": "...", "trading_days": 59}},
  "criteria_version": "v1",
  "label_thresholds": {"min_test_windows": 3, "min_trades_per_window": 30},
  "strategy_params": {"atr_period": 14, "ma_period": 200, "...": "..."},
  "structure_period_hardcoded": 10
}
```

- `actual_span` 記的是**實得**期間，不是請求期間（實測 `period="60d"` 得 59 交易日）。
- `structure_period_hardcoded` 顯式標示既有硬編碼值（FR-021）——本案不修它，
  但報告不得讓它看起來像個組態參數。

## results

```json
{
  "universe": {
    "included": ["2330.TW", "..."],
    "decisions": [
      {"ticker": "2454.TW", "included": false,
       "failed_criteria": ["min_avg_daily_volume"],
       "measured": {"avg_daily_volume": 812345.0, "gap_ratio": 0.004}}
    ]
  },
  "per_ticker": [
    {
      "ticker": "2330.TW",
      "data_health": {"bars": 3130, "trading_days": 59, "bars_per_day_median": 53.0,
                      "gap_bars": 12},
      "signal_density": {"bos_up": 210, "bos_down": 198, "mss_up": 41, "mss_down": 37,
                         "regime_ok": 1204, "warmup_bars": 200, "usable_bars": 2930},
      "attrition": {"bos_signals": 210,
                    "single_pass_rates": {"momentum": 0.52, "trend": 0.31,
                                          "volatility": 0.44, "global": 0.24},
                    "conjunction_passed": 11},
      "trades": 7,
      "zero_trade_cause": null,
      "performance": {
        "total_return":  {"value": 0.0312, "validity_label": "in_sample_descriptive"},
        "max_drawdown":  {"value": -0.0184, "validity_label": "in_sample_descriptive"},
        "profit_factor": {"value": 1.21,   "validity_label": "in_sample_descriptive"},
        "win_rate":      {"value": 0.4286, "validity_label": "in_sample_descriptive"}
      }
    }
  ],
  "pooled": [
    {"metric": "conjunction_pass_rate", "pooled_value": 0.061,
     "min": 0.021, "max": 0.158, "ratio": 7.52, "n_tickers": 8}
  ],
  "windows": {
    "splits": [],
    "sufficient": false,
    "shortfall_trading_days": 47
  },
  "scale_sweep": [
    {"factor": 0.25, "single_pass_rates": {"...": 0.0}, "conjunction_passed": 0, "trades": 0}
  ]
}
```

### 硬性結構約束

1. **`performance` 的每一項都是 `{value, validity_label}` 物件**，不得為裸數值
   （FR-005 / SC-002）。裸數值即為缺陷。
2. **`pooled` 的每一筆都同時帶 `min`/`max`/`ratio`**（FR-002 / SC-003）。
   `pooled_value` 不得單獨序列化。
3. `zero_trade_cause` 在 `trades == 0` 時**必須**為四個列舉值之一，
   不得為 `null`、不得為 `"unknown"`（FR-007 / SC-004）。
4. `windows.sufficient == false` 時 `splits` **必為空陣列**且
   `shortfall_trading_days` 必為正整數（FR-015 / SC-008）——
   不得回傳部分切分。
5. `signal_density` 的 BOS/MSS **分方向**四欄齊備（FR-008）。

### 措辭檢核（FR-006 / SC-012）

序列化後的全文不得出現有效性宣稱措辭。檢核清單（固定、可擴充）：

```text
策略有效、確實有效、可用於實盤、建議啟用、應該啟用、證明有效、
穩定獲利、勝率高、值得投入實盤
```

出現次數必須為 0。此檢核對三種 `validity_label` 一律適用——
`out_of_sample_validated` 只宣稱程序已執行，不宣稱策略有效。

## 確定性規則（research.md R8）

- 所有物件鍵序列化前排序；陣列依 `ticker` 字串排序。
- 浮點以固定小數位輸出（比率 4 位、金額 4 位、通過率 4 位）。
- `provenance` 以外不得出現任何時間戳以外的執行期資訊。
