# Interface Contracts: 台指期做空（003）

**Phase 1 產出** | 值語意見 [data-model.md](../data-model.md)。全部為既有介面之
**back-compat 擴充**（新參數皆有預設值 = 現行為）。

## `PositionManager.check_entry_signal`（ladder_system.py）

```python
def check_entry_signal(self, close, open_val, daily_open, vwap, atr,
                       candle_high, candle_low, structure_sig,
                       global_filter_ok, is_daily=False,
                       disabled_filters=frozenset(),
                       direction: int = 1) -> bool:   # 新參數，預設 1 = 現行為
```

- `direction=1`：四維度**逐字現行為**（結構==1、收陽、價>當日開盤&VWAP、振幅>1.2×ATR）。
- `direction=-1`：鏡像——結構==−1、收陰（close<open）、價<當日開盤（且<VWAP，
  非日線時）、振幅同式（無方向）；`disabled_filters` 語意不變。
- **契約測試**：direction=1 對任意輸入與現行輸出位元相同（parity）；
  direction=-1 與翻轉輸入下的 direction=1 等價（鏡像）。

## `PositionManager.manage_position`（ladder_system.py）

```python
def manage_position(self, current_close, current_atr, chandelier_long,
                    bar_count, time_limit=10,
                    chandelier_short: float = None) -> ExitEvent:  # 新可選參數
```

- `direction==1` 分支**逐字不動**（既有呼叫不傳 chandelier_short → 零影響）。
- `direction==-1` 分支（新）：`close >= stop_loss` → STOP_LOSS；階段 1 目標
  `entry − 1.5×ATR`，達標 → STAGE1_HALF + 止損移保本；階段 2 吊燈
  `chandelier_short`（新值 < stop_loss 時下移），`close > stop_loss` → CHANDELIER；
  時間止盈同多方。`direction==-1` 而 `chandelier_short is None` 且 stage==2 →
  ValueError（fail-fast，防呼叫端漏傳）。
- ExitEvent enum **復用**（STOP_LOSS/STAGE1_HALF/TIME_LIMIT/CHANDELIER 方向無關）。

## `calculate_regime_filter` / `build_indicator_frame`（ladder_system.py）

- 指標框架增產 `regime_ok_short` 欄位（include_regime 時）：ADX/ER 分量共用、
  MA 分量鏡像（價<長均線）。`regime_ok`（多方）**逐字不動**。
- 消融：`disabled_filters={'regime'}` 時兩欄皆以恆 True 語意替代（現行模式）。

## `BacktestEngine.run_backtest`（backtester.py）

```python
run_backtest(..., enable_short: bool = False)   # 新參數，預設 False = 現行為
```

- 進場裁決（flat 時）：`close > mid` → 多方分支（**逐字現行**：BOS=1 → MSS=1 反轉）；
  `close < mid` 且 `enable_short and is_futures` → 空方分支（BOS=−1 續勢
  【global = close<mid AND regime_ok_short】→ MSS=−1 反轉【`mss_reversal_entry` 且
  global = close<mid、放寬 trend、免 regime——多方反轉 profile 之鏡像】）。
- 空方成交：進場 `cost_model.slip(open, "sell")`（向下不利）、回補 `slip(open, "buy")`
  （向上不利）——008b 元件現成方向語意，**零改動**。
- sizing：`sizer.size(equity, sizing_price)` 同 008b（無方向）；部分回補
  `sizer.partial_units`（floor）。
- 會計：方向因子 d 統一式（data-model）；爆倉機制沿 008b（空方由上漲觸發）。
- 動作：SELL_SHORT / COVER_HALF / COVER_ALL；`_calculate_metrics` 增空方配對
  （SELL_SHORT→COVER_ALL，profit 含方向因子），多方配對段**逐字不動**。
- **硬邊界**：空方分支條件含 `is_futures`——equity 在任何 enable_short 值下
  不可達空方路徑。

## `SingleStrategyParams` / `SystemConfig`（config/config.py）

- `enable_short: bool = False`（Field，description 明示期貨限定）。
- SystemConfig validator：`ticker_overrides` 中現貨 ticker 明設 enable_short=true
  → ValueError（SC-004）；期貨 instrument id 的 override 合法。

## `monitor_signals.py`

- 標的迭代：`cfg.data.tickers`（現行）→ `InstrumentRegistry.from_config(...)` 全
  instrument（equity 行為不變；futures 走 adapter 取數 + `fut_*` 語意）。
- 期貨訊息：既有多空 MSS/BOS 文案復用；instrument.source == "mock" 時訊息前綴
  `【MOCK 資料—dry-run】`（FR-010）。
- 去重表鍵（ticker, bar_time, alert_type）語意不變。

## SC ↔ 契約對映

| SC | 契約段落 | 測試 |
|----|----------|------|
| SC-001 | 引擎空方分支 + 008b 元件復用 | `test_short_futures_e2e.py` |
| SC-002 | 鏡像變換定義 + check_entry/manage 對稱 | `test_short_side.py`（變換 + 情境對） |
| SC-003 | 全部新參數預設 = 現行為 | 既有全套 pytest + 基準數字對照 |
| SC-004 | config validator + 引擎 is_futures 閘門 | `test_short_side.py` 硬邊界段 |
| SC-005 | 空方 N+1 成交/sizing 訊號根權益 | `test_lookahead_bias.py` 擴充 |
| SC-006 | 爆倉方向鏡像 | `test_short_futures_e2e.py` |
| SC-007 | MSS=−1 反轉分支 + 007 BLOCKED 移除 | e2e + 007 spec 文件更新 |
| SC-008 | monitor 迭代 + mock 標示 | `test_monitor_short.py` |
