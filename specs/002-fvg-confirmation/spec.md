# Feature Specification: MSS 之 FVG（公平價值缺口）確認

**Feature Branch**: `002-fvg-confirmation`

**Created**: 2026-07-07

**Status**: Implemented（2026-07-12；訊號層達成 SC-001，回測 P&L 零影響見 baseline-pre-fvg.md 的 mss⊆bos 發現）

**Input**: 原 OpenSpec §4.1 要求「MSS 必須伴隨公平價值缺口（FVG）形成」，但程式碼從未實作。
本規格將該條款自基準（spec 001）抽出，作為獨立功能交付。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 降低 MSS 假訊號（Priority: P1）

量化使用者希望 MSS（結構破壞）訊號只在有機構資金位移證據（FVG）時觸發，
減少盤整區的假反轉訊號，提高偏見移位（bias shift）的可信度。

**Why this priority**: MSS 直接驅動反轉預警與反手進場；假訊號成本高。

**Independent Test**: 以歷史數據對照「含 FVG 確認」與「不含」的 MSS 訊號數量與後續報酬，
可透過消融測試框架（`run_ablation.py`）獨立驗證。

**Acceptance Scenarios**:

1. **Given** 價格跌破前波段低點但三根 K 線間無向下 FVG，**When** 偵測 MSS，**Then** 不觸發看跌 MSS。
2. **Given** 價格以位移跌破前波段低點且伴隨向下 FVG（K1 低點 > K3 高點），**When** 偵測 MSS，**Then** 觸發看跌 MSS。
3. **Given** 同一數據集，**When** 於消融模式停用 FVG 濾網，**Then** 行為與 spec 001 基準完全一致。

### Edge Cases

- 數據序列前兩根 K 線（不足三根組成 FVG 結構）：不得產生 FVG 訊號。
- 跳空開盤造成的巨大缺口：屬有效 FVG，但須確認缺口方向與 MSS 方向一致。
- 缺口在後續 K 線被完全回補：本規格不要求追蹤回補狀態（保持簡單，YAGNI）。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系統 MUST 以三根 K 線定義 FVG：向上 FVG 為 `low(K3) > high(K1)`；向下 FVG 為 `high(K3) < low(K1)`。
- **FR-002**: 看跌 MSS MUST 同時滿足：跌破前 N 期波段低點、位移動能（振幅 > 1.2×ATR）、近 M 根 K 線內存在向下 FVG（M 為可配置參數，預設 3）。看漲 MSS 對稱。
- **FR-003**: FVG 偵測 MUST 向量化實作，且所有參與判斷的 K 線加 `.shift()` 防禦看前偏誤。
- **FR-004**: FVG 確認 MUST 可透過 `disabled_filters` 消融機制停用，以評估其對期望值的真實貢獻。
- **FR-005**: 新增參數 MUST 進入 `config/config.yaml` 與 Pydantic schema（如 `fvg_lookback`）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 含 FVG 確認後，MSS 訊號數量下降且不為零（在既有五檔標的歷史數據上驗證）。
- **SC-002**: 消融測試報告顯示 FVG 濾網對每筆交易期望值的影響（正負皆可，須有數字）。
- **SC-003**: 看前偏誤測試（tests/test_lookahead_bias.py）新增 FVG 案例並通過。

## Assumptions

- FVG 僅作為 MSS 的確認條件，不作為獨立進場訊號。
- 基於既有 `detect_market_structure()` 擴充，不重寫市場結構模組。
