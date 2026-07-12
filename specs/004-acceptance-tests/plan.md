# Implementation Plan: 驗收標準自動化測試套件（Acceptance Criteria as Tests）

**Branch**: `004-acceptance-tests` | **Date**: 2026-07-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/004-acceptance-tests/spec.md`

## Summary

把 OpenSpec §6 的四項可自動化驗收標準（回測↔即時零誤差、100ms 延遲預算、
缺漏插補容錯、離群值過濾）落為三個 pytest 測試檔。前置調查
（見 [research.md](research.md)）確認了三件事，決定本計畫的形狀：

1. **系統沒有逐根增量計算路徑**——監控端的「即時」就是對整段歷史全量重算、
   再取最後一根已收盤 bar。因此 Parity 的可測定義是**前綴一致性**：
   對任一截斷點 i，`compute(df[:i])` 的最後一列必須等於 `compute(df)` 的第 i 列。
2. **指標組裝邏輯重複內聯**於 `backtester.py:118-163` 與
   `monitor_signals.py:117-141`，無共用入口。Parity 測試需要一個正典計算路徑，
   故本計畫含一個**前置重構**：抽出共用的 `build_indicator_frame()`，
   兩個呼叫端改用之，並以前後回測零差異證明重構無害（憲法工作流程第 3 條）。
3. **現行資料契約不擋價格為 0 與極端跳動**（只擋負值）。US3 的離群值場景
   需要**先補實作**：`validate_data_contract` 增加 `price > 0` 與
   相鄰收盤跳動比率上限，閾值進 `config/config.yaml` + Pydantic（憲法 V）。

## Technical Context

**Language/Version**: Python 3.10 / 3.12（CI 兩版矩陣，與現行 tests.yml 一致）

**Primary Dependencies**: pandas、numpy、numba（選配，no-op decorator 降級）、pydantic、pytest

**Storage**: N/A —— 測試全程離線，使用程序內合成 K 線（沿用既有測試慣例，不讀 `data/*.csv`、不碰 SQLite）

**Testing**: pytest；新增 `pytest.ini` 註冊 `performance` marker（目前 repo 無任何 pytest 設定檔）

**Target Platform**: 本機 macOS + GitHub Actions ubuntu 標準 runner

**Project Type**: 既有單體 Python 專案的測試套件擴充 + 兩處小型引擎重構

**Performance Goals**: 單根新 K 線到達後的全量重算路徑（≈5 日 × 5 分 K 的監控視窗與 10,000 根壓力情境）中位數 < 100ms

**Constraints**: 測試離線可跑；Parity 零容差（`assert_series_equal` 預設嚴格比對）；有/無 Numba 兩模式結果一致

**Scale/Scope**: 3 個新測試檔 + 1 個共用 fixture 模組；重構觸及 `ladder_system.py`、`backtester.py`、`monitor_signals.py`、`data_ingestion.py`、`config/config.yaml` + schema

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 評估 | 結果 |
| :--- | :--- | :--- |
| I. 看前偏誤防禦（NON-NEGOTIABLE） | 前綴一致性測試本身就是看前偏誤的結構性防禦（若任何指標偷看未來，前綴計算必與全量不符）。`build_indicator_frame()` 重構**必須逐行搬移、不改任何 shift/時序語意**，並附前後回測對照（同資料、同成本）：交易筆數與每筆成交價完全相同方可合併。 | ✅ PASS（附重構迴歸閘門） |
| II. 真實摩擦成本（NON-NEGOTIABLE） | 本功能不產生績效數字；重構對照回測沿用 config 現行費率。 | ✅ PASS |
| III. 規格驗收標準映射測試 | 本規格即原則 III 的直接執行：spec 001 SC-003~005 各對應至少一個測試檔（見 contracts/）。 | ✅ PASS |
| IV. 效能紀律 | 不動熱路徑。Parity 測試在測試碼中對前綴取樣重算（取樣 ~40 個截斷點而非全部 N 點，避免 O(N²) 拖垮 CI）——測試碼的 Python 迴圈不受熱路徑禁令約束。 | ✅ PASS |
| V. 組態集中化 | 新增的離群值閾值（`max_close_jump_ratio` 等）一律進 `config/config.yaml` 的新 `data_quality` 區塊 + Pydantic schema，禁止寫死在 `data_ingestion.py`。 | ✅ PASS（附行動項） |
| VI. 可重現性與資料衛生 | 測試合成資料固定 seed；不產生新的可再生成產物。`validate_data_contract` 的強化正是本原則「離群值須過濾並警告」的補完。 | ✅ PASS |

**Post-design re-check（Phase 1 完成後）**: 設計產物未引入新違規；離群值閾值已定義為 config 參數（見 data-model.md）；重構迴歸閘門已寫入 quickstart.md 驗證步驟。GATE 通過。

## Project Structure

### Documentation (this feature)

```text
specs/004-acceptance-tests/
├── plan.md              # 本檔
├── research.md          # Phase 0：關鍵決策與替代方案
├── data-model.md        # Phase 1：指標欄位契約、資料品質參數、合成資料模型
├── quickstart.md        # Phase 1：驗證指南（含重構迴歸閘門步驟）
├── contracts/
│   ├── indicator-frame.md   # build_indicator_frame() 的欄位與時序契約
│   └── data-contract.md     # validate_data_contract 強化後的規則契約
└── tasks.md             # Phase 2（/speckit-tasks 產出，本命令不建立）
```

### Source Code (repository root)

```text
ladder_system.py            # +build_indicator_frame(df, params) —— 正典指標組裝入口
backtester.py               # run_backtest 內聯區塊（118-163）改呼叫共用函式
monitor_signals.py          # check_new_signals 內聯區塊（117-141）改呼叫共用函式
data_ingestion.py           # validate_data_contract 增加 price>0 與跳動比率檢查；
                            # clean/validate 的警告改走 logging
config/config.yaml          # +data_quality: {max_close_jump_ratio, ...}
config_schema.py（或既有 schema 檔）  # +DataQualityConfig Pydantic 模型
pytest.ini                  # 新增：註冊 performance marker
.github/workflows/tests.yml # no-numba 重跑清單加入 test_acceptance_parity.py；
                            # 主跑保持無 -m 過濾（performance 預設納入 CI）

tests/
├── acceptance_fixtures.py       # 共用合成 K 線建構器（固定 seed、含缺漏/離群變體）
├── test_acceptance_parity.py    # US1：前綴一致性（SC-003）
├── test_acceptance_latency.py   # US2：@pytest.mark.performance（SC-004）
└── test_acceptance_data_quality.py  # US3：缺漏 + 離群（SC-005）
```

**Structure Decision**: 沿用現行扁平單體佈局。唯一新增抽象是
`build_indicator_frame()`——它不是新層，而是把既存的兩份重複程式碼合而為一
（消除 silent-drift 風險，並給 Parity 測試一個正典受測入口）。

## Complexity Tracking

無憲法違規需要證成。`build_indicator_frame()` 重構的必要性：更簡單的做法
（測試自行複製第三份組裝邏輯）會把 drift 風險從兩份升到三份，且測的不是
生產路徑，違反本規格的存在目的。
