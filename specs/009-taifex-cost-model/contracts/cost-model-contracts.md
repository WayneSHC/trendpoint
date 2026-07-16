# Interface Contracts: 成本/口數元件（008b）

**Phase 1 產出** | 元件介面契約；欄位語意見 [data-model.md](../data-model.md)。
新模組 `trading_costs.py`（MPL-2.0 標頭）。型別示意用 Python 簽名表達，實作須與此一致。

## CostModel（ABC）

每筆**成交**（一邊）的摩擦成本計算。純函式、無狀態、O(1)。

```python
class CostModel(ABC):
    @abstractmethod
    def entry_cost(self, price: float, units: float) -> float: ...
        # 買/開倉邊總摩擦成本（NT$）。price 為成交價（滑價後）。
    @abstractmethod
    def exit_cost(self, price: float, units: float) -> float: ...
        # 賣/平倉邊總摩擦成本（NT$）。
    @abstractmethod
    def slip(self, raw_price: float, side: str) -> float: ...
        # 回傳滑價後成交價；side ∈ {"buy","sell"}，方向恆不利。
```

**EquityCostModel（現況逐字重現，FR-009）**
- `slip`: buy → `raw × (1 + slip_rate)`；sell → `raw × (1 − slip_rate)`（同 backtester.py:232,294,323 現行為）
- `entry_cost`: `price × units × commission_rate`
- `exit_cost`: `price × units × (commission_rate + tax_rate)`（證交稅僅賣邊）
- **契約測試**: 對任意 (price, units)，與現行內聯公式輸出逐位元相同。

**FuturesCostModel（FR-002）**
- 建構參數: `contract: ContractSpec`、`fut_cfg: FuturesCostConfig`
- `slip`: buy → `raw + slippage_ticks × tick_size`；sell → `raw − ...`（點數偏移，非百分比）
- `entry_cost` = `exit_cost` = `(exchange_fee_per_lot + broker_commission_per_lot) × units
  + price × point_value × units × fut_cfg.tax_rate`（期交稅**兩邊**；定額費兩邊）
- 注意：滑價成本已內含於成交價偏移（與現股同構），**不得**在 cost 中重複計費。
- **契約測試**（SC-002）: TX 1 口 @20000 點單邊 → 定額 20 + 稅 20000×200×0.00002=80 → 100 NT$
  ＋滑價 1 tick = 200 NT$（進場價 +1 點）；MTX/TMF 依規格縮放。

## PositionSizer（ABC）

進場部位單位決定。輸入僅允許訊號根收盤時已知資訊（FR-007）。

```python
class PositionSizer(ABC):
    @abstractmethod
    def size(self, equity: float, signal_close: float) -> float: ...
        # 回傳部位單位（現股=股數、期貨=口數）。0 = 不進場。
    @abstractmethod
    def partial_units(self, held: float, fraction: float) -> float: ...
        # 部分出場單位；held 之 fraction 比例的可執行單位。
```

**EquitySizer（現況逐字重現）**
- `size`: `round_to_lot(equity / (execution_price × (1 + commission_rate)))`
  ——注意現行語意用**成交價**算最大可負擔股數（backtester.py:235-236），契約保留此語意，
  簽名允許實作取用成交價（見引擎注入契約）。
- `partial_units`: `round_to_lot(held × fraction)`（backtester.py:298 現行為）

**FuturesSizer（FR-004/005/012）**
- 建構參數: `contract: ContractSpec`、`fut_cfg: FuturesCostConfig`
- `size`: `floor(equity × margin_utilization / (signal_close × point_value × margin_rate))`，
  非負整數；`< 1 → 0`（不進場，不拋錯）
- `partial_units`: `floor(held × fraction)`；0 → 呼叫端跳過平倉但執行風控（移保本位）
- **契約測試**（SC-003）: equity 1,000,000、close 20,000、TX、rate .055、util .5 → 每口保證金
  220,000 → 口數 2；equity 200,000 同上 → 0 口；`partial_units(1, 0.5)` → 0、`(3, 0.5)` → 1。

## 工廠

```python
def for_asset_class(instrument, config) -> tuple[CostModel, PositionSizer]:
    # equity（或 instrument=None）→ (EquityCostModel, EquitySizer)（自 config.trading_cost 建構）
    # futures → (FuturesCostModel, FuturesSizer)（自 instrument.contract + trading_cost.futures 建構）
    # futures 而 instrument.contract 為 None → ValueError（fail-fast，理論上被 Pydantic 擋在前）
```

## 引擎注入契約（`backtester.py` / `portfolio_backtester.py`）

- `run_backtest(..., cost_model=None, sizer=None, point_value: float = 1.0)`（或等價 instrument 參數）：
  `None` → 內部預設現股元件 = **既有呼叫零改動、現股 code path 位元不變**（SC-001）。
- P&L 統一式: `units × (exit_point − entry_point) × point_value`（現股 point_value=1 退化為現行公式）。
- 權益 mark-to-market、爆倉檢查（FR-011）：每根收盤後檢查權益 ≤ 0 → 當根價強制結清、
  截止曲線、`summary["blown_up"] = True`（現股數學上不會觸發——無槓桿且 long-only，
  但檢查為資產類別無關之通用防護）。
- 008a 護欄退役: 引擎/入口不再對 futures 呼叫 `assert_backtestable` 拒絕；函式與
  `FuturesBacktestNotSupportedError` 定義保留（import 相容），語意改為 no-op 或移除呼叫點
  （`test_futures_backtest_guard.py` 隨之語意反轉改寫，SC-006）。
- 監控/推播、walk-forward、optimizer 對期貨**不在本 spec**——僅 `run_backtest.py`
  入口 + 引擎層接期貨（spec Assumptions 範圍邊界）。

## SC ↔ 契約對映

| SC | 契約段落 | 測試 |
|----|----------|------|
| SC-001 | EquityCostModel/EquitySizer 逐字重現 + 引擎 None 預設 | 既有全套 pytest + 4 檔基準數字 |
| SC-002 | FuturesCostModel 數值例 | `test_trading_costs.py` |
| SC-003 | FuturesSizer 數值例 | `test_trading_costs.py` |
| SC-004 | 引擎注入 + mock e2e | `test_futures_backtest_e2e.py` |
| SC-005 | Sizer 輸入限制 + N+1 成交 | `test_lookahead_bias.py` 擴充 |
| SC-006 | 護欄退役 | `test_futures_backtest_guard.py` 改寫 |
| SC-007 | 工廠自 config 建構、無硬編碼 | grep 稽核 + config 載入測試 |
