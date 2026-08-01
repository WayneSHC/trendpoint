# Phase 0 Research: BOS 續勢進場的量能確認濾網

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-07-30

本檔記錄六個設計決策及其被否決的替代方案。凡 spec 的 Assumptions 在實作層
有歧義者，於此收斂為單一決定（D4 即為一例）。

---

## D1：濾網掛在進場判定層，不掛在訊號產生層

**Decision**: 量能條件作為 `check_entry_signal` 的一道確認維度，不修改
`detect_market_structure` 產生的 `bos_signal`。

**Rationale**: 反轉訊號的定義帶有互斥項（`ladder_system.py:254-258`）：

```python
bear_mss = (trend_up & (close < conf_swing_low) & displacement & (~bear_bos))
bull_mss = (trend_down & (close > conf_swing_high) & displacement & (~bull_bos))
```

`~bear_bos` / `~bull_bos` 的作用是保證同一根 K 線不同時是續勢與反轉
（spec 007 的「語意分離」）。若在訊號層把續勢訊號濾掉，該根的 `~bos` 由
False 翻為 True，原本被互斥排除的反轉訊號**轉為成立**。使用者以為只是
「少一個 BOS 進場」，實際上還多了一個 MSS 反轉進場——方向可能相反，
且該進場走的是完全不同的濾網 profile（`backtester.py:271-278` 放寬 trend 與 regime）。

這種副作用不會被任何現有測試抓到，也不會出現在績效摘要裡，只會讓消融結果
無法解釋。屬於必須在設計階段排除、而非實作階段除錯的問題。

**Alternatives considered**:
- **訊號層 gating**（`bull_bos = bull_bos & volume_ok`）：否決，理由如上。
- **新增獨立訊號欄 `bos_volume_signal`**：否決。訊號欄的語意是「市場結構發生了什麼」，
  而量能確認是「我們要不要據此進場」，屬於進場政策而非結構事實。混入訊號層
  會讓 `bos_signal` 不再是可獨立驗證的結構判定。
- **在引擎迴圈內就地重算量能**：否決。違反 FR-010（回測與監控須共用同一判定），
  且把指標計算散進引擎，違背 spec 004 建立「正典組裝入口」的初衷。

---

## D2：預設關閉時不輸出 `bos_volume_ok` 欄，引擎對缺欄以 True 回填

**Decision**: `build_indicator_frame(..., use_bos_volume=False)` 時不產生該欄；
`backtester.py` 在取用前比照 `regime_ok` 的既有處理補 `True`。

**Rationale**: 本 repo 已有完全同形的先例：

```python
# ladder_system.py:521-526
if include_regime:
    out['regime_ok'] = calculate_regime_filter(...)
# backtester.py:211-214
if 'regime_ok' not in temp_df.columns:
    temp_df['regime_ok'] = True
```

沿用同一模式有兩個具體好處：(a) spec 004 的 parity 欄位集
（`tests/test_acceptance_parity.py:30` 的 `PARITY_COLUMNS`）在預設狀態下逐字不變，
FR-002 的「位元不變」連欄位集層面都成立；(b) 讀 code 的人不需要學新慣例。

**Alternatives considered**:
- **恆定輸出該欄（關閉時填 True）**：否決。會改變預設狀態的欄位集，
  且讓「濾網關閉」與「濾網啟用但全數通過」在資料上無法區分，消融時難以歸因。
- **輸出原始比值（`volume / vol_ma`）而非布林**：否決。門檻比較會被迫在
  消費端重複實作（引擎與 monitor 各一份），正是 FR-010 要避免的漂移來源。
  比值若有分析價值，屬後續 UI 議題。

---

## D3：`check_entry_signal` 新增 `volume_ok: bool = True`，與 `disabled_filters` 並用

**Decision**: 新增關鍵字參數 `volume_ok: bool = True`；消融鍵 `'bos_volume'`
沿用既有 `disabled_filters` 機制。判定式為
`volume_conf_ok = volume_ok or ('bos_volume' in disabled_filters)`。

**Rationale**: 兩者承載的是不同的東西——`disabled_filters` 表達「這道濾網視為通過」
（消融用），`volume_ok` 傳遞「這根 K 線的量能實際上通不通過」。用 `disabled_filters`
單獨承載會無法表達後者。

預設 `True` 的價值是相容性：現有 14 個呼叫點（`portfolio_backtester.py:385,390`、
`validate_ladder.py:135`、`tests/` 六個檔案）全部零改動，且語意正確——
它們都不套用此濾網。

**FR-005（不疊加於反轉分支）由呼叫點自然成立**：`backtester.py` 只在
BOS 分支（多方 `:265`、空方 `:291`）傳入真值，MSS 分支（`:274`、`:301`）不傳，
於是恆為 `True`。無需在函式內判斷「這次是哪一種進場」——那會需要傳入進場類型，
增加一個沒有必要的參數。

**Alternatives considered**:
- **新增 `entry_kind: str` 參數，函式內分流**：否決。把呼叫點已知的資訊
  轉成字串再在函式內 if 回來，是純粹的複雜度淨增。
