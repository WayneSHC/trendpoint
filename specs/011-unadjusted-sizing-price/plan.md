# Implementation Plan: 期貨連續序列未調整參考價（sizing 與期交稅價格基準修正）

**Branch**: `011-unadjusted-sizing-price` | **Date**: 2026-07-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/011-unadjusted-sizing-price/spec.md`

## Summary

連續月引擎在回溯平移**之前**多輸出四欄未調整 OHLC（`unadj_open/high/low/close`
＝當日近月契約原始價），隨連續表一併入庫；回測引擎在期貨分支把**價格基準**
一分為二——口數/保證金取 `unadj_close`（訊號根）、期交稅取 `unadj_open`（成交根，
套同一滑價點數），訊號與每點損益仍走調整後欄位。成本/sizing 元件的介面簽章
**不動**（008b 現貨位元不變保證因而免受衝擊），改由引擎在呼叫邊界選價。
另補三道守門：未調整欄位嚴格正值檢查（不受連續層負價豁免影響）、期貨路徑缺欄
硬失敗、非 rollover 期貨來源（MTX/mock）於通用 ingestion 路徑補齊同名欄位。

## Technical Context

**Language/Version**: Python 3.10+（`.venv` = 3.13）

**Primary Dependencies**: pandas（既有）。**不引入新依賴**。

**Storage**: SQLite `trendpoint.db` —— `fut_TXF_daily` 連續表增四欄。寫入為
`to_sql(if_exists="replace")` 整表覆寫、schema 由 DataFrame 自動推導
（`db_security.py:93-99`），故「回填」＝重跑連續層重建，無需 migration DDL。
raw 層 `fut_TXF_raw_daily`（34,722 列）不動，是重建的輸入。

**Testing**: pytest。新增：rollover 未調整欄位透傳與**截斷不變性**、看前偏誤
防禦（`tests/test_lookahead_bias.py`）、稅基與 sizing 基準單元測試、缺欄硬失敗、
現貨/mock 等價回歸。既有 182 passed 須維持全綠。

**Target Platform**: 本機 CLI（`run_ingestion.py` 重建、`run_backtest.py` 驗收）；
監控路徑（`monitor_signals.py:127-129`）不做 sizing，僅被動多收四欄，行為不變。

**Project Type**: 單一 Python 專案

**Performance Goals**: 未調整欄位為建構時既有值的複製（平移前擷取），連續層
重建仍為單次向量化；引擎內每根取值 O(1) 欄位查找，無新增迴圈或 rolling 結構。
憲章 IV 無熱路徑疑慮。

**Constraints**: 現貨路徑位元不變（008b SC-001 承諾延續）；不新增可調參數
（現有 `trading_cost.futures` 五參數已足）；未調整價不得由位移量回推（FR-011）；
CI 不出網——全部驗收以既有 raw 表或離線 fixture 完成。

**Scale/Scope**: 觸碰 5 個檔案（`data_sources/rollover.py`、`run_ingestion.py`、
`data_ingestion.py`、`backtester.py`、測試）。`trading_costs.py` **簽章零改動**。
無新模組、無新抽象層。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 判定 | 依據 |
|------|------|------|
| I 看前偏誤（NON-NEGOTIABLE） | ✅ PASS | 未調整價直接取自原始契約、非由位移量回推（FR-011）——位移量是未來轉倉的函數且隨尾端截斷而變（`rollover.py:19-20` 自載此性質）。sizing 取訊號根、稅取成交根，皆為該時點已可知之真實市價。新增截斷不變性測試（SC-008）與 `test_lookahead_bias.py` 防禦測試（FR-007）。本案未引入任何 rolling 結構，故無 `.shift(1)` 適用面。 |
| II 摩擦成本（NON-NEGOTIABLE） | ✅ PASS | 本案正是修正稅基失真（早年負價位會算出負稅額）。費率仍唯一來自 `config.trading_cost.futures`（`config/config.py:194-220`），本案不觸碰費率值、不引入常數。無零成本績效展示。 |
| III 規格↔測試 | ✅ PASS | SC-001~008 逐條對應測試，見 [quickstart.md](quickstart.md) 對照表。無 `[MANUAL]` 項。 |
| IV 效能紀律 | ✅ PASS | 平移前欄位複製屬向量化；引擎內為 O(1) 欄位取值。無純 Python 迴圈新增、無 `apply()`。 |
| V 組態集中 | ✅ PASS | **不新增任何可調參數**（FR-010）。已核對 `config/config.yaml:95-100` 現有 5 參數（`broker_commission_per_lot`/`tax_rate`/`slippage_ticks`/`margin_rate`/`margin_utilization`）足以支撐本案；欄位名為資料 schema 而非策略參數，不入 config。 |
| VI 可重現/資料衛生 | ✅ PASS | 連續表為可再生成產物（已 gitignore），回填＝重跑重建。未調整欄位納入資料契約嚴格正值檢查（FR-003），且明確不受連續層負價豁免影響。 |

**Gate 結論**：無違反項，Complexity Tracking 留空（本案為缺陷修正，淨新增
抽象為零——反而移除了「單一價格基準」這個錯誤的隱含假設）。

**Post-Design 複查（Phase 1 後）**：設計未新增模組、未改動元件介面簽章、
未新增參數，上表判定不變。唯一需持續盯的是原則 I——實作時若有人為了省一欄
而改用「調整後價 − 位移量」回推，即構成違反；此風險已由 FR-011 明文與
SC-008 截斷不變性測試釘死。

## Project Structure

### Documentation (this feature)

```text
specs/011-unadjusted-sizing-price/
├── plan.md              # 本檔
├── research.md          # Phase 0：三個設計決策與被否決的替代方案
├── data-model.md        # Phase 1：連續表 schema 增補與價格基準對照
├── quickstart.md        # Phase 1：驗收腳本與 SC 對照表
├── contracts/
│   └── price-basis-contract.md   # 價格基準分離契約（引擎↔元件邊界）
├── checklists/
│   └── requirements.md  # 規格品質檢查（含兩輪 review 修正記錄）
├── spec.md
└── tasks.md             # Phase 2 輸出（/speckit-tasks，本命令不產生）
```

### Source Code (repository root)

```text
data_sources/
└── rollover.py              # build_continuous 平移前擷取 unadj_* 四欄（FR-001）

