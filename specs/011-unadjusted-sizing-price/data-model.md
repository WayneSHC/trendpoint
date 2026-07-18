# Phase 1 Data Model: 連續表 schema 增補與價格基準對照

**Feature**: 011-unadjusted-sizing-price | **Date**: 2026-07-18

## 1. 期貨連續序列（`fut_{id}_daily`）

索引 `datetime`（DatetimeIndex，寫入時 `index_label="datetime"`）。

| 欄位 | 現況 | 本案 | 語意 | 值域 |
|------|------|------|------|------|
| `open`/`high`/`low`/`close` | 既有 | 不變 | back-adjust 後連續價；**僅供訊號與每點損益** | 可 ≤ 0（早年，spec 010 既有例外） |
| `volume` | 既有 | 不變 | 近月契約成交量，不平移 | ≥ 0 |
| `unadj_open` | — | **新增** | 當日近月契約原始開盤價 | **> 0 且有限** |
| `unadj_high` | — | **新增** | 當日近月契約原始最高價 | **> 0 且有限** |
| `unadj_low` | — | **新增** | 當日近月契約原始最低價 | **> 0 且有限** |
| `unadj_close` | — | **新增** | 當日近月契約原始收盤價 | **> 0 且有限** |

**不變式**：

- I1：`unadj_*` 為原始契約值的直接複製，不參與任何平移運算。
- I2：同一根內 `close − unadj_close == open − unadj_open == high − unadj_high
  == low − unadj_low`（該根的平移量對四個價格一致）。此式僅作為**測試斷言**
  使用，實作**禁止**反向利用它回推價格（FR-011）。
- I3：`unadj_*` 截斷不變——序列於任一點截斷重建後，該點之前各根的 `unadj_*`
  完全相同；`open`/`high`/`low`/`close` 則允許改變。
- I4：無轉倉調整的來源（MTX/mock、csv）：`unadj_* ==` 對應調整後欄位。

**Schema 演進**：無 DDL。寫入為 `to_sql(if_exists="replace")`、schema 由
DataFrame 欄位自動推導，故重跑連續層重建即完成回填。舊表（缺四欄）不會被
自動偵測，由消費端硬失敗把關（見 §3）。

## 2. 價格基準對照表（本案的核心）

這張表就是本案要建立的區分。左欄是計算，右欄是它該吃哪個價格。

| 計算 | 價格基準 | 取自哪一根 | 現行程式位置 | 理由 |
|------|----------|-----------|-------------|------|
| 訊號判定（階梯/ATR/結構/停損停利） | **調整後** | 各自 | `ladder_system.py` | 只依賴價差，跨轉倉需連續 |
| 每點損益增量 | **調整後** | 進出場根 | `backtester.py` PnL 段 | Δ 不受平移影響（平移為常數） |
| 成交價（含滑價） | **調整後** | 成交根 `open` | `backtester.py:282/344` | 與 PnL 同基準，維持自洽 |
| **口數 sizing** | **`unadj_close`** | **訊號根** | `backtester.py:287/345` | 名目值 = 價位 × 乘數，需真實價位 |
| **每口保證金** | **`unadj_close`** | **訊號根** | `backtester.py:336/383` | 同上；`margin_per_lot(price)` |
| **期交稅（進場）** | **`unadj_open` + 滑價** | **成交根** | `backtester.py:302/358` | 稅基 = 成交契約金額 |
| **期交稅（出場/部分/強制）** | **`unadj_open` + 滑價** | **成交根** | `backtester.py:419/455/502` | 同上 |
| 每口定額手續費 | 不吃價格 | — | `trading_costs.py:142` | `fee_per_lot × units` |
| 現貨全部計算 | 調整後（＝原值） | 現行不變 | — | 現貨無 back-adjust，位元不變 |

**滑價的處理**：期貨滑價是**點數加減**（`trading_costs.py:134-138`），非比例，
故對未調整價套用同一 `slip()` 得到的偏移量與調整後路徑一致——
`slip(unadj_open) = slip(open) − (open − unadj_open)`，自洽。

## 3. 消費端契約（FR-008 硬失敗）

期貨回測引擎初始化時：

```
若 is_futures 且 {unadj_open, unadj_close} ⊄ df.columns：
    raise ValueError（訊息須含：表名、缺哪些欄、重建指令 run_ingestion.py）
```

- 檢查**僅**在期貨路徑執行；現貨資料框不得被要求具備這些欄位。
- 檢查在**初始化**執行，不在逐根迴圈內（失敗要早，不留半份結果）。
- **禁止**任何 fallback 分支。因所有期貨來源皆有產出義務（I4），缺欄唯一
  代表舊資料。

## 4. 資料契約驗證增補（FR-003）

`validate_data_contract` 增一段：若 `unadj_*` 欄位存在，則檢查嚴格 > 0 且
有限，**不受 `allow_nonpositive_prices` 豁免**。

實作位置關鍵：現行 `allow_nonpositive_prices=True` 會在
`data_ingestion.py:163` 提早 `return True`，新檢查必須置於該 return
**之前**，否則對連續層（唯一帶此旗標的呼叫點，`run_ingestion.py:87`）永遠
不會執行——而連續層正是唯一需要這道檢查的地方。

## 5. 回測產物（trades CSV）

`sizing_price` 欄現存於進場記錄（`backtester.py:333/379`），本案後其值語意
變為未調整價。建議同時保留調整後訊號根收盤於另一欄，便於驗收比對兩基準——
屬可再生成產物，無相容性承諾（spec Assumptions）。
