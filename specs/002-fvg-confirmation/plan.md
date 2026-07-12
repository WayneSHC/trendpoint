# Implementation Plan: MSS 之 FVG（公平價值缺口）確認

**Branch**: `002-fvg-confirmation` | **Date**: 2026-07-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-fvg-confirmation/spec.md`

## Summary

為 MSS（結構破壞）訊號加上公平價值缺口（FVG）確認：只有在近 M 根 K 線內
存在同向 FVG 時，MSS 才成立。目的是濾掉盤整區的假反轉訊號（SC-001），
並以消融測試量化 FVG 對每筆交易期望值的貢獻（SC-002）。

前置調查（見 [research.md](research.md)）定出的核心設計：**FVG 比照現行
`regime` 濾網的模式**——它是「建構期濾網」，把 FVG 確認折進
`detect_market_structure` 的 `mss_signal` 計算，而非在交易迴圈另設閘門。
關掉時（`use_fvg=False` / `'fvg' in disabled_filters`）`mss_signal` 逐位元
回到 spec 001 基準（Acceptance Scenario 3 的精確重現）。這個設計有三個好處：

1. **消融即精確基準**：FVG off → `detect_market_structure` 走原路徑 →
   回測結果與 spec 001 零差異（憲法 VI）。
2. **004 parity 測試免費守護 FVG 的看前偏誤**：`mss_signal` 是 parity 欄位，
   任何 FVG 洩漏未來資料，前綴一致性立刻不符——不需重造防線。
3. **BOS 不受影響**：FVG 只確認 MSS；BOS 進場照舊，進場訊號不會整批消失。

**範圍收斂**：FR-002 的「位移動能（振幅 > 1.2×ATR）」現行已由進場的
**波動維度**（`check_entry_signal` volatility filter）處理，不在
`detect_market_structure` 內。spec 002 只新增 FVG 這一條，不重做位移。

## Technical Context

**Language/Version**: Python 3.10 / 3.12（CI 雙版）

**Primary Dependencies**: pandas、numpy（FVG 純向量化：`shift` + `rolling`）、pydantic、pytest

**Storage**: N/A（沿用既有 db/CSV；測試離線合成）

**Testing**: pytest；新增 FVG 看前偏誤案例（SC-003）+ FVG 單元測試；沿用 004 parity 覆蓋

**Target Platform**: 本機 + GitHub Actions

**Project Type**: 既有單體 Python 的訊號模組擴充（`detect_market_structure` + 參數穿線）

**Performance Goals**: 不劣化；FVG 為 2 個 `shift` + 1 個 `rolling`，O(N) 向量化（憲法 IV）

**Constraints**: FVG off 時 `mss_signal` 與 spec 001 逐位元相同；FVG 計算全程因果（causal）

**Scale/Scope**: 觸及 `ladder_system.py`（FVG + detect_market_structure + build_indicator_frame）、`backtester.py`（參數穿線 + 'fvg' 消融）、`config/config.py`+yaml（2 參數）、`run_ablation.py`（+1 列）、`monitor_signals.py`（預設啟用 FVG）、3 個測試檔

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 評估 | 結果 |
| :--- | :--- | :--- |
| I. 看前偏誤（NON-NEGOTIABLE） | FVG 以 `shift(2)` 定義（K1=t−2、K3=t），近 M 根用 `rolling(M)`，全程只用已收盤 bar；引擎照舊在 `struct_row=iloc[i−2]` 消費，維持原一根延遲。**004 parity 測試自動覆蓋**（mss_signal 是 parity 欄位），另加 `use_fvg=True` 的 parity 變體與 `test_lookahead_bias.py` 的 tail-tamper 案例（SC-003）。 | ✅ PASS（附測試） |
| II. 真實摩擦成本（NON-NEGOTIABLE） | 不產生新績效路徑；消融報告沿用 config 現行費率。 | ✅ PASS |
| III. 規格驗收映射測試 | SC-001（MSS 數量下降且非零）、SC-002（消融 EV 報告）、SC-003（看前偏誤測試）各對應測試/腳本產出。 | ✅ PASS |
| IV. 效能紀律 | FVG = 2×`shift` + 1×`rolling(M).max()`，純向量化，無 Python 迴圈。 | ✅ PASS |
| V. 組態集中 | 新參數 `use_fvg`(bool)、`fvg_lookback`(int) 進 `SingleStrategyParams` + config.yaml；比照 `atr_period` 的 config 穿線路徑，禁止硬編碼。 | ✅ PASS |
| VI. 可重現性 | `use_fvg=False` → `mss_signal` 走原路徑 → 回測與 spec 001 零差異（迴歸閘門）。 | ✅ PASS |

**Post-design re-check**: 設計未引入新違規。`structure_period` 現行硬編碼為 10
（既有技術債，非本規格引入）——本規格沿用該常數，不擴大範圍去修它，
但把新參數走**正確的 config 穿線路徑**（`atr_period` 模式），不複製硬編碼債。
GATE 通過。

## Project Structure

### Documentation (this feature)

```text
specs/002-fvg-confirmation/
├── plan.md              # 本檔
├── research.md          # Phase 0：關鍵決策（含 SC-001 非零風險與緩解）
├── data-model.md        # Phase 1：FVG 欄位語意、參數、mss 閘門真值表
├── quickstart.md        # Phase 1：驗證指南（含消融與基準重現閘門）
├── contracts/
│   └── fvg-detection.md # detect_market_structure 擴充後的契約與時序保證
└── tasks.md             # Phase 2（/speckit-tasks 產出，本命令不建立）
```

### Source Code (repository root)

```text
ladder_system.py            # +_detect_fvg(); detect_market_structure 加 use_fvg/fvg_lookback
                            # 並以同向 FVG 閘門 mss_signal；build_indicator_frame 穿線兩參數
backtester.py               # run_backtest 加 use_fvg/fvg_lookback kwargs；
                            # effective_use_fvg = use_fvg and 'fvg' not in disabled_filters；
                            # 轉發給 build_indicator_frame（比照 include_regime 模式）
config/config.py            # SingleStrategyParams +use_fvg(bool=True) +fvg_lookback(int=3,ge=1)
config/config.yaml          # strategy.default +use_fvg/fvg_lookback（per-ticker override 自動生效）
run_backtest.py             # 呼叫端把 params.use_fvg/fvg_lookback 傳入 run_backtest
run_ablation.py             # ABLATION_TARGETS +("停用 FVG 確認","fvg")；穿線兩參數
monitor_signals.py          # build_indicator_frame 呼叫加 use_fvg=True, fvg_lookback=3（即時告警亦套 FVG）

tests/
├── test_lookahead_bias.py         # +FVG tail-tamper 案例（SC-003）
├── test_fvg_confirmation.py       # 新增：FVG 偵測單元 + mss 閘門 + 基準重現（use_fvg=False 零差異）
└── test_acceptance_parity.py      # 加 use_fvg=True 參數化變體，確保 FVG 欄位納入前綴一致性
```

**Structure Decision**: 沿用扁平單體。唯一新函式 `_detect_fvg()` 是純向量化
FVG 布林偵測；`detect_market_structure` 與 `build_indicator_frame` 只擴充
既有簽名（新增 keyword 參數、baseline-preserving 預設），不新增抽象層。

## Complexity Tracking

無憲法違規需證成。設計刻意**不**去修 `structure_period` 的硬編碼債
（超出範圍、且會擴大迴歸面）；新參數走既有正確路徑，不因遷就舊債而複製它。
