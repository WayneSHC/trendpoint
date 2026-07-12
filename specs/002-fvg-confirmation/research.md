# Research: MSS 之 FVG 確認（spec 002）

**Date**: 2026-07-12 | **Input**: 程式碼調查（Explore agent，逐檔核對 file:line）

Phase 0 的關鍵決策。每項含 Decision / Rationale / Alternatives。

## R1. FVG 是「建構期濾網」，比照 regime 模式折進 mss_signal

**Decision**: FVG 確認在 `detect_market_structure` 內完成——先算原始 MSS
（現行邏輯不動），再以「近 M 根內存在同向 FVG」的布林遮罩把不符者歸零：

```
raw_mss ∈ {-1,0,+1}（現行）
fvg_up_present   = _detect_fvg(df, "up").rolling(M).max().astype(bool)     # 近 M 根有向上 FVG
fvg_down_present = _detect_fvg(df, "down").rolling(M).max().astype(bool)
若 use_fvg:
    mss = +1 只在 (raw_mss==+1 且 fvg_up_present) 時保留，否則 0
    mss = -1 只在 (raw_mss==-1 且 fvg_down_present) 時保留，否則 0
否則:
    mss = raw_mss（spec 001 基準，逐位元不變）
```

消融透過 `run_backtest` 計算 `effective_use_fvg = use_fvg and ('fvg' not in
disabled_filters)`，再傳入 `build_indicator_frame`——**完全比照現行 regime**
（`include_regime=('regime' not in disabled_filters)`，backtester.py:120）。

**Rationale**: 調查確認 regime 是唯一「建構期就被關掉」的濾網（不算 regime_ok
欄位、預設 True），其餘五維在交易迴圈以 `'x' in disabled_filters` 短路。
FVG 是 **MSS 的子條件**（spec FR-002），且 SC-001 要求「MSS 訊號數量下降」——
這代表 FVG 必須改變 `mss_signal` 本身，不能只在進場閘門放行。折進
`detect_market_structure` 讓 (a) MSS 計數直接反映 FVG、(b) `use_fvg=False`
時走原路徑 → 與 spec 001 逐位元相同（Acceptance Scenario 3）、(c) 004 parity
測試自動涵蓋（見 R4）。

**Alternatives considered**:
- *在 check_entry_signal 加第 6 維 `fvg_ok`*：MSS 計數不變（SC-001 失義），且 FVG 只影響「進場」不影響「訊號」，與 spec 語意（MSS 訊號本身要收斂）不符。
- *把 FVG 做成獨立進場訊號*：spec Assumption 明言 FVG 僅作確認、非獨立訊號。

## R2. FVG 定義與因果性（3 根 K 線，全用已收盤 bar）

**Decision**: 在 bar t 上，
- 向上 FVG（bullish gap）：`low(t) > high(t-2)` → `df['low'] > df['high'].shift(2)`
- 向下 FVG（bearish gap）：`high(t) < low(t-2)` → `df['high'] < df['low'].shift(2)`

「近 M 根內存在」以 `rolling(M).max()`（bool 視為 0/1）實作，包含當根 t。
M = `fvg_lookback`（config，預設 3）。

**Rationale**: FVG 的三根結構 K1=t−2、K2=t−1、K3=t；缺口在 K1 與 K3 之間
（K2 為位移中繼）。bar t 的 FVG 只用 t、t−2 兩根，皆在 t 收盤時已知 → 因果。
原始 MSS 已用 `close(t) > rolling_high.shift(1)`（用 t 的已收盤 close + 過去高點），
FVG 確認同樣只到 t，時序一致。引擎在 `struct_row=iloc[i−2]` 消費結構訊號
（backtester.py:156），維持原一根延遲設計，不需為 FVG 另加 shift。

**Alternatives considered**:
- *FVG 只看到 t−1（額外 shift 一根）*：過度保守，與現行 MSS 用 close(t) 的時序不一致，且會讓 FVG 與其確認的 MSS 錯位一根。
- *rolling 用 sum>0 而非 max*：等價；`max` 對 bool 更直觀（近 M 根任一為真）。

## R3.〔風險〕SC-001「數量下降且不為零」的經驗性未知

**Decision**: 把「FVG 確認後 MSS 仍 > 0」列為**實作期必須經驗驗證**的閘門，
而非計畫假設。緩解：`fvg_lookback`（M）可配置；若 M=3 在某標的把 MSS 歸零，
放寬 M（M 擴大 → 窗內更容易命中 FVG）。實作時對五檔標的實跑並記錄
FVG-on 的 MSS 計數，寫入 quickstart 驗證與 PR。

