# Contract: 價格基準分離（引擎 ↔ 成本/Sizing 元件邊界）

**Feature**: 011-unadjusted-sizing-price | **Date**: 2026-07-18

本案不新增對外介面。此契約規範的是既有內部邊界上「傳哪個價格」的約定——
它是本案最容易被後續改動悄悄破壞的地方，故獨立成契約。

## C1：元件介面簽章凍結

```python
class PositionSizer(ABC):
    def size(self, equity: float, price: float) -> float: ...
    def partial_units(self, held: float, fraction: float) -> float: ...

class CostModel(ABC):
    def slip(self, raw_price: float, side: str) -> float: ...
    def entry_costs(self, price: float, units: float) -> TradeCosts: ...
    def exit_costs(self, price: float, units: float) -> TradeCosts: ...
```

**本案不得修改上列任一簽章。** 元件維持無狀態純函式；「傳哪個價格」是
**呼叫端的責任**，此語意已由 `trading_costs.py:56-62` 的 docstring 確立
（「price 輸入語意依實作而異」），本案沿用而非推翻。

## C2：呼叫端義務（`backtester.py`，期貨分支）

| 呼叫 | 現行傳入 | 本案應傳入 |
|------|----------|-----------|
| `sizer.size(capital, price)` | `sig_row['close']` | `sig_row['unadj_close']` |
| `sizer.margin_per_lot(price)` | `sizing_price` | 同上（沿用同一變數） |
| `cost_model.slip(raw, side)` | `row['open']` | **兩次**：調整後供成交價、未調整供稅基 |
| `cost_model.entry_costs(price, units)` | 調整後成交價 | **未調整成交價** |
| `cost_model.exit_costs(price, units)` | 調整後成交價 | **未調整成交價** |

現貨分支**一律不變**（008b 位元不變承諾）。

## C3：不變式（實作後須以測試釘住）

- **V1 元件純度**：對同一組輸入，元件輸出恆定；元件內不得讀取資料框、
  不得持有序列狀態。
- **V2 現貨位元不變**：現貨路徑的成本與 sizing 結果與本案前逐筆相同。
- **V3 PnL 基準自洽**：損益計算使用的成交價與權益結算基準維持調整後序列，
  不得混用未調整價（否則跨轉倉損益斷裂）。
- **V4 稅基正性**：期貨稅額恆 ≥ 0（早年負價位曾使其為負，本案修正之）。
- **V5 無 fallback**：期貨路徑不存在「缺未調整欄位時改用調整後價」的分支；
  以缺欄資料執行期貨回測必須拋錯。

## C4：連續層產出契約（`build_continuous`）

**輸入不變**：`(raw, front, events)`。

**輸出增補**：回傳的 DataFrame 除既有 `open/high/low/close/volume` 外，
必含 `unadj_open/unadj_high/unadj_low/unadj_close`，其值為該日近月契約
原始價格的直接複製（平移前擷取）。

**禁止**：以任何形式由調整後價格與位移量回推未調整價（FR-011）。

**截斷不變性**：對任意 `k`，`build_continuous(raw[:k], ...)` 產出的
`unadj_*` 與全量版本前 `k` 根完全相同。此為 SC-008 的可執行形式。
