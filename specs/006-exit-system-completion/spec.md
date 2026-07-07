# Feature Specification: 止盈離場體系補完（Exit System Completion）

**Feature Branch**: `006-exit-system-completion`

**Created**: 2026-07-07

**Status**: Draft

**Input**: 原 OpenSpec §4.5 的離場體系有三處未實作或與程式碼歧異：
(1) 時間止盈僅存在於階段 1，階段 2 部位無時間出口；
(2) 「當日收盤前 15 分鐘強制平倉」（日內模式）完全未實作；
(3) 「量價背離（BOS 創新高但成交量與 RSI 頂背離）主動減倉 25%」完全未實作（程式中無任何 RSI 計算）。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 日內部位不留倉（Priority: P1）

使用 5 分鐘級數據的日內交易者，其部位在當日收盤前 15 分鐘一律強制平倉，
避免隔夜跳空風險。

**Why this priority**: 隔夜跳空是日內策略最大的尾部風險；OpenSpec 明文要求。

**Independent Test**: 以既有 5 分鐘 CSV 回測，驗證無任何持倉跨越交易日邊界。

**Acceptance Scenarios**:

1. **Given** 日內模式持有部位且時間到達收盤前 15 分鐘（台股 13:15 後之第一根 K 線），**When** 該 K 線收盤，**Then** 全數平倉，離場原因標記為「收盤前強制平倉」。
2. **Given** 日線模式，**When** 回測，**Then** 本規則不生效（僅適用日內級別）。

---

### User Story 2 - 階段 2 的時間出口（Priority: P2）

趨勢跟蹤中的剩餘部位（階段 2）若長期橫盤未創新高，須有時間性退出機制，
避免資金無限期滯留於死水部位。

**Why this priority**: 現行階段 2 僅有吊燈止損；橫盤市可能持倉極久而無停利。

**Acceptance Scenarios**:

1. **Given** 階段 2 部位連續 `stage2_time_limit` 根 K 線未更新滾動最高價，**When** 下一根 K 線收盤，**Then** 全數平倉，離場原因標記為「階段 2 時間出口」。

---

### User Story 3 - 量價背離減倉（Priority: P3）

價格創新高（BOS）但成交量與 RSI 動能出現頂背離時，系統主動減倉 25%，
提前兌現部分趨勢利潤。

**Why this priority**: 錦上添花的優化；先經消融測試證明期望值貢獻再默認啟用。

**Acceptance Scenarios**:

1. **Given** 階段 2 多頭部位、價格突破前波段高點，**When** 當根 RSI(14) 低於前次新高時之 RSI 且成交量低於 20 期均量，**Then** 減倉 25%（以原始部位計），每波段至多觸發一次。

### Edge Cases

- 收盤前 15 分鐘規則與階段 1 止盈同根觸發：強制平倉優先（全平勝過部分平）。
- 減倉 25% 後剩餘部位規模低於最小交易單位：直接全平。
- 半日交易日（台股封關日等）：以數據中該日實際最後 K 線回推 15 分鐘。`[NEEDS CLARIFICATION: 是否需要台股交易日曆，或以資料驅動回推即可]`

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 日內模式 MUST 於當日收盤前 15 分鐘強制全平；日線模式不適用。
- **FR-002**: 階段 2 MUST 具備 `stage2_time_limit`（可配置，預設 0 = 停用）之時間出口。
- **FR-003**: 系統 MUST 新增向量化 RSI(14) 計算（Wilder 平滑，與 ATR 同法），並加 `.shift(1)` 用於決策。
- **FR-004**: 量價背離減倉 MUST 可經消融框架停用，且預設關閉直至 SC-002 達成。
- **FR-005**: 所有新離場路徑 MUST 於交易紀錄輸出明確的離場原因字串（供績效歸因）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 5 分鐘級回測中，跨日持倉筆數 = 0。
- **SC-002**: 消融報告量化背離減倉對期望值的影響；若為負貢獻則保持預設關閉並記錄結論。
- **SC-003**: 既有日線回測在新離場機制全部停用時與基準零差異。

## Assumptions

- 台股收盤時間以數據推斷（每日最後一根 K 線），暫不引入交易所日曆依賴。
- RSI 僅用於離場減倉，不加入進場四維度確認。
