# Phase 0 Research: 未調整參考價的取得方式與注入點

**Feature**: 011-unadjusted-sizing-price | **Date**: 2026-07-18

規格階段沒有留下 `[NEEDS CLARIFICATION]`，本階段的工作因此不是補問題，而是把
三個「有多種合理解法、選錯會違憲」的技術岔路走到定案。每條都附被否決的替代
方案與否決理由。

---

## D1：未調整價的取得方式——直接攜帶原始 OHLC，不由位移量回推

**Decision**：`build_continuous` 在回溯平移**之前**擷取當日近月契約的原始
OHLC，輸出為 `unadj_open/high/low/close` 四欄，與調整後 OHLC 並存。

**Rationale**：

規格初稿曾寫成「攜帶未調整收盤價，其餘價格由位移量（調整後收盤 − 未調整
收盤）回推」。看似省三欄，實則違反憲章原則 I：

- 位移量(N) ＝ N 之後所有轉倉調整之總和，本質上是**未來轉倉事件的函數**，
  在 N 時點不可知。
- 它也**不是截斷不變的**。`data_sources/rollover.py:19-20` 自己就記載：
  「back-adjust 平移基準隨尾端而異屬預期，斷言以近月序列為準」。把序列在任
  一點截斷重建，該點之前的位移量全部改變。
- 原始價格則相反：它只依賴近月選擇序列，而近月選擇在 spec 010 已確立為
  截斷不變（僅用前一交易日成交量判定）。

換言之，回推法會讓稅基變成一個「隨你餵多少未來資料而改變」的量——這正是
看前偏誤的定義。多存三欄的代價是 6,947 列 × 3 個浮點數，可忽略。

**Alternatives considered**：

| 方案 | 否決理由 |
|------|----------|
| 只存 `unadj_close`，其餘由位移量回推 | 違反憲章 I（上述），且稅基取成交根開盤價時必然需要 `unadj_open` |
| 額外存一欄 `adjustment_offset` | 資訊等價於回推法，同樣非截斷不變；且多一欄卻仍需 `unadj_open` 才能算稅，沒有省到 |
| 回測時即時重查 raw 表取原始價 | 每根一次查表，違反憲章 IV（熱路徑）；且引入回測對 raw 表的執行期依賴 |
| 不存欄位，改為回測前重跑 rollover 取兩份序列 | 回測啟動成本大增，且兩份序列的對齊本身就是本案要避免的錯誤來源 |

---

## D2：注入點——引擎在呼叫邊界選價，元件簽章不動

**Decision**：`CostModel` / `PositionSizer` 的 ABC 簽章維持
`(price, units)` / `(equity, price)` 不變。改由 `backtester.py` 在既有的
`is_futures` 分支決定傳入哪一個價格基準。

**Rationale**：

程式碼現況已經為此鋪好路。`backtester.py:287` 本來就有

```
sizing_price = float(sig_row['close']) if is_futures else execution_price
```

這條分支——按資產類別選價**已是既定模式**，`trading_costs.py:56-62` 的
`PositionSizer` docstring 也明文記載「`size` 的 price 輸入語意依實作而異」。
本案只是把同一個模式再用一次：期貨分支改取 `unadj_close`，並為成本呼叫新增
一個對應的成本基準價。

反過來，若改 ABC 讓元件自己去查未調整價，就得同時改 `EquityCostModel` 與
`EquitySizer` 的簽章。008b 對現貨路徑有「逐字重現、位元不變」的承諾
（`trading_costs.py:9`、`:74`），動它們的簽章是在拿一個已驗收的保證去冒險，
換不到任何東西。

另有一個實質好處：期貨手續費是每口定額（`fee_per_lot * units`），只有稅用到
price。把價格基準的選擇留在引擎，元件維持純函式無狀態（憲章 IV 的
「O(1) 純函式」設計得以保留）。

**Alternatives considered**：

| 方案 | 否決理由 |
|------|----------|
| 改 ABC 簽章為 `(price, unadj_price, units)` | 波及現貨元件，威脅 008b 位元不變承諾；且現貨永遠傳同一個值，介面說謊 |
| 元件建構時注入整個 DataFrame | 元件從無狀態變有狀態，且要處理日期對齊，複雜度暴增 |
| 在讀取層就把調整後價換成未調整價 | 會同時破壞訊號與 PnL（FR-006 明文禁止） |

---

## D3：缺欄硬失敗掛在引擎初始化，不掛在資料契約層

**Decision**：期貨回測引擎初始化時檢查未調整欄位是否齊備，缺則
`raise ValueError` 並提示重建連續層。`validate_data_contract` 只負責
「欄位若存在則必須嚴格為正」，不負責「欄位必須存在」。

**Rationale**：

資料契約層看不到資產類別的完整脈絡——`validate_data_contract` 雖有
`asset_class` 參數，但在 `allow_nonpositive_prices=True` 分支會在
`data_ingestion.py:163` 提早 return，該參數實際上是死參數。更根本的是，
現貨表本來就不該有這四欄，把「欄位必須存在」寫進通用契約會誤擋現貨
（FR-008 限縮作用域的實作理由即此）。

引擎初始化時 `is_futures` 已知、資料已載入，是唯一能無歧義判定的位置。
且**失敗要早**——掛在初始化而非迴圈內，避免跑到一半才炸、留下半份結果。

一個必要的配套：因為 `mock_source` 產生的 MTX 序列**不經過 rollover**
（`data_sources/mock_source.py:22-40` 只產生單一序列，走
`run_ingestion.py:123-137` 通用路徑），若不在通用路徑補齊同名欄位，硬失敗
會誤傷 MTX。這正是 FR-009 存在的理由，也是規格階段 FR-008/FR-009 矛盾
被抓出來的具體案例。

**Alternatives considered**：

| 方案 | 否決理由 |
|------|----------|
| 在 `validate_data_contract` 強制欄位存在 | 誤擋現貨表；且該函式在負價豁免模式下提早 return，檢查位置不可靠 |
| 缺欄時 fallback 用調整後價並印警告 | 本 bug 的症狀（爆倉）極易被誤判為策略問題，警告會被淹沒；FR-008 明文禁止 |
| 缺欄時自動觸發重建 | 回測指令隱式改寫資料庫，違反最小驚訝；重建需 raw 表存在，失敗模式更難懂 |
| 在 `for_asset_class` 工廠檢查 | 工廠只拿到 instrument 與 config，看不到資料框 |

---

## 附帶確認（無需決策，但影響任務拆解）

- **儲存層零改動**：寫入為 `to_sql(if_exists="replace")`、schema 由 DataFrame
  自動推導（`db_security.py:93-99`），讀取為 `SELECT *`（`db_security.py:70`）。
  新欄位會自動流通全鏈，不需 migration、不需改讀取端。
- **既有測試不鎖欄位集合**：`tests/test_taifex_source.py:136` 等處皆為
  `>=` superset 比較，增欄不會使既有測試轉紅。
- **監控路徑不做 sizing**：`monitor_signals.py` 無 sizer 呼叫，僅被動多收
  四欄，指標組裝只用 OHLCV，行為不變。
- **不需新參數**：`config/config.yaml:95-100` 現有五個 futures 參數足夠，
  憲章 V 在本案無新增面。