**Rationale**: MSS 本就稀少（004 的合成資料 600 根僅 6 個非零 MSS）。日線約
2400 根、MSS 事件數十個級別，若再要求 3 根內同向 FVG，有實際歸零風險 →
SC-001「不為零」失敗。但 MSS 的本質是「位移突破」，位移常伴隨跳空（FVG），
兩者相關性應為正 → M=3 大機率非零。這是經驗問題，設計上以「M 可調 + 實跑驗證」
消化，不在計畫階段猜死。BOS 進場不受 FVG 影響，故即使 MSS 歸零，回測仍有交易。

**Alternatives considered**:
- *預設更大的 M（如 5）*：先射箭再畫靶；先用理論預設 3、以資料決定是否放寬更誠實。
- *FVG 命中改用「回看 + 前看」對稱窗*：前看即看前偏誤，直接違憲，排除。

## R4. 看前偏誤防禦：三層，且 004 parity 免費覆蓋

**Decision**: 不為 FVG 重造防線，疊加三層既有機制：
1. **004 parity**（`test_acceptance_parity.py`）：加 `use_fvg=True` 參數化變體。
   `mss_signal` 是 parity 欄位，FVG 若洩漏未來，前綴一致性（`check_exact`）立即紅。
2. **tail-tamper**（`test_lookahead_bias.py`）：加一案例——回測跑原資料與
   「split 後 OHLC 全部加倍」的資料，斷言 split 前交易逐筆相同（FVG 欄位
   自動被 mock 資料驅動）。
3. **FVG 單元測試**（新 `test_fvg_confirmation.py`）：手工三根缺口 df，
   斷言 FVG 偵測與 mss 閘門在正確 bar 觸發、且 `use_fvg=False` 時零差異。

**Rationale**: 這正是 004 抽出 `build_indicator_frame()` + parity 的**設計紅利**
——新訊號的看前偏誤由既有測試自動守護。三層互補：parity 抓「未來回洩過去」、
tail-tamper 抓「引擎級端到端洩漏」、單元測試釘住「FVG 邏輯本身正確」。

**Alternatives considered**:
- *只靠 parity*：parity 抓洩漏，但不驗證 FVG「邏輯正確」（可能因果但算錯）；需單元測試補。

## R5. 參數穿線走 atr_period 模式（config → run_backtest kwargs → build_indicator_frame）

**Decision**: `use_fvg`、`fvg_lookback` 加入 `SingleStrategyParams`（config.py:41），
沿 `atr_period` 的既有穿線：`config.yaml` → `get_params_for_ticker` →
`run_backtest.py`/`run_ablation.py` 呼叫端 → `run_backtest` 具名 kwargs →
`build_indicator_frame` → `detect_market_structure`。per-ticker override 自動生效。

**Rationale**: 調查發現 `structure_period` 是**硬編碼 10**（backtester.py:115、
monitor_signals.py:120），是既有技術債。**不**沿用該壞模式；改沿 `atr_period`
這條正確的 config 穿線。憲法 V 要求可調參數集中於 config + Pydantic。
不在本規格順手修 `structure_period` 債（超範圍、擴大迴歸面），但也不複製它。

**Alternatives considered**:
- *沿用 structure_period 的硬編碼模式*：省事但複製技術債、違反憲法 V，排除。
- *在本規格一併修 structure_period 債*：範圍蔓延，另開議題較清楚。

## R6. Monitor 即時告警亦套 FVG（預設啟用）

**Decision**: `monitor_signals.check_new_signals` 的 `build_indicator_frame`
呼叫加 `use_fvg=True, fvg_lookback=3`（常數預設）。`build_indicator_frame` 的
`use_fvg` 預設為 `False`（baseline-preserving），故各呼叫端須明示啟用。

**Rationale**: 減少假反轉告警正是本規格目的，即時端也該享有。monitor 現行
**不穿線任何 strategy 參數**（連 structure_period 都硬編碼 10），故此處用常數
預設與現況一致，不擴大範圍去為 monitor 建 config 穿線（既有缺口，另議）。
`build_indicator_frame` 預設 `use_fvg=False` 確保 004 parity 既有呼叫
（不帶 use_fvg）行為不變，新變體顯式開啟。

**Alternatives considered**:
- *build_indicator_frame 預設 use_fvg=True*：會靜默改變所有未指定呼叫端的語意（含 004 既有 parity 呼叫），風險高；baseline-preserving 預設更安全。
- *monitor 完整 config 穿線*：正確方向但超本規格範圍；記入交接另議。
