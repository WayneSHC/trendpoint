# Implementation Plan: 台指期成本/口數模型（008b — 期貨可交易回測）

**Branch**: `009-taifex-cost-model` | **Date**: 2026-07-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/009-taifex-cost-model/spec.md`

## Summary

在 008a 資料層抽象之上，以**可插拔成本/sizing/契約元件**（鏡像 008a adapter 模式）讓台指期
（TX/MTX/TMF）可被誠實回測：TAIFEX 權威每口費率（定額兩邊 + 期交稅兩邊 + tick 滑價）、
名目值百分比保證金、使用率上限整數口 sizing、return-on-margin 會計、爆倉終止護欄。
現貨路徑經由「現股元件精確重現現況」達成位元不變（SC-001 硬關卡）；008a 期貨回測拒絕護欄退役。
做空不在範圍（spec 003）。

## Technical Context

**Language/Version**: Python 3.10+（開發環境 `.venv` = 3.13）

**Primary Dependencies**: pandas、numpy、pydantic v2（既有；無新增依賴）

**Storage**: SQLite `trendpoint.db`（008a 既有 `fut_*` 表；本 spec 不改資料層）

**Testing**: pytest（`pytest -q` 全綠為合併關卡；新增單元 + e2e + 看前偏誤防線）

**Target Platform**: 本機 CLI（`run_backtest.py` 等入口腳本）＋ Streamlit 儀表板（本 spec 不動 UI）

**Project Type**: 單一 Python 專案（repo 根平鋪模組，維持現狀）

**Performance Goals**: 憲章 IV——回測向量化預計算不退化；新元件為 per-trade 純函式
（每筆交易 O(1)），不進逐根熱路徑迴圈的向量化部分

**Constraints**: 現貨路徑位元不變（SC-001）；費率 SoT = `config/config.yaml` `trading_cost`；
所有新參數走 Pydantic schema

**Scale/Scope**: 引擎觸碰面 = `backtester.py` 成本/sizing/會計注入點（約 5 處：進場 232-250、
部分出場 294-305、全出場 323-328、權益計算、summary）+ `portfolio_backtester.py` 對應處；
新模組 1 個（成本/sizing 元件）；config schema 擴充；測試新增 4-5 檔

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 關卡 | 評估 |
|------|------|------|
| I 防禦看前偏誤（NON-NEG） | sizing 只用訊號根收盤權益；第 N 根訊號、N+1 開盤成交；`test_lookahead_bias.py` 補期貨防線 | ✅ FR-007/SC-005 直接編入；沿用引擎既有 sig_row/成交分離結構，不新增 rolling 計算 |
| II 真實摩擦成本（NON-NEG） | 期貨費率含手續費/期交稅/滑價，SoT = `trading_cost` | ✅ 本 spec 的存在理由；TAIFEX 權威費率 + 禁零成本（SC-002/SC-004/SC-007） |
| III 規格可測試 | 每 SC ≥ 1 測試 | ✅ SC-001~007 全對映（見 spec + quickstart V1-V6） |
| IV 效能紀律 | 不得使逐根迴圈劣化為逐筆 Python 重算 | ✅ 元件為 per-trade O(1) 純函式；指標預計算不動 |
| V 組態集中化 | 參數只進 config + Pydantic | ✅ `trading_cost.futures` + Instrument `contract`；SC-007 grep 稽核硬編碼 |
| VI 可重現性與資料衛生 | 測試確定性、不引入外部即時依賴 | ✅ e2e 用 008a mock adapter（seeded 確定性）；無網路依賴 |

**初評：無違反。** Complexity Tracking 免填。

**Post-Phase-1 再評（設計完成後）**：資料模型與契約未引入任何違反——元件注入不改變
訊號計算路徑（I）、費率全走 config（II/V）、契約文件對映測試（III）、無效能熱點（IV）、
mock 確定性（VI）。**通過。**

## Project Structure

### Documentation (this feature)

```text
specs/009-taifex-cost-model/
├── spec.md              # 規格（已完成 + clarify 3 問）
├── plan.md              # 本檔
├── research.md          # Phase 0：決策記錄 D1-D9
├── data-model.md        # Phase 1：實體與欄位
├── quickstart.md        # Phase 1：驗證場景 V1-V6
├── contracts/
│   └── cost-model-contracts.md   # Phase 1：元件契約
├── checklists/requirements.md    # specify 品質檢核（16/16）
└── tasks.md             # Phase 2（/speckit-tasks 產出，非本命令）
```

### Source Code (repository root)

```text
trading_costs.py             # 新模組：ContractSpec 消費端——CostModel(ABC) + EquityCostModel
                             #   + FuturesCostModel；PositionSizer(ABC) + EquitySizer + FuturesSizer；
                             #   for_asset_class() 工廠（MPL-2.0 標頭）
instruments.py               # 擴充：ContractSpec(frozen) + Instrument.contract: ContractSpec|None
config/
├── config.py                # 擴充：FuturesCostConfig（trading_cost.futures 巢狀）+ 驗證
└── config.yaml              # 擴充：trading_cost.futures 區塊；data.instruments 期貨帶 contract
backtester.py                # 注入點改造：成本/sizing/會計走元件；護欄退役（assert_backtestable
                             #   放行 futures）；run_backtest 增 instrument/元件參數（預設=現股元件）
portfolio_backtester.py      # 同步注入（引擎層一致）
run_backtest.py              # 入口：期貨 instrument 分派期貨元件；護欄呼叫移除/放行
tests/
├── test_trading_costs.py        # 新：成本數學單元（SC-002）+ 保證金/口數單元（SC-003）
├── test_futures_backtest_e2e.py # 新：mock TXF/MTX 端到端（SC-004）+ 護欄退役（SC-006）
├── test_lookahead_bias.py       # 擴充：期貨 sizing/成交防線（SC-005）
├── test_futures_backtest_guard.py # 改寫：護欄語意反轉（futures 不再拋錯；equity 不受影響）
└── （既有全套 = SC-001 parity 關卡）
```

**Structure Decision**: 維持 repo 根平鋪模組慣例（如 `instruments.py`、`db_security.py`）。
新增單檔 `trading_costs.py` 承載全部元件——三個小類族共享 ContractSpec 依賴，單檔內聚且
避免過早建包；若日後 003 擴充再考慮拆包。引擎注入採「參數預設 = 現股元件」策略，
使既有呼叫零改動（parity 保護），期貨路徑由入口顯式注入。
