# Phase 0 Research: MSS 進場區別化（fractal 反轉校正）

解析設計未定項，逐條給 Decision / Rationale / Alternatives。

## D1 — 對稱碎形 swing 偵測

- **Decision**: bar `i` 為 swing high 若 `high[i] == max(high[i−N : i+N+1])`（嚴格大於兩側更佳，用 `>=` 處理平台時取最右）；swing low 對稱以 `low`。N 為碎形強度（預設 2 = Williams 五根碎形）。
- **Rationale**: 對稱窗口是量化實務中最直接、可向量化（`rolling(2N+1, center=True).max()` 比較）的樞紐定義，貼近理論的「關鍵波段點」。
- **Alternatives**: (a) rolling 極值近似（spec 選項 A，被否決——非真樞紐）；(b) ZigZag（含閾值、狀態多、較難防看前偏誤）。

## D2 — 看前偏誤：確認延遲 N 根（最關鍵）

- **Decision**: 樞紐 `i` 在 bar `i+N` 才「已確認」。以 `is_swing_high.shift(N)` 標記確認時點，再對已確認樞紐的值 forward-fill，得到每個決策 bar `t` 可用的「最近已確認 swing high/low 值」。任何 MSS 判斷只用 close[t]（決策當下）與 `≤ t−N` 的樞紐；`(t−N, t]` 內尚未確認之樞紐一律不可引用。
- **Rationale**: 對稱碎形需右側 N 根才能確立極值；不延遲即引用等於偷看未來，違反憲章 I。`shift(N)` + ffill 為向量化且可測的實作。
- **Alternatives**: 因果型單側碎形（只看左側，無延遲但非真樞紐、雜訊高）——否決，語意偏離理論。
- **測試**: `tests/test_lookahead_bias.py` 構造「未來才成為樞紐」情境，遮蔽 `>t` 資料後 MSS[t] 不變；並驗證 `(t−N,t]` 未確認樞紐不影響 MSS[t]。

## D3 — 結構分類（HH/HL/LH/LL）與趨勢偏向（效能約束）

- **Decision**: 由「已確認」樞紐序列，比較相鄰同型樞紐：swing high 高於前一 swing high = HH，否則 LH；swing low 高於前一 swing low = HL，否則 LL。趨勢偏向：近端呈 HH+HL → 上升；LH+LL → 下降；否則不明（MSS 不觸發）。實作以向量化 diff/比較為主；若分類需序列狀態，改用 numba `@jit` 並附無 numba 降級（憲章 IV）。
- **Rationale**: 趨勢偏向必須源自結構本身（與 fractal 一致），非借用 200MA，避免雙重、可能衝突的偏向來源。FR-002 已規定偏向由已確認樞紐序列判定。
- **Alternatives**: 用 `calculate_regime_filter` 的 200MA 方向當偏向——否決（與結構脫鉤、且長期均線滯後）。

## D4 — MSS 反轉判定（校正 detect_market_structure）

- **Decision**:
  - 看跌 MSS（`-1`）= 上升結構中，`close[t]` 跌破「最近已確認 HL」+ 位移確認。
  - 看漲 MSS（`+1`）= 下降結構中，`close[t]` 突破「最近已確認 LH」+ 位移確認。
  - BOS 維持不變：`bull_bos = close > rolling_high`、`bear_bos = close < rolling_low`（`shift(1)` 後）。
  - 兩者互斥：MSS 依「反向已確認結構點」+ 趨勢偏向界定，BOS 依「同向 rolling 突破」，同一 bar 同向不同時成立。
- **Rationale**: 直接對應理論（`docs/ladder-optimization-research.md:20,28`）；消除 `mss ⊆ bos`。
- **參照點**：「最近已確認 HL」= 當前上升結構中，最後一個被分類為 HL 的已確認 swing low 的價位（同理 LH）。

## D5 — 位移確認（Displacement）

