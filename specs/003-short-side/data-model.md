# Data Model: 台指期做空（Short Side, Futures-Only）

**Phase 1 產出** | 契約見 [contracts/short-side-contracts.md](contracts/short-side-contracts.md)。

## 方向因子（值語意）

`d ∈ {+1, −1}`（`PositionManager.direction` 既有欄位）：

```
已實現損益 = d × 口數 × (平倉點 − 開倉點) × point_value − 摩擦成本
未實現損益 = d × 口數 × (當根收盤 − 開倉點) × point_value
權益(MTM)  = 現金 + 未實現損益            # d=+1 退化為 008b 現式
爆倉檢查   = 權益 ≤ 0 當根強制結清（008b FR-011 機制不變；空方由上漲觸發）
```

## `enable_short` 組態（`config/config.py`）

| 欄位 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `SingleStrategyParams.enable_short` | bool | **False** | 期貨做空開關；經 `ticker_overrides` 得 per-instrument 粒度 |

**驗證規則**（SystemConfig model validator，SC-004）：`ticker_overrides` 中屬於
**現貨** ticker（`data.tickers` 成員或非期貨 instrument id）的項目明設
`enable_short: true` → 載入 fail-fast。`default.enable_short` 不受此限
（語意=期貨可做空，對現貨無效——引擎閘門保證）。

**聯合控制**：空方 BOS 續勢 ← `enable_short AND is_futures`；
空方 MSS 反轉 ← `enable_short AND is_futures AND mss_reversal_entry`。

## 空方部位狀態（PositionManager，既有欄位新語意）

| 欄位 | 多方（現行） | 空方（新） |
|------|------------|-----------|
| `direction` | 1 | **−1** |
| `stop_loss` | 進場價下方；上穿=安全 | **進場價上方**；`close ≥ stop_loss` 出場 |
| 階段 1 目標 | `entry + 1.5×ATR` | **`entry − 1.5×ATR`** |
| 階段 2 吊燈 | `chandelier_long`（Rolling Max − m×ATR，只升不降） | **`chandelier_short`**（Rolling Min + m×ATR，**只降不升**：新值 < stop_loss 時下移） |
| 時間止盈 | 階段 1 逾時全出 | 同（方向無關） |

## 交易紀錄（動作值域擴充）

| 動作 | 語意 | 配對 |
|------|------|------|
| `BUY` / `SELL_HALF` / `SELL_ALL` | 多方（現行，**不動**） | BUY → SELL_ALL |
| `SELL_SHORT` | 空方進場（賣出開倉；滑價向下不利偏移） | — |
| `COVER_HALF` | 空方部分回補（floor 整數口；滑價向上不利偏移） | — |
| `COVER_ALL` | 空方全回補（含爆倉強制回補） | SELL_SHORT → COVER_ALL |

期貨空方紀錄同 008b 帶 `point_value`/`sizing_price`/`margin_used`；成本欄位
（commission/tax）同 008b 元件輸出（兩邊各收，無借券費欄位）。

## 鏡像變換（SC-002a 測試定義）

對常數 `C`（取序列中點價），翻轉映射 `m(p) = 2C − p`：

| 原欄位 | 翻轉後 |
|--------|--------|
| `open` | `2C − open` |
| `high` | **`2C − low`**（高低對調） |
| `low` | **`2C − high`** |
| `close` | `2C − close` |
| `volume` | 不變 |

**對稱斷言**：原序列（`enable_short=false`）之多方訊號/進場/出場序列，與翻轉
序列（`enable_short=true`、多方閘門對稱存在）之空方序列一一對應：
進出場根相同、口數規則對應、事件類型鏡像（BUY↔SELL_SHORT、SELL_HALF↔COVER_HALF、
SELL_ALL↔COVER_ALL、止損/吊燈/時間事件同類）。衍生指標（ATR、振幅、ADX、ER、
量能）在翻轉下不變或對稱；`daily_open`/`vwap`/`mid_price`/`ladder` 隨價格翻轉。

## 空方市況濾網（regime）

`regime_ok_short` = ADX 分量（無方向，共用）AND MA 分量鏡像（**價 < 長均線**）
AND ER 分量（無方向，共用）。指標框架與 `regime_ok` 同批向量化產出；
消融 `disabled_filters={'regime'}` 語意同多方。

## 三關價互斥裁決（進場優先序）

```
flat 時逐根：
  close > mid_price → 僅評估多方（BOS=1 續勢 → MSS=1 反轉）
  close < mid_price → 僅評估空方（BOS=−1 續勢 → MSS=−1 反轉）
  close == mid_price → 不進場（邊界保守）
持倉遇反向訊號 → 既有止損/吊燈先出場，不反手
```