run_ingestion.py             # _ingest_taifex 連續層原樣入庫（欄位自動帶入）
                             # 通用期貨路徑補 unadj_* = 調整後值（FR-009，MTX/mock）

data_ingestion.py            # validate_data_contract 增未調整欄位嚴格正值檢查
                             # （FR-003，須置於 allow_nonpositive_prices 提早 return 之前）

backtester.py                # 期貨分支：sizing 取 unadj_close、稅基取 unadj_open
                             # （FR-004/FR-005）；引擎初始化時缺欄硬失敗（FR-008）

trading_costs.py             # 【零改動】簽章不變，由引擎在呼叫邊界選價

tests/
├── test_rollover.py             # 未調整欄位透傳、截斷不變性（SC-002/SC-008）
├── test_lookahead_bias.py       # 未調整價非未來資訊之防禦測試（FR-007）
├── test_trading_costs.py        # 稅基與 margin 基準（SC-006）
├── test_backtester.py           # 期貨 sizing 回歸、缺欄硬失敗（SC-001/SC-007）
└── test_real_data_integration.py # 全歷史抽驗（SC-002/SC-004）
```

**Structure Decision**: 沿用現行單一專案扁平結構，不新增目錄。修改集中於
資料產生端（`rollover.py`）、資料驗證端（`data_ingestion.py`）與消費端
（`backtester.py`）三處，中間的儲存層因 schema 自動推導 + `SELECT *` 讀取
而無需改動——這是本案觸碰面能壓到最小的結構性原因。

## 關鍵設計決策摘要

完整論證見 [research.md](research.md)，此處列出對實作最有約束力的三條：

1. **攜帶未調整 OHLC 四欄，而非單一收盤價 + 位移量回推**。位移量含未來資訊
   且非截斷不變，回推會把看前偏誤引進稅基。四欄的儲存成本（6,947 列）可忽略。

2. **元件簽章不動，改由引擎選價**。`CostModel`/`PositionSizer` 的 ABC 維持
   `(price, units)` / `(equity, price)`；引擎在既有的 `is_futures` 分支
   （`backtester.py:287`）多決定一個「成本基準價」。這避開了改 ABC 會波及
   `EquityCostModel` 進而威脅 008b 現貨位元不變承諾的風險。

3. **缺欄硬失敗掛在引擎初始化，不掛在資料契約**。資料契約層無法區分
   「現貨表本就無此欄」與「期貨舊表缺欄」；引擎在 `is_futures` 已知的位置
   檢查才無歧義。這也是 FR-008 必須限縮作用域的實作理由。