- **Decision**: 沿用量能 proxy：`volume[t] > mss_volume_mult × volMA`（`volMA = volume.rolling(period).mean().shift(1)`，`mss_volume_mult` 預設 1.5）。門檻乘數集中至組態。
- **Rationale**: 與現行 stub 一致、零新資料需求，先證明機制方向性。
- **Alternatives**: ATR 正規化 range（`(high−low)/ATR ≥ x`）更貼近「Displacement」，列為後續升級（spec Out of Scope）。

## D6 — 反轉進場閘門（解決 200MA 順勢濾網衝突）

- **Decision**: MSS 反轉進場**複用既有 `check_entry_signal` 的 `disabled_filters` 消融機制**，以「反轉 profile」進場：停用 `'trend'` 維度（繞過 `close>vwap/daily_open` 順勢確認），並在呼叫端把 `global_filter_ok` 改為**只留三關價**（`close>mid_price`）、去掉 200MA regime；保留 `structure` + `momentum` + `volatility`。BOS 續勢進場維持原本全維度 profile 不變。
- **修訂（2026-07-12，US2 實作）**：初版停用 `{'trend','global'}` 會連三關價一併繞過；因三關價是 spec 003 關閉時強調的空頭防線，改為**保留三關價、只放寬 trend + regime**。實測代表標的回測數字與初版**完全相同**（反轉進場本就在中關價之上），故收緊零成本。
- **Rationale**: 看漲反轉本質發生在 200MA 下方，若套順勢/regime 濾網會被靜默封殺、使 SC-002/SC-003 無法達成。既有 `disabled_filters`（`ladder_system.py:509`）已提供乾淨的維度旁路，複用即可、零新抽象。
- **Alternatives**: (a) 反轉專用的獨立進場函式——多一份邏輯、重複；(b) 反轉時反轉 200MA 濾網方向——語意含糊。均否決。
- **短側注意**: `momentum_ok = close>open_val`、`trend` 維度目前偏多；看跌反轉（做空）需 `check_entry_signal` 的方向泛化——屬 **spec 003**。007 的反轉 profile 對**長側**直接可用。

## D7 — 反轉進場對既有部位：先平再開

- **Decision**: MSS 反轉觸發時：持反向部位 → 先平再開；無部位 → 開新；持同向部位 → 略過。回測交易紀錄不得同時持多空（SC-005）。
- **Rationale**: FR-006；長側情境下「先平再開」通常退化為「無反向部位可平 → 直接開多」，短側部位存在後才會出現真正的多空互換（依賴 003）。

## D8 — 組態新增（SingleStrategyParams + config.yaml）

- **Decision**（新增至 `config/config.py: SingleStrategyParams` 與 `config.yaml: strategy.default`，並允許 `ticker_overrides`）：
  - `swing_fractal_n: int = 2`（碎形強度；同時是確認延遲根數）
  - `mss_reversal_entry: bool = True`（反轉進場開關；設 False 復現 007 前的 BOS-only 進場，供回歸/消融）
  - `mss_ladder_k: float | None = None`（反轉進場 k；`None` 繼承 `ladder_k`）
  - `mss_volume_mult: float = 1.5`（位移量能乘數，取代硬編 1.5）
- **Rationale**: 沿用 spec 002 `use_fvg`/`fvg_lookback` 落點模式；憲章 V。
- **Alternatives**: 硬編碼——違憲，否決。

## D9 — 跨規格依賴 spec 003（短側）

- **Decision**: 007 訊號層完整雙向；長側反轉進場於 007 完成；**短側反轉進場執行任務阻塞於 spec 003**。003 需先補：做空部位管理、`check_entry_signal` 方向泛化（`structure_sig==-1`、動能/趨勢方向翻轉）、Chandelier 空方出場、做空成本（如借券/保證金，若適用）。
- **Rationale**: clarify 決策——重啟做空但獨立由重開的 003 承接，保持 007 聚焦與回測乾淨。
- **排序**: `/speckit-tasks` 時將短側「進場執行」相關任務標 `blocked-on-003`；其餘（訊號、長側進場、看前偏誤測試、前後回測長側對照）可先行。
