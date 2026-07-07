# Feature Specification: 極端趨勢盤之動態 k 值調整（Extreme Regime）

**Feature Branch**: `005-extreme-regime`

**Created**: 2026-07-07

**Status**: Draft

**Input**: 原 OpenSpec §4.3 整合策略矩陣：「突破上關價或跌破下關價時，代表市場進入
極端趨勢盤，此時縮小 ATR 乘數（k），採取激進追價。」現行程式計算三關價
但從未依此調整 k 值。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 極端趨勢盤的階梯貼近（Priority: P1）

當價格突破上關價（極端多頭），使用者希望階梯線以較小間距（縮小 k）貼近價格，
更早鎖定趨勢利潤、更積極跟進，而非沿用常態間距錯過行情。

**Why this priority**: 極端趨勢盤是本策略獲利主要來源；此機制直接影響盈虧比。

**Independent Test**: 消融測試對照「固定 k」與「動態 k」在既有五檔標的上的
期望值與最大回撤差異。

**Acceptance Scenarios**:

1. **Given** 收盤價突破當日上關價，**When** 計算下一根 K 線的階梯間距，**Then** 使用 `k_extreme = k × extreme_ratio`（extreme_ratio 可配置，預設 0.75）。
2. **Given** 價格回落至上關價之下，**When** 計算階梯間距，**Then** 恢復常態 k 值。
3. **Given** 消融模式停用本機制，**When** 回測，**Then** 結果與 spec 001 基準完全一致。

### Edge Cases

- 開盤即跳空越過上關價：以第一根收盤確認後才切換 k，避免單 tick 噪音。
- 上下關價間距極小（昨日十字線）：`k_extreme` 不得使階梯間距低於最小 tick 的合理倍數。
- 極端狀態與 MSS 反轉預警同時出現：MSS 風險控制優先（不因激進追價而放寬止損）。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系統 MUST 以「前一根已收盤 K 線」是否越過上／下關價判定極端狀態（`.shift(1)`，防看前偏誤）。
- **FR-002**: 極端狀態下階梯間距 MUST 改為 `k × extreme_ratio`；`extreme_ratio` 進入 config 與 Pydantic schema，預設 0.75，範圍 (0, 1]。
- **FR-003**: 本機制 MUST 可經 `disabled_filters`／消融框架停用。
- **FR-004**: 回測輸出 MUST 可辨識每筆交易期間是否處於極端狀態（供績效歸因）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 消融報告顯示動態 k 對期望值與回撤的量化影響（正負皆可，須有數字）。
- **SC-002**: `extreme_ratio = 1.0` 時回測結果與基準零差異（迴歸保護）。
- **SC-003**: 看前偏誤測試新增極端狀態切換案例並通過。

## Assumptions

- 「激進追價」僅指縮小階梯間距，不改變四維度進場確認門檻。
- 上下關價沿用日線級別計算，不引入盤中重算。