- **拆出 `check_bos_entry_signal()` 獨立函式**：否決。會複製四道既有確認的
  邏輯，兩份實作必然漂移；且 spec 003 的多空鏡像真值表測試需要跟著複製。

---

## D4：回看期為獨立參數、預設 20，不沿用 `structure_period`

**Decision**: 新增 `bos_volume_period: int = 20`（Pydantic 預設），並在
`config/config.yaml` 顯式寫出，不與 `structure_period` 綁定。

**Rationale**: spec 的 Assumptions 寫「回看期預設對齊結構訊號的滾動窗」，
但這句話在本 repo 對應**兩個不同的值**：

| 位置 | 值 |
|---|---|
| `detect_market_structure` 函式宣告預設 | 20 |
| `backtester.py:194` 實際傳入 | 10 |
| `monitor_signals.py:180` 實際傳入 | 10 |
| `portfolio_backtester.py:99` 實際傳入 | 10 |

且 `structure_period` **不是 config 參數**——它硬編碼於三處呼叫端，本身即為
憲章原則 V 的既有缺陷。隱式綁定會把該缺陷擴散到新參數，並使
「調整量能回看期」這個消融維度無法單獨掃描（改它會連帶改變結構訊號）。

故取獨立參數，預設 20（函式契約上的宣告值），並在 `config.yaml` 顯式寫出使其可見。
**若要與 MSS 的 displacement 量能基準完全對齊，把 config 改為 10 即可（一行）**——
本決定不阻擋該選項，只拒絕把它變成隱式耦合。

`structure_period` 本身的參數化屬既有缺陷修正，範圍外（見 D5）。

**Alternatives considered**:
- **沿用傳入的 `structure_period`（＝10）**：否決，理由如上（耦合 + 消融維度不可分離）。
- **預設 10 以貼近呼叫端現值**：部分可取，但會讓 config 的顯式值與函式宣告值不一致，
  且掩蓋上表的既有矛盾。選 20 並記錄矛盾，比選 10 並沉默更誠實。

---

## D5：monitor 端只穿線新參數，不修既有硬編碼

**Decision**: `monitor_signals.check_new_signals` 取 `cfg.strategy.get_params_for_ticker(ticker)`
並穿線**本案的三個新參數**；`structure_period=10`、`use_fvg=True`、`fvg_lookback=3`
的既有硬編碼（`monitor_signals.py:179-181`）保持不動。

**Rationale**: FR-002 要求預設狀態行為位元不變。若順手把 `use_fvg` 改為讀 config，
而某標的的 config 值與硬編碼常數不同，monitor 的預設行為立刻改變——基準被污染，
消融比較失去意義。修既有缺陷是好事，但**不能夾在需要位元不變保證的變更裡做**。

已知落差如實記錄於 spec 的 Assumptions：monitor 目前僅消費結構訊號、
未套用動能/趨勢/波動/全域四道確認（`monitor_signals.py:199-221`），
本案僅保證**新濾網**在兩端一致，不修補其餘四道的落差。

**Alternatives considered**:
- **一併把 monitor 的 strategy 參數全面穿線**：否決（污染基準；且範圍膨脹至
  另一個獨立議題）。建議另立 spec 處理「monitor ↔ backtester 進場判定對齊」。
- **monitor 完全不套用新濾網**：否決。會使推播的訊號與回測驗證過的訊號不是同一件事，
  直接違反 FR-010。

---

## D6：未成熟／缺值／零量一律明確判為不通過

**Decision**: 判定式為
`ok = vol_ma.notna() & (vol_ma > 0) & (volume > vol_ma * mult)`，
`fillna(False)` 收尾。

**Rationale**: 本 repo 有一條寫在註解裡的踩坑教訓（`ladder_system.py:645-649`）：

> ATR 未成熟（NaN 或 <=0，見 calculate_atr 暖機期）一律不進場——
> 不得依賴「與 NaN 比較恰好為 False」的隱性行為

同一原則適用於量能。`volume > NaN` 在 pandas 恰好是 False，看似「剛好正確」，
但這是實作巧合而非契約；一旦中間插入 `.fillna()` 或改用 numpy 比較就會翻轉。
`vol_ma > 0` 這一項則處理零量情形（連續停牌或資料異常導致均量為 0 時，
`volume > 0 * mult` 對任何正量都成立——會變成「一律通過」，與意圖相反）。

**Alternatives considered**:
- **依賴 NaN 比較的隱性 False**：否決，理由如上（且違反 repo 既有的明文教訓）。
- **暖機期以 `min_periods=1` 放行**：否決。`calculate_regime_filter` 為了不封死
  整段回測而用 `min_periods=1`（`ladder_system.py:463`），但那是**長均線方向**濾網
  （寧可放行也不要讓 200 日暖機期吞掉整段回測）；量能確認的暖機期只有 20 根，
  放行反而製造一段「濾網名義上開著但實際沒生效」的區間，污染消融歸因。

---

## 未解決項

無。spec 中 0 個 `[NEEDS CLARIFICATION]`；spec Assumptions 唯一的歧義
（回看期預設值）已由 D4 收斂。
