# Implementation Plan: 台指期做空（Short Side, Futures-Only）

**Branch**: `003-short-side` | **Date**: 2026-07-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-short-side/spec.md`

## Summary

以**方法 A（in-place 方向分支）**完成多空對稱：`check_entry_signal` 增 `direction`
參數（四維度鏡像）、`manage_position` 補 `direction == -1` 分支（吊燈用現成
`chandelier_short`）、引擎以方向因子貫穿 P&L/mark-to-market/爆倉、`enable_short`
（預設 false）＋現貨結構硬邊界。008b 成本/sizing 元件**零改動復用**。監控推播
已有空頭訊號文案，僅補期貨 instrument 迭代與 mock 標示。SC-002 以數值鏡像變換
＋手工情境對雙法驗證。

## Technical Context

**Language/Version**: Python 3.10+（`.venv` = 3.13）

**Primary Dependencies**: pandas、numpy、pydantic v2（既有；無新增）

**Storage**: SQLite `trendpoint.db`（`fut_*` 表既有；本 spec 不動資料層）

**Testing**: pytest（合併關卡全綠；新增鏡像對稱/空方 e2e/lookahead/硬邊界測試）

**Target Platform**: 本機 CLI + monitor_signals（推播能力）；UI 不動

**Project Type**: 單一 Python 專案（repo 根平鋪模組）

**Performance Goals**: 憲章 IV——訊號層零新計算（`bos_signal`/`mss_signal` 已含 ±1）；
空方 regime 為既有 regime filter 之鏡像分量（向量化一次計算）；per-trade 邏輯 O(1)

**Constraints**: 零回歸雙保證（現貨全套 + 008b 期貨 long-only 基準位元不變，
`enable_short` 預設 false 保證）；參數走 config + Pydantic

**Scale/Scope**: 觸碰面 = `ladder_system.py`（check_entry_signal + manage_position +
regime 空方分量）、`backtester.py`（空方進場分支 + 方向因子會計）、
`config/config.py`（enable_short + 現貨 override 驗證）、`run_backtest.py`（旗標穿線）、
`monitor_signals.py`（期貨迭代 + mock 標示）；測試新增 3-4 檔；
**訊號偵測層（detect_market_structure）與 008b 成本元件零改動**

## 已驗證之實作事實（plan 前查證）

| 事實 | 位置 | 對 003 的意義 |
|------|------|--------------|
| `bos_signal` 已編碼 -1（bear_bos）、`mss_signal` 已編碼 -1（007） | ladder_system.py:274-282 | 訊號層**零改動**——003 純消費端對稱化 |
| `PositionManager.direction` 欄位已存在（1 多/-1 空） | :585 | 設計預留，直接使用 |
| `chandelier_short` 已計算並在指標框架 | :346,530 | 空方出場線現成 |
| `check_entry_signal` 四維度硬編多方 | :606-629 | 需 direction 參數鏡像化 |
| `manage_position` 僅 `direction == 1` 分支、只收 `chandelier_long` | :650-687 | 需 -1 分支 + `chandelier_short` 可選參數（back-compat） |
| monitor_signals **已推播空頭 MSS/BOS 訊息**（-1 文案在） | monitor_signals.py:140-156 | FR-010 僅缺期貨 instrument 迭代 + mock 標示 |
| `regime_ok` 單一欄位（MA 分量為多方向） | :514-516 | 需空方鏡像分量（ADX/ER 共用、MA 反向） |
| 008b 元件成本對稱、sizer 無方向 | trading_costs.py | **零改動復用** |

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 關卡 | 評估 |
|------|------|------|
| I 看前偏誤（NON-NEG） | 空方同受訊號根/N+1 開盤紀律；鏡像變換不引入新 rolling | ✅ FR-007/SC-005 編入；訊號層不動 |
| II 摩擦成本（NON-NEG） | 空方成本 = 008b 模型兩邊各收；無借券費虛構 | ✅ 元件零改動；SC-001 斷言非零成本 |
| III 規格可測試 | SC-001~008 全對映 | ✅ quickstart V1-V6 |
| IV 效能紀律 | 訊號零新計算；空方 regime 向量化 | ✅ |
| V 組態集中 | `enable_short` 進 SingleStrategyParams + Pydantic；現貨 override 驗證 | ✅ |
| VI 可重現/衛生 | 鏡像變換與 fixture 確定性；無網路依賴 | ✅ |

**初評：無違反。** Complexity Tracking 免填。

**Post-Phase-1 再評**：資料模型（方向因子/鏡像映射）與契約未引入違反；
`enable_short` 預設 false 使零回歸由構造保證。**通過。**

## Project Structure

### Documentation (this feature)

```text
specs/003-short-side/
├── spec.md              # 已完成（含 clarify）
├── plan.md              # 本檔
├── research.md          # D1-D7 決策
├── data-model.md        # 方向因子/鏡像映射/config 欄位
├── quickstart.md        # V1-V6 驗證
├── contracts/short-side-contracts.md
└── checklists/requirements.md（16/16）
```

### Source Code (repository root)

```text
ladder_system.py         # check_entry_signal 增 direction 參數（四維度鏡像）；
                         #   manage_position 增 direction==-1 分支 + chandelier_short 可選參數；
                         #   calculate_regime_filter 增空方 MA 分量（regime_ok_short 欄位）
backtester.py            # 空方進場分支（bos==-1 續勢 + mss==-1 反轉、三關價互斥裁決、
                         #   enable_short×is_futures 閘門）；方向因子會計（P&L/MTM/爆倉）；
                         #   SELL_SHORT/COVER_HALF/COVER_ALL 動作 + metrics 空方配對
config/config.py         # SingleStrategyParams.enable_short=False；SystemConfig validator：
                         #   現貨 ticker override 明設 enable_short=true → fail-fast
config/config.yaml       # （預設不加旗標——預設 false 即零回歸；期貨啟用示例入註解）
run_backtest.py          # params.enable_short 穿線至引擎
monitor_signals.py       # 迭代 registry 全 instrument（含期貨）；mock 源訊息標示
tests/
├── test_short_side.py        # 鏡像變換測試（SC-002a）+ 手工情境對（SC-002b）+ 裁決 + 硬邊界
├── test_short_futures_e2e.py # 空方 e2e（成本/整數口/爆倉上漲觸發）（SC-001/006）
├── test_lookahead_bias.py    # 擴充：空方防線（SC-005）
└── test_monitor_short.py     # 推播 dry-run（SC-008）
```

**Structure Decision**: 全部 in-place 分支（方法 A）——`PositionManager.direction`
本為此預留；無新模組（空方非新子系統，是既有單元的方向對稱化）。引擎空方進場
分支鏡像 007 的 BOS/MSS 分流結構。
