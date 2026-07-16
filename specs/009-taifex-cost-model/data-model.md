# Data Model: 台指期成本/口數模型（008b）

**Phase 1 產出** | 實體、欄位、驗證規則。實作對映見 [contracts/cost-model-contracts.md](contracts/cost-model-contracts.md)。

## ContractSpec（新，`instruments.py`，Pydantic frozen）

契約內生屬性；期貨 instrument 必帶、現股為 None。

| 欄位 | 型別 | 驗證 | 說明 |
|------|------|------|------|
| `point_value` | float | > 0 | 每點價值 NT$（TX=200、MTX=50、TMF=10）；名目值 = 價格點數 × point_value |
| `tick_size` | float | > 0，預設 1.0 | 最小跳動點數（台指類 = 1 點）；tick_value = tick_size × point_value |
| `exchange_fee_per_lot` | float | ≥ 0 | 交易所每口**每邊**定額費（經手費+結算費）：TX=20、MTX=12.5、TMF=8.0 |

**關聯**: `Instrument.contract: ContractSpec | None`（預設 None = 現股）。
**驗證規則**: `asset_class == FUTURES` ⟹ `contract` 必須非 None（Pydantic model validator，fail-fast）；
equity 帶 contract 為組態錯誤（同樣 fail-fast）。

## FuturesCostConfig（新，`config/config.py`，`trading_cost.futures` 巢狀）

帳戶/政策層參數；全部 config SoT。

| 欄位 | 型別 | 驗證 | 預設 | 說明 |
|------|------|------|------|------|
| `broker_commission_per_lot` | float | ≥ 0 | 0.0 | 券商加收每口每邊定額（0 = 僅交易所權威費率） |
| `tax_rate` | float | ≥ 0 | 0.00002 | 期交稅率（契約金額 ×，**兩邊各收**） |
| `slippage_ticks` | float | ≥ 0 | 1.0 | 每邊滑價 tick 數 |
| `margin_rate` | float | > 0, ≤ 1 | 0.055 | 每口保證金 = 名目值 × 此值 |
| `margin_utilization` | float | > 0, ≤ 1 | 0.5 | 口數 = floor(權益 × 此值 ÷ 每口保證金) |

**關聯**: `TradingCostConfig.futures: FuturesCostConfig`（預設值全帶 → 既有 config.yaml 零改可載入，
向後相容比照 008a `DataConfig.instruments` 前例）。既有扁平欄位（commission_rate/tax_rate/slip_rate/
lot_size）**不動** = 現股語意不變。

## 成本計算（值語意，非儲存實體）

**期貨每口每邊成本**（FR-002）：

```
名目值      = 成交價(點) × point_value × 口數
定額費      = (exchange_fee_per_lot + broker_commission_per_lot) × 口數
期交稅      = 名目值 × futures.tax_rate
滑價成本    = slippage_ticks × tick_size × point_value × 口數
單邊成本    = 定額費 + 期交稅 + 滑價成本
```

開倉、平倉各收一邊（來回 = 2 邊）。滑價以「成交價不利方向偏移」實作
（買 +slippage_ticks×tick_size 點、賣 −），與現股 `execution_price × (1±slip_rate)` 同構。

**現股每邊成本**（FR-009，現況逐字保留）：買邊 = 手續費 0.1425%；
賣邊 = 手續費 0.1425% + 證交稅 0.3%；滑價 = 價格 × slip_rate 方向性偏移。

## 保證金與口數（值語意）

```
每口保證金 = 訊號根收盤價(點) × point_value × margin_rate        # FR-004
口數       = floor(權益 × margin_utilization ÷ 每口保證金)        # FR-005，非負整數
部分平倉   = floor(持倉口數 × 出場比例)；0 → 跳過平倉、風控照做    # FR-012
```

**狀態不變量**: 持倉口數任何時點為非負整數；口數 0 時不進場（無部分口）。
**看前約束**（FR-007）: 口數計算輸入 = 第 N 根（訊號根）收盤時之權益與收盤價；成交於 N+1 開盤。

## 帳戶權益（會計語意，FR-006）

```
權益(t) = 權益(t-1) + 已實現損益(t) − 摩擦成本(t)      # 平倉根
已實現損益 = 口數 × (平倉點 − 開倉點) × point_value
報酬率 = 期末權益 ÷ init_capital − 1
爆倉：權益 ≤ 0 當根 → 當根價強制結清、終止、summary 標記   # FR-011
```

持倉中權益曲線以收盤價 mark-to-market（口數 × (當根收盤 − 開倉點) × point_value 之未實現損益計入）。
與現股共用 `backtest.init_capital` 基底；期貨曲線與現股**不直接可比**（槓桿本質，spec Assumptions）。

## 期貨交易紀錄（交易日誌擴充）

現股交易日誌欄位之上，期貨紀錄含：口數（lots）、進出場價（點）、佔用保證金、
成本明細（定額費／稅／滑價分列）、已實現損益。方向恆為多（long-only，FR-008；做空 = spec 003）。

## config.yaml 形狀（示意）

```yaml
trading_cost:
  commission_rate: 0.001425   # 現股（不動）
  slip_rate: 0.0005
  tax_rate: 0.003
  futures:                    # 新增（全欄位有預設，可整段省略）
    broker_commission_per_lot: 0
    tax_rate: 0.00002
    slippage_ticks: 1
    margin_rate: 0.055
    margin_utilization: 0.5

data:
  instruments:
    - id: "TXF"
      asset_class: "futures"
      source: "mock"
      display_name: "臺股期貨(mock)"
      timeframes: ["daily"]
      contract: { point_value: 200, tick_size: 1, exchange_fee_per_lot: 20 }
    - id: "MTX"
      asset_class: "futures"
      source: "mock"
      display_name: "小型臺指(mock)"
      timeframes: ["daily"]
      contract: { point_value: 50, tick_size: 1, exchange_fee_per_lot: 12.5 }
```
