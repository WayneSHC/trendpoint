# Feature Specification: 驗收標準自動化測試套件（Acceptance Criteria as Tests）

**Feature Branch**: `004-acceptance-tests`

**Created**: 2026-07-07

**Status**: Draft

**Input**: 原 OpenSpec §6 定義了可量測的驗收標準（100ms 延遲、回測即時零誤差、
插補容錯、離群值過濾），但無任何對應的自動化測試。依憲法原則 III
（規格驗收標準必須映射至測試），本規格將其全數落為 pytest 測試。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 回測 ↔ 即時一致性（Parity）（Priority: P1）

維護者能透過一個測試證明：對同一段歷史數據，「一次性全量計算」的階梯線／指標，
與「逐根 K 線增量餵入」計算的結果完全相同（誤差 = 0）。

**Why this priority**: 這是使用者信任的根基——回測績效若與實盤計算不一致，一切數字皆虛。

**Independent Test**: `pytest tests/test_acceptance_parity.py` 獨立可跑，不依賴網路。

**Acceptance Scenarios**:

1. **Given** 固定的歷史 K 線集，**When** 全量計算與逐根增量計算 Ladder_Level、ATR、三關價、MSS/BOS，**Then** 兩者逐點完全相等（`assert_series_equal`, 零容差）。

---

### User Story 2 - 延遲預算（Latency Budget）（Priority: P2）

維護者能透過測試確認：新一根分鐘級 K 線到達後，核心演算法模組
（指標更新 + 訊號判斷）耗時 < 100ms。

**Why this priority**: OpenSpec 非功能性驗收標準；防止未來變更悄悄劣化效能。

**Independent Test**: `pytest tests/test_acceptance_latency.py -m performance` 可獨立執行。

**Acceptance Scenarios**:

1. **Given** 已載入 10,000 根 K 線的既有狀態，**When** 追加一根新 K 線並重算訊號，**Then** 中位數耗時 < 100ms（取多次量測中位數以抗 CI 抖動）。

---

### User Story 3 - 資料容錯（Gap & Outlier）（Priority: P2）

維護者能透過測試確認：缺漏 K 線經向前填補後時序正確、極端離群值被過濾並產生警告。

**Why this priority**: OpenSpec 系統防呆條款；爬蟲數據品質不可控，容錯是常態需求。

**Independent Test**: `pytest tests/test_acceptance_data_quality.py` 獨立可跑。

**Acceptance Scenarios**:

1. **Given** 中段缺漏 3 根 K 線的序列，**When** 執行清洗管線，**Then** 缺漏被向前填補、索引嚴格遞增、後續 ATR 無 NaN 且發出警告紀錄。
2. **Given** 含價格為 0 或 1000 倍離群值的序列，**When** 執行資料契約驗證，**Then** 驗證失敗（或離群列被過濾）且產生系統警告。

### Edge Cases

- CI 環境效能抖動：延遲測試以中位數與寬鬆重試策略設計，並可用 pytest marker 隔離。
- 無 Numba 環境：一致性測試須在有／無 Numba 兩種模式下皆通過（CI matrix 或 fixture 切換）。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 每一條 spec 001 的 SC-003 ~ SC-005 MUST 對應至少一個 pytest 測試檔。
- **FR-002**: Parity 測試 MUST 覆蓋 Ladder_Level、ATR、三關價、MSS/BOS 與吊燈止損線。
- **FR-003**: 延遲測試 MUST 標記 `@pytest.mark.performance`，預設納入 CI，容許以 marker 排除。
- **FR-004**: 資料品質測試 MUST 直接呼叫 `clean_kline_dataframe` 與 `validate_data_contract` 之公開介面。
- **FR-005**: 所有測試 MUST 離線可跑（使用 `data/` 內既有 CSV 或合成數據，不呼叫 yfinance）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: CI 中 `pytest` 全綠，且新測試合計覆蓋 OpenSpec §6 全部四項可自動化驗收標準。
- **SC-002**: 任何造成 parity 誤差 > 0 的變更會使 CI 變紅（以人工注入 off-by-one 驗證測試有效性）。

## Assumptions

- 沿用既有 `conftest.py` 與 pytest 佈局；不引入新測試框架。
- 100ms 預算以單機 CPU（GitHub Actions 標準 runner）為量測基準。
