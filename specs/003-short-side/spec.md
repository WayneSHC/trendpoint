# Feature Specification: 空頭階梯與做空部位管理（Short Side）

**Feature Branch**: `003-short-side`

**Created**: 2026-07-07

**Status**: Draft — 待決策：實作做空，或正式將產品範圍定為 Long-Only

**Input**: 原 OpenSpec 描述對稱的多空階梯（空頭 BOS 下移階梯、看漲 MSS 反手、
空方吊燈止損），但現行 `PositionManager` 僅處理 `direction == 1`（多頭），
`check_entry_signal` 僅接受看漲訊號。

> **NEEDS CLARIFICATION（優先於實作解決）**：台股現貨 ETF 做空有借券／信用交易限制。
> 選項 A：實作做空並限定於可放空標的（如期貨、00632R 反向 ETF 以做多替代）。
> 選項 B：正式宣告 Long-Only，將本規格關閉並於 spec 001 移除空方描述殘留。
> 在決策前，本規格不得進入 `/speckit-plan`。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 空頭趨勢的對稱訊號（Priority: P1）

使用者在空頭市場（價格低於中關價、空頭 BOS 持續）能獲得做空（或避險）訊號
與空方階梯壓力線，而非只被告知「觀望」。

**Why this priority**: 台股多頭年約佔六成；缺空方邏輯代表約四成市況無訊號覆蓋。

**Independent Test**: 以 2022 年台股空頭段回測，系統須產生空方交易且績效可歸因。

**Acceptance Scenarios**:

1. **Given** 價格低於中關價且觸發空頭 BOS，**When** 四維度確認通過（動能端為收陰線、趨勢端為價格低於當日開盤與 VWAP），**Then** 產生做空進場訊號。
2. **Given** 持有空頭部位，**When** 獲利達 1.5×ATR，**Then** 回補 50% 並將止損移至保本位。
3. **Given** 持有空頭部位階段 2，**When** 價格升破空方吊燈線（Rolling Min(Low, n) + m×ATR，只降不升），**Then** 全數回補離場。

### Edge Cases

- 多空訊號同一根 K 線同時出現：以三關價全域濾網裁決（高於中關只做多、低於中關只做空）。
- 持有多頭部位時出現看跌 MSS：先依既有止損／吊燈邏輯離場，不直接反手（反手為未來範圍）。
- 不可放空之標的：組態層 MUST 可標記 per-ticker `allow_short: false`。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `check_entry_signal` MUST 接受 `structure_sig == -1` 並以方向對稱的四維度確認（動能端：收陰線；趨勢端：價格 < 當日開盤 且 < VWAP）。
- **FR-002**: `manage_position` MUST 實作 `direction == -1` 分支：止損於價格上穿、三階段止盈對稱、吊燈線採 `Rolling Min(Low, n) + m×ATR` 且只降不升。
- **FR-003**: 三關價濾網 MUST 對稱生效：價格低於中關價時僅允許空方邏輯。
- **FR-004**: 回測引擎與監控推播 MUST 支援空方交易之成本計算（含借券費率參數，預設 0 並標註）。
- **FR-005**: 組態 MUST 支援 per-ticker `allow_short` 開關，預設 `false`。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 空頭段回測（如 2022-01 ~ 2022-10 之 0050.TW）產生 ≥ 1 筆空方交易，且回測具決定性。
- **SC-002**: 多空對稱性測試：將價格序列鏡像後，多方訊號與空方訊號一一對應。
- **SC-003**: 既有多方回測結果（spec 001 基準）在 `allow_short: false` 下完全不變（迴歸零差異）。

## Assumptions

- 反手（stop-and-reverse）不在本規格範圍。
- 現行五檔標的中，僅期貨類或反向 ETF 適用實際做空；其餘標的空方訊號僅作為避險／減碼提示。
