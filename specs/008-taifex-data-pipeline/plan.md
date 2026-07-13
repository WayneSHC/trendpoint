# Implementation Plan: 台指期資料管線 + Instrument 抽象（純資料層）

**Branch**: `008-taifex-data-pipeline` | **Date**: 2026-07-13 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-taifex-data-pipeline/spec.md`

## Summary

引入 `Instrument` 資產類別抽象與可插拔資料來源 adapter，讓期貨（及未來資產）以加法接入、現貨路徑位元不變。**範圍限資料層**：ingest→驗證→存→載走 adapter 分派 + 集中表命名；回測引擎除**一道最小 `asset_class` fail-fast 護欄**外不碰（`Instrument` 穿線與成本/sizing dispatch 順延 008b）。實際來源後補（現用 Csv/Mock adapter）。台指期三段拆解第一段（008a 資料 → 008b 成本/口數 → spec 003 做空）。

## Technical Context

**Language/Version**: Python 3.10+

**Primary Dependencies**: pandas / numpy（正規化/驗證向量化）、pydantic（registry/Instrument 驗證）、pyyaml（config）、yfinance（現行 equity adapter）、sqlite3（stdlib）

**Storage**: SQLite `trendpoint.db`（整表覆蓋；對 back-adjust 連續序列正確）

**Testing**: pytest（新增 registry/adapter/table-naming/e2e/guard 測試）

**Target Platform**: 本機研究工具（批次 `run_*.py` + Streamlit `app.py`）

**Project Type**: 單一專案（資料層 + 演算法 + 回測 + UI）

**Performance Goals**: 資料匯入非熱路徑；正規化/驗證維持向量化（無逐列 Python 迴圈）。

**Constraints**: 現貨路徑位元不變；引擎除護欄外不碰；`clean_kline_dataframe` ffill-only 因果契約不破；新參數集中 config + Pydantic。

**Scale/Scope**: 少數 instruments、日線/5 分線；registry 規模個位數~數十。

## Constitution Check

*GATE: Phase 0 前必過；Phase 1 後複檢。*

| 原則 | 如何滿足 | Gate |
|---|---|---|
| **I 看前偏誤** | adapter 交付的連續序列須因果；沿用 `clean_kline_dataframe` 的 ffill-only（無 bfill）；e2e 測試驗證。 | ✅ |
| **II 摩擦成本** | 008a 不改成本；**護欄**確保不對期貨套用股票型成本（正是保護此原則）。 | ✅ |
| **III 規格↔測試** | 5 條 SC 各對應 pytest（見 quickstart 對照）。 | ✅ |
| **IV 效能紀律** | 資料層非熱路徑；正規化/驗證向量化，無逐列迴圈。 | ✅ |
| **V 組態集中** | Instrument registry + 新參數進 config.yaml + Pydantic，禁硬編碼。 | ✅ |
| **VI 可重現/衛生** | SQLite 整表覆蓋（對 back-adjust 正確）；資料契約驗證擴為 per-asset-class；db 續 gitignore。 | ✅ |

**違反項**：無。結構性註記：008a 對回測引擎有**最小觸碰**（一道 asset_class fail-fast 護欄，不做成本邏輯）——為使用者選定的雙保險，非憲章違反。

## Project Structure

### Documentation (this feature)

```text
specs/008-taifex-data-pipeline/
├── plan.md              # 本檔
├── research.md          # Phase 0：設計決策 D1–D8
├── data-model.md        # Phase 1：實體與欄位契約
├── quickstart.md        # Phase 1：驗證情境 + SC↔測試對照
├── contracts/
│   └── data-layer-contracts.md   # adapter 介面 / 表命名 / config schema / 護欄
├── checklists/requirements.md    # 由 /speckit-specify 產生
└── tasks.md             # 由 /speckit-tasks 產生（本命令不建立）
```

### Source Code (repository root)

```text
instruments.py                 # 新：Instrument 值物件 + registry 解析（含純字串向後相容）
data_sources/                  # 新套件：adapter 介面 + 實作
├── __init__.py                #   adapter 註冊/分派（by source 鍵）
├── base.py                    #   DataSourceAdapter 抽象介面 fetch(instrument, tf)->OHLCV
├── yfinance_source.py         #   包裝現行 fetch_stock_data + clean_kline_dataframe
├── csv_source.py              #   讀 CSV → 正規化連續序列
└── mock_source.py             #   確定性連續序列（含一段 rollover 跳空）
config/config.py               # Instrument/registry schema；data.instruments；per-asset 驗證門檻
config/config.yaml             # 新增 data.instruments（範例期貨）；per-asset 驗證設定
db_security.py                 # table_name_for(instrument, tf) helper + regex 放寬（stock|fut）
data_ingestion.py              # fetch 改走 adapter 分派；validate_data_contract 吃 asset_class
run_ingestion.py               # 迭代 registry、依 source 分派、以 helper 命名存表
run_backtest.py / run_portfolio_backtest.py  # 表名讀取改 helper + 入口層 futures 護欄
optimizer.py / run_ablation.py / run_walk_forward.py / app.py  # 表名讀取改 helper
backtester.py / portfolio_backtester.py       # 僅加一道最小 asset_class fail-fast 護欄
tests/
├── test_instrument_registry.py     # 解析、純字串向後相容
├── test_data_sources.py            # adapter 介面契約（yfinance/csv/mock 皆符合）
├── test_table_naming.py            # helper 往返 + regex 接受 stock_*/fut_*
├── test_futures_pipeline_e2e.py    # mock futures（含跳空）ingest→驗證→存→載
└── test_futures_backtest_guard.py  # 入口 + 引擎雙層 fail-fast
```

**Structure Decision**: 單一專案；新增 `instruments.py` 與 `data_sources/` 套件承載抽象與 adapter，其餘為既有檔的接縫改動。演算法/UI 不內嵌資料來源邏輯。

## Complexity Tracking

> 無憲章違反需證成。以下為結構性註記。

| 事項 | 為何需要 | 管理方式 |
|---|---|---|
| 引擎最小護欄觸碰 | 使用者選「入口+引擎」雙保險 fail-fast | 引擎僅加 `asset_class` 拒絕護欄（預設 equity），不引入成本/Instrument 依賴；成本 dispatch 仍 008b |
| 表名 ~7 處散落改 helper | 集中命名以支援 futures 命名空間 | 一次性機械改動，`table_name_for` 對 equity 回傳原 `stock_*` → parity 保護 |
