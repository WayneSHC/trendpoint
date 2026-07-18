# Implementation Plan: 真實台指期資料源（TAIFEX + FinMind）

**Branch**: `010-taifex-real-data` | **Date**: 2026-07-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/010-taifex-real-data/spec.md`

## Summary

兩個新 adapter（`taifex` 主源 / `finmind` 驗證源）+ 共用連續月引擎（量最大月轉倉 +
back-adjust）+ 獨立交叉驗證器。雙層儲存：原始 per-contract 列入 `fut_TXF_raw_daily`
（**現行 regex 已容納、零 regex 改動**）、連續序列入既有 `fut_TXF_daily`（消費端零改動）。
歷史回填全歷史（1998 起，clarify 定案）、每日增量走 OpenAPI。網路測試以
`@pytest.mark.network` 隔離（CI 不出網）；解析/拼接/驗證全部離線單元化。

## Technical Context

**Language/Version**: Python 3.10+（`.venv` = 3.13）

**Primary Dependencies**: pandas、requests（皆既有）；**不引入 FinMind SDK**——驗證源
直接打 FinMind REST API（requests 即可，少一個依賴；token 走環境變數 `FINMIND_TOKEN`）

**Storage**: SQLite `trendpoint.db`——`fut_TXF_raw_daily`（新，原始 per-contract）+
`fut_TXF_daily`（既有，連續序列，整表覆蓋）

**Testing**: pytest；新增 `network` marker（pytest.ini 註冊、預設 deselect）；
解析 fixture = 真實 TAIFEX Big5 CSV／FinMind JSON 樣本截取入 `tests/fixtures/`

**Target Platform**: 本機 CLI（`run_ingestion.py` 擴充）；監控/回測消費端零改動

**Project Type**: 單一 Python 專案

**Performance Goals**: 回填 ~340 請求 × 2s 節流 ≈ 12 分鐘（一次性）；拼接為單次
向量化計算（28 年日線 ~7000 列，毫秒級）；憲章 IV 無熱路徑疑慮

**Constraints**: 零回歸（mock 路徑測試不變）；CI 不出網；憑證僅環境變數；
back-adjust 負價 → 連續表品質檢查放寬為有限數值（僅期貨連續序列）

**Scale/Scope**: 新模組 3（taifex_source/finmind_source/rollover）+ 驗證器 1；
觸碰面 `run_ingestion.py`（回填/增量/verify 分流）、`config`（FuturesDataSourceConfig +
TXF source 切換）、`db_security`（raw 表名 helper）、`data_ingestion`
（validate_data_contract 對 back-adjusted 連續序列的正價放寬）；測試新增 4-5 檔

## 已驗證之事實（實測 + 程式碼查證）

| 事實 | 來源/位置 | 意義 |
|------|-----------|------|
| TAIFEX `POST /cht/3/futDataDown`：單次一月、Big5、無驗證碼、含結算/OI | 端點實測（2026-07-17） | 回填可程式化；欄位含「交易時段」需過濾 |
| OpenAPI `/v1/DailyMarketReportFut` 僅最近一交易日 | 端點實測 | 增量用、不能回填 |
| yfinance 無台指期（`TXF=F` 不存在） | 實測 | 排除 |
| FinMind `TaiwanFuturesDaily` 1998 起、600 req/hr、REST 可直打 | 文件+實測 | 驗證源；免 SDK |
| `fut_TXF_raw_daily` 匹配現行 `TABLE_NAME_PATTERN`（中段容納底線） | db_security.py regex | **零 regex 改動**，僅加命名 helper |
| `save_to_sqlite(df, table, db_path)` 為整表覆蓋語意 | data_ingestion.py（008a 用法） | back-adjust 整表重寫天然契合 |
| `validate_data_contract(df, quality, asset_class)` 現含正價檢查 | data_ingestion.py | 需對期貨連續序列放寬為有限數值（負價無害） |
| mock/csv adapter 與其測試自足（不依賴 config TXF source） | tests/test_data_sources.py 等 | TXF source 切 taifex 不破壞既有測試 |

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 關卡 | 評估 |
|------|------|------|
| I 看前偏誤（NON-NEG） | 近月選擇：第 k−1 日量能判定 → 第 k 日生效；截斷不變性測試（近月選擇序列） | ✅ FR-004/SC-002；拼接引擎 shift 語意單元釘住 |
| II 摩擦成本（NON-NEG） | 本 spec 不動成本層 | ✅ 無觸碰 |
| III 規格可測試 | SC-001~008 全對映；network 標記者 CI 外驗收 | ✅ |
| IV 效能紀律 | 拼接向量化、一次性回填 | ✅ |
| V 組態集中 | FuturesDataSourceConfig + Pydantic；token 環境變數 | ✅ |
| VI 可重現/衛生 | 回填後離線；fixture 離線化；CI 零出網；冪等入庫 | ✅ 核心設計目標 |

**初評：無違反。**

**Post-Phase-1 再評**：資料模型（roll 事件/雙層表）與契約未引入違反；正價放寬僅限
期貨連續序列（現貨檢查不動）。**通過。**

## Project Structure

### Documentation (this feature)

```text
specs/010-taifex-real-data/
├── spec.md / plan.md / research.md / data-model.md / quickstart.md
├── contracts/data-source-contracts.md
└── checklists/requirements.md（16/16）
```

### Source Code (repository root)

```text
data_sources/
├── taifex_source.py      # TaifexAdapter(source_key="taifex")：backfill(逐月 POST+Big5+節流+重試)
│                         #   + fetch_latest(OpenAPI JSON)；fetch() 組合 raw→rollover→連續
├── finmind_source.py     # FinMindAdapter(source_key="finmind")：REST 直打、token 環境變數
└── rollover.py           # 共用連續月引擎：select_front_month(量最大、單向、k−1 判定 k 生效)
                          #   + back_adjust(轉倉日收盤差回溯平移) + build_continuous()
verify_futures_data.py    # 交叉驗證器 CLI（獨立腳本；亦供 run_ingestion --verify 呼叫）
run_ingestion.py          # futures 真源分流：表空→backfill、有資料→增量；--verify 選項
config/config.py|yaml     # FuturesDataSourceConfig(backfill_start=1998-07-21, throttle_seconds=2,
                          #   max_retries=3, verify_tolerance=0.0) 掛 data 下；TXF source→taifex
db_security.py            # raw_table_name_for(instrument, tf) helper（regex 不動）
data_ingestion.py         # validate_data_contract：futures 連續序列正價檢查放寬為有限數值
tests/
├── fixtures/taifex_sample_big5.csv / finmind_sample.json   # 真實樣本截取（離線）
├── test_rollover.py            # 拼接引擎錨定例（SC-002）
├── test_taifex_source.py       # Big5 解析/時段過濾/格式 fail-fast（SC-003）+ network e2e（標記）
├── test_finmind_source.py      # 解析 + token 缺失行為（SC-003/004 支柱）
├── test_verify_futures.py      # 交叉驗證器（SC-004）
└── test_real_data_integration.py  # 消費端零改動（離線注入：無 MOCK 前綴、回測跑通）（SC-006）
```

**Structure Decision**: 沿 008a adapter 模式；rollover 獨立模組（兩 adapter 共用、
單元可測）；驗證器獨立腳本（不塞進 ingestion 主流程，US3 獨立性）。TaifexAdapter.fetch()
回傳連續序列（008a 契約：adapter 交付已拼接序列），raw 層由 ingestion 顯式存取
（adapter 另提供 fetch_raw()）。
