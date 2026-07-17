# Data Model: 真實台指期資料源（010）

**Phase 1 產出** | 契約見 [contracts/data-source-contracts.md](contracts/data-source-contracts.md)。

## 原始契約列（`fut_TXF_raw_daily`，新表）

| 欄位 | 型別 | 說明 |
|------|------|------|
| `date` | datetime（索引） | 交易日 |
| `contract` | str | 契約月份（如 `202307`；TAIFEX「到期月份(週別)」正規化） |
| `open/high/low/close` | float | 一般時段 OHLC（>0） |
| `volume` | float | 成交量（≥0） |
| `settlement` | float | 結算價 |
| `open_interest` | float | 未沖銷契約數 |

**唯一鍵**: （date × contract）——重跑冪等覆蓋（upsert 或整段重寫）。
**過濾**: 僅「一般」交易時段列（2017-05 起有盤後列）；僅月契約（排除週契約列）。

## 連續序列（`fut_TXF_daily`，既有表、整表覆蓋）

標準 OHLCV（datetime 索引）——由 raw 經近月選擇 + back-adjust 產生；
**可能含 ≤0 價格**（平移所致，品質檢查放寬為有限數值）；時間嚴格遞增、無缺值。
消費端（回測/監控/UI）格式與 008a 起完全相同——零改動。

## 轉倉事件（拼接引擎內部產物，可選輸出供稽核）

| 欄位 | 說明 |
|------|------|
| `roll_date` | 轉倉生效日（k：以 k−1 日量能判定） |
| `from_contract` / `to_contract` | 舊/新契約月份 |
| `adjustment` | 平移差額 = 新契約(k−1 收盤) − 舊契約(k−1 收盤) |

**規則不變量**: 近月選擇單調（contract 序列非遞減）；判定僅用 ≤k−1 資訊
（截斷不變性：任意截斷點前的近月選擇序列不變）；back-adjust 累積平移後
相鄰兩日 Δ點 = 同契約真實變動。

## FuturesDataSourceConfig（`config/config.py`，掛 `data` 下）

| 欄位 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `backfill_start` | str(date) | `"1998-07-21"` | 回填起始（clarify 定案全歷史） |
| `throttle_seconds` | float ≥ 0 | 2.0 | 回填每請求間隔（TAIFEX 未公告限流，保守） |
| `max_retries` | int ≥ 0 | 3 | 單請求失敗重試次數（之後 fail-fast） |
| `verify_tolerance` | float ≥ 0 | 0.0 | 交叉驗證容差（同源鏡像預設全等） |

FinMind token：環境變數 `FINMIND_TOKEN`（不入 config，安全鐵律）。

## config.yaml 變更

```yaml
data:
  instruments:
  - id: TXF
    asset_class: futures
    source: taifex          # ← mock 改 taifex（真源生效；MTX 暫留 mock）
    contract: {point_value: 200, tick_size: 1, exchange_fee_per_lot: 20}
  futures_source:           # ← 新增（全欄位有預設，可省略）
    backfill_start: "1998-07-21"
    throttle_seconds: 2.0
    max_retries: 3
    verify_tolerance: 0.0
```

## 交叉驗證報告

逐（date × contract）比對列：`field, taifex_value, finmind_value, diff, pass`；
超差列彙總印出 + 存 `data/verify_futures_report.csv`。驗證未執行時明確記錄原因
（無 token / HTTP 失敗 / 無重疊區間）。
