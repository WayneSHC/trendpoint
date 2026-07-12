# Implementation Plan: MSS 進場區別化（fractal 反轉校正）

**Branch**: `007-mss-entry-distinction` | **Date**: 2026-07-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/007-mss-entry-distinction/spec.md`

## Summary

把 MSS 從「高量能同向 BOS」的 stub（`ladder_system.py:163-169`，作者自標「此處簡化示意」）校正為理論的反轉訊號：以對稱碎形偵測 swing 高/低點，判定 HH/HL/LH/LL 結構與趨勢偏向，將 MSS 定義為「反向已確認波段點被突破 + 位移確認」。BOS 續勢語意不動，MSS 與 BOS 因此語意分離、不再是子集。並在進場路徑新增**雙向 MSS 反轉進場**，讓 MSS 品質（含 spec 002 的 FVG 閘門）真正影響回測交易與 P&L。

**跨規格依賴（重要）**：本規格為雙向（多+空），但現行程式為 **Long-Only**（`backtester.py` 僅買進做多；`check_entry_signal` 硬編 `structure_sig == 1` 且動能/趨勢維度皆偏多）。短側能力（做空部位管理、Chandelier 空方出場、做空成本、`check_entry_signal` 的方向泛化）由**重開的 spec 003** 負責。因此：
- **訊號層**（swing/結構/MSS 判定）方向無關，007 完整實作雙向。
- **長側反轉進場執行**於 007 直接可行（不需 003）。
- **短側反轉進場執行**（看跌反轉做空）之任務**阻塞於 spec 003**；在 003 落地前，看跌 MSS 只產生訊號、不成交。

## Technical Context

**Language/Version**: Python 3.10+

**Primary Dependencies**: pandas / numpy（向量化熱路徑）、numba（選配 `@jit`，須有無 numba 降級）、pydantic（組態驗證）

**Storage**: SQLite `trendpoint.db`（OHLCV 來源，本功能只讀取已載入的 DataFrame；不改 schema）

**Testing**: pytest（`tests/`）；新增 `tests/test_mss_reversal.py`、擴充 `tests/test_lookahead_bias.py`

**Target Platform**: 本機研究工具（批次回測 `run_*.py` + Streamlit 儀表板 `app.py`）

**Project Type**: 單一專案（演算法庫 `ladder_system.py` + 回測 CLI + Streamlit UI）

**Performance Goals**: 熱路徑向量化（Pandas→NumPy→Numba），百萬級 K 線不得用純 Python 迴圈或 `DataFrame.apply()`（憲章 IV）。碎形偵測與結構分類須以 rolling/向量化實作。

**Constraints**: 看前偏誤防禦（碎形確認延遲 N 根，`shift(N)` 對齊）；摩擦成本納入前後回測；新參數集中於 Pydantic + `config.yaml`。

**Scale/Scope**: 日線/日內 K 線，多標的（`2330.TW`、`0050.TW`、`00878.TW`、`00919.TW`、`00631L.TW`）。

## Constitution Check

*GATE: Phase 0 前必過；Phase 1 設計後複檢。*

| 原則 | 本規格如何滿足 | Gate |
|---|---|---|
| **I 看前偏誤（NON-NEGOTIABLE）** | 碎形樞紐 `i` 僅在 bar `i+N` 可斷言；用 `shift(N)` + forward-fill「已確認」樞紐，決策 bar `t` 僅引用 `i≤t−N`。訊號第 N 根收盤出、N+1 開盤成交。`tests/test_lookahead_bias.py` 新增 MSS fractal 遮蔽測試 + 時序契約測試。 | ✅ 設計即防禦 |
| **II 摩擦成本（NON-NEGOTIABLE）** | 前後回測與 FVG on/off 對照均含 `trading_cost`（commission/tax/slip，`config.yaml` 唯一來源）。 | ✅ |
| **III 規格↔測試** | spec 的 6 條 SC 各對應 pytest（見 quickstart.md 對照表）。 | ✅ |
| **IV 效能紀律** | 碎形（rolling max/min + shift）與結構分類須向量化；HH/HL/LH/LL 分類避免逐列 Python 迴圈（用向量化比較或 numba，並附降級）。 | ⚠️ 設計約束，見 research D3 |
| **V 組態集中** | 新參數 `swing_fractal_n`、`mss_reversal_entry`、`mss_ladder_k`、`mss_volume_mult` 進 `SingleStrategyParams`（config.py）+ `config.yaml`，禁硬編碼。 | ✅ |
| **VI 可重現/衛生** | 回測產物續走 `.gitignore`；不改資料契約。 | ✅ |

**違反項**：無需 Complexity Tracking 的憲章違反。唯一結構性風險為**跨規格依賴 spec 003**（見 Summary），以任務阻塞關係管理，非憲章違反。

## Project Structure

### Documentation (this feature)

```text
specs/007-mss-entry-distinction/
├── plan.md              # 本檔
├── research.md          # Phase 0：設計決策（D1–D9）
├── data-model.md        # Phase 1：實體與欄位契約
├── quickstart.md        # Phase 1：驗證/執行指南 + SC↔測試對照
├── contracts/
│   └── library-contracts.md   # Phase 1：函式與欄位/組態契約
├── checklists/
│   └── requirements.md  # 由 /speckit-specify 產生
└── tasks.md             # 由 /speckit-tasks 產生（本命令不建立）
```

### Source Code (repository root)

```text
ladder_system.py          # 新增 detect_swing_points；擴充 detect_market_structure（MSS 反轉語意）；
                          #   新增結構分類/趨勢偏向；反轉進場閘門 profile（複用 check_entry_signal 的 disabled_filters）
config/config.py          # SingleStrategyParams 新增 4 個欄位
config/config.yaml        # strategy.default（+ 選配 ticker_overrides）新增對應鍵
backtester.py             # 新增 MSS 反轉進場分支（長側可行；短側呼叫 003 提供的做空進場）
portfolio_backtester.py   # 同上，組合層
tests/test_mss_reversal.py        # 新增：swing/結構/MSS 真值表單元測試
tests/test_lookahead_bias.py      # 擴充：MSS fractal 遮蔽 + 時序契約
tests/test_fvg_confirmation.py    # 檢視：FVG 閘門套用於新 MSS 後的行為
```

**Structure Decision**: 沿用單一專案結構，演算法集中於 `ladder_system.py`，UI/回測不內嵌邏輯（憲章工作流 4）。所有改動落在既有模組，無新增套件或抽象層。

## Complexity Tracking

> 無憲章違反需證成。以下為跨切面依賴，供任務排序參考（非違反）。

| 事項 | 為何需要 | 管理方式 |
|---|---|---|
| 依賴 spec 003（短側） | 007 為雙向，但現行為 Long-Only；做空進場/出場/成本泛化屬 003 | 短側「進場執行」任務標記 blocked-on-003；訊號層與長側於 007 獨立完成 |
