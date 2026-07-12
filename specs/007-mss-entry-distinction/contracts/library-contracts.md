# Phase 1 Contracts: 函式 / 欄位 / 組態

本功能無對外 API；「契約」為演算法庫的函式簽章、DataFrame 欄位契約與組態 schema。變更須維持向後相容處已標註。

## C1 — `detect_swing_points(df, n) -> pd.DataFrame`（新增，ladder_system.py）

- **輸入**: `df` 含 `high`/`low`；`n:int`（碎形強度，來自 `swing_fractal_n`）。
- **輸出**: 欄位 `is_swing_high`、`is_swing_low`、`swing_high_val`、`swing_low_val`（見 data-model E1）。
- **時序契約**: 純結構偵測，回傳「原始樞紐」（於 bar `i` 對齊）；**確認延遲由呼叫端以 `shift(n)` 處理**（保持單一職責）。
- **效能**: 以 `rolling(2n+1, center=True).max()/.min()` 向量化比較，不得逐列迴圈。

## C2 — `detect_market_structure(...)`（擴充，ladder_system.py:143）

現行簽章：
```python
detect_market_structure(df, period=20, *, use_fvg=False, fvg_lookback=3) -> (mss_signal, bos_signal)
```
新簽章（新增關鍵字參數，維持既有預設之向後相容）：
```python
detect_market_structure(df, period=20, *, use_fvg=False, fvg_lookback=3,
                        swing_n=2, volume_mult=1.5) -> (mss_signal, bos_signal)
```
- **BOS 契約（不變）**: `bull_bos = close > rolling_high(shift1)`；`bear_bos = close < rolling_low(shift1)`。
- **MSS 契約（校正）**: 依 D2–D5：以 `detect_swing_points(df, swing_n)` → `shift(swing_n)` 確認 → 結構分類 → 反向點突破 + 位移（`volume_mult`）→（`use_fvg` 時）FVG 閘門。輸出 `mss_signal ∈ {−1,0,+1}`。
- **不變式**: `mss ⊄ bos`（SC-001）；`use_fvg=False` 時輸出**不再**與 spec 001 逐位元相同（語意已改）——此為預期破壞性變更，回歸錨點改由 `mss_reversal_entry=False` 於進場層提供。
- **看前偏誤**: 第 `i` 列僅依賴 `df.iloc[:i+1]` 且 MSS 僅用 `≤ i−swing_n` 之樞紐。

## C3 — 反轉進場閘門（複用 check_entry_signal，backtester / portfolio_backtester）

- **契約**: MSS 反轉進場呼叫既有 `check_entry_signal(..., structure_sig=mss_dir, disabled_filters=frozenset({'trend','global'}))`。
- **長側**（`mss_dir==+1`）: 直接可用（`structure_ok` 認 `==1`、`momentum_ok=close>open` 對反轉綠 K 成立）。
- **短側**（`mss_dir==−1`）: 需 `check_entry_signal` 方向泛化（`structure_sig==−1`、動能/趨勢翻轉）——**屬 spec 003**；007 於此處預留分支並在 003 前為 no-op。
- **部位動作**: 依 data-model E4 規則（先平再開/開新/略過）。

## C4 — Config schema（config/config.py: SingleStrategyParams）

新增欄位（Field 附預設與驗證，見 data-model E5）：
```python
swing_fractal_n:   int          = Field(2,    ge=1)
mss_reversal_entry: bool        = Field(True)
mss_ladder_k:      float | None = Field(None)          # None → 繼承 ladder_k
mss_volume_mult:   float        = Field(1.5, gt=0)
```
- **config.yaml**: `strategy.default` 新增上述鍵；`ticker_overrides.*` 可選覆寫。
- **相容性**: 均有預設，舊 `config.yaml` 不填亦可載入。

## C5 — 測試契約（tests/）

- `test_mss_reversal.py`（新）: swing 偵測、結構分類、MSS 真值表（HH/HL/LH/LL × 突破/未突破 × 位移有無 × FVG on/off）。
- `test_lookahead_bias.py`（擴充）: MSS fractal 遮蔽測試（遮 `>t` 後 MSS[t] 不變）+ 未確認樞紐不影響 + 時序契約（第 i 列僅依賴前 i 列）。
- 既有 `test_fvg_confirmation.py`、`test_acceptance_parity.py` 等: 檢視校正後行為，必要時更新斷言（記錄於 tasks）。
