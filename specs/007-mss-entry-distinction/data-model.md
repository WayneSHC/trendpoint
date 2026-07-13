# Phase 1 Data Model: MSS 進場區別化

本功能不新增持久化 schema；「實體」為在 OHLCV DataFrame 上計算的**逐列序列/欄位**與**進場決策物件**。以下描述欄位、驗證規則與狀態。

## E1 — SwingPoint（樞紐點，逐列布林/值序列）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `is_swing_high` | bool series | `high[i]` 為 `[i−N, i+N]` 之極大 |
| `is_swing_low` | bool series | `low[i]` 為 `[i−N, i+N]` 之極小 |
| `swing_high_val` | float series | 樞紐處的 high，其餘 NaN |
| `swing_low_val` | float series | 樞紐處的 low，其餘 NaN |

**驗證/不變式**：樞紐於 bar `i` 成立，但**確認時點為 `i+N`**；下游只能使用 `shift(N)` 後的版本。前 `N`（左）與尾 `N`（右）邊界不足處為 False/NaN，不得報錯。

## E2 — ConfirmedStructure（已確認結構，逐列）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `conf_swing_high` | float series | 截至 `t` 已確認的最近 swing high 值（ffill） |
| `conf_swing_low` | float series | 截至 `t` 已確認的最近 swing low 值（ffill） |
| `last_HL` | float series | 上升結構中最近已確認的較高低點（HL）價位 |
| `last_LH` | float series | 下降結構中最近已確認的較低高點（LH）價位 |
| `trend_bias` | int series | +1 上升 / −1 下降 / 0 不明（由已確認樞紐 HH/HL/LH/LL 判定） |

**驗證/不變式**：所有欄位在 `t` 僅依賴 `≤ t−N` 的樞紐（看前偏誤）。`trend_bias==0` 時 MSS 不觸發。

**狀態轉移（trend_bias）**：`0 →(連續 HH+HL)→ +1`；`0 →(連續 LH+LL)→ −1`；`+1 →(看跌 MSS 確立/結構破壞)→` 可轉 `−1`（反之亦然），轉移僅由已確認樞紐驅動。

## E3 — StructureSignals（訊號輸出，沿用既有欄位語意）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `bos_signal` | int series (−1/0/+1) | 續勢：同向 rolling 突破（**語意不變**） |
| `mss_signal` | int series (−1/0/+1) | **校正後**：反向已確認波段點突破 + 位移（+ FVG 若啟用） |

**驗證/不變式（對應 FR-004 / SC-001）**：存在至少一個 bar 其 `mss_signal==±1` 而同向 `bos_signal` 不成立；同一 bar 同向不得同時 `bos_signal` 與 `mss_signal` 皆為該向。

**FVG 交互（FR-008）**：`use_fvg=True` 時，`mss_signal` 僅在近 `fvg_lookback` 根內有同向 FVG 才保留（沿用 `ladder_system.py:173-181` 閘門，現在套在校正後 MSS 上）。

## E4 — ReversalEntry（反轉進場決策，回測迴圈內）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `direction` | int (−1/+1) | 來自 `mss_signal`（+1 做多反轉、−1 做空反轉） |
| `position_action` | enum | `OPEN_NEW` / `CLOSE_THEN_REVERSE` / `SKIP`（見規則） |
| `k_used` | float | `mss_ladder_k`（None 時 = `ladder_k`） |
| `gate_profile` | set | 反轉 profile：`disabled_filters={'trend','global'}`（D6） |

**規則（FR-006）**：無部位→`OPEN_NEW`；持反向部位→`CLOSE_THEN_REVERSE`；持同向部位→`SKIP`。**短側（`direction==−1`）之執行依賴 spec 003**；在 003 前，`direction==−1` 的進場為 no-op（僅記錄訊號）。

## E5 — Config 欄位（新增，SingleStrategyParams）

| 欄位 | 型別 | 預設 | 驗證 |
|---|---|---|---|
| `swing_fractal_n` | int | 2 | ≥1 |
| `mss_reversal_entry` | bool | True | — |
| `mss_ladder_k` | float \| None | None（繼承 `ladder_k`） | >0 若非 None |
| `mss_volume_mult` | float | 1.5 | >0 |

**不變式**：以上為唯一可調來源（憲章 V）；演算法/回測/UI 不得硬編碼對應數值（含移除 `detect_market_structure` 內硬編的 `1.5`）。
