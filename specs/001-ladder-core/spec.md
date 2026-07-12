# Feature Specification: 多空階梯核心系統（Ladder Core, As-Built Baseline）

**Feature Branch**: `001-ladder-core`

**Created**: 2026-07-07

**Status**: Adopted — as-built baseline（依實際程式碼修訂後的基準規格）

**Input**: 由 `TrendPoint_OpenSpec.md` 遷移，並依 2026-07-07 規格 ↔ 程式碼落差審查修訂。
原 OpenSpec 保留於版本庫根目錄作為歷史文件；本文件為現行唯一有效基準。

> **修訂摘要（相對原 OpenSpec）**
>
> | 原 OpenSpec 條款 | 本基準的處理 | 後續規格 |
> | :--- | :--- | :--- |
> | MSS 必須伴隨 FVG（公平價值缺口） | 移出基準 — 現行程式未實作 | [002-fvg-confirmation](../002-fvg-confirmation/spec.md) |
> | 空頭階梯 / 做空邏輯 | **已定案 Long-Only（2026-07-11）**，003 已關閉；重啟條件見該規格決策記錄 | [003-short-side](../003-short-side/spec.md) |
> | 突破上/下關價縮小 k 值激進追價 | 移出基準 — 現行未實作 | [005-extreme-regime](../005-extreme-regime/spec.md) |
> | 收盤前 15 分鐘強制平倉、量價背離減倉 25%、階段 2 時間止盈 | 移出基準 — 現行僅有階段 1 的 bar-count 時間止盈 | [006-exit-system-completion](../006-exit-system-completion/spec.md) |
> | §6 驗收標準（延遲 100ms、回測即時一致性、插補、離群值） | 保留為基準需求，但目前無自動化測試 | [004-acceptance-tests](../004-acceptance-tests/spec.md) |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 趨勢方向判讀（Priority: P1）

一般投資人打開 App，能在 10 秒內說出目前對特定標的（如 0050.TW）的交易偏見：
看多（Bullish）、看空（Bearish）或觀望（Sideways），並看到判斷依據
（階梯位置、三關價相對位置、MSS/BOS 狀態）。

**Why this priority**: 這是 PRD 定義的核心問題（缺乏方向感）；沒有它產品不成立。

**Independent Test**: 載入任一標的歷史數據，介面須顯示明確的方向標籤與依據數值。

**Acceptance Scenarios**:

1. **Given** 價格高於中關價且 BOS 持續，**When** 使用者開啟趨勢畫面，**Then** 顯示「看多」與對應階梯支撐價。
2. **Given** 出現看跌 MSS 訊號，**When** 使用者開啟趨勢畫面，**Then** 顯示反轉預警而非單純「看多」。

---

### User Story 2 - 動態區間提示（Priority: P1）

使用者能看到當日三關價（上關、中關、下關）與最佳化後的階梯價格線，
作為具體的進出場與止損參考，而非模糊的預測描述。

**Why this priority**: PRD 驗收標準「範圍界定明確」的直接對應。

**Independent Test**: 對任一交易日，介面顯示的三關價須等於以昨日高低價套用公式的手算結果。

**Acceptance Scenarios**:

1. **Given** 昨日最高 H、最低 L，**When** 計算三關價，**Then** 中關 = (H+L)/2、上關 = L+(H−L)×1.382、下關 = H−(H−L)×1.382。
2. **Given** 任一根 K 線，**When** 檢視階梯線，**Then** 階梯價 = 前一階梯 ± k×ATR 的階梯狀移動（僅隨 BOS 方向移動，不回頭）。

---

### User Story 3 - 回測驗證（Priority: P2)

量化使用者能以歷史數據執行含摩擦成本的回測，檢視權益曲線、交易明細與
績效指標（勝率、獲利因子、最大回撤、Sharpe），驗證策略在不同波動環境的表現。

**Why this priority**: 建立信任的必要條件，但可在方向判讀與區間提示之後交付。

**Independent Test**: 對固定的歷史區間執行回測兩次，輸出結果必須完全一致（決定性）。

**Acceptance Scenarios**:

1. **Given** 同一組數據與參數，**When** 重複執行回測，**Then** 每筆交易與每個權益點完全相同。
2. **Given** 任一筆回測交易，**When** 檢視成交價，**Then** 已含 config 定義的手續費、稅與滑價。

### Edge Cases

- 數據缺漏（爬蟲漏抓 K 線）：向前填補（forward fill），並記錄警告；後續指標時序不得錯亂。
- 極端離群值（價格歸零、暴漲千倍）：資料契約驗證須拒絕或過濾，並發出系統警告。
- 成交量為 0 的 K 線：VWAP 計算不得除以零（現行以 NaN → ffill 處理）。
- 未安裝 Numba 的環境：所有 `@jit` 函式自動降級為純 Python，結果須一致。
- 數據長度不足 ATR 週期：回傳全零序列，不得拋出未處理例外。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系統 MUST 以 Wilder 平滑法計算 ATR：`ATR_t = ((n−1)×ATR_{t−1} + TR_t) / n`，初始值採前 n 期 TR 的 SMA。
- **FR-002**: 系統 MUST 以昨日高低價計算三關價（公式見 US2），作為全域多空濾網：價格高於中關價僅執行多頭邏輯。
- **FR-003**: 系統 MUST 向量化偵測 BOS（突破前 N 期波段高點 + 量能確認）與 MSS（跌破前 N 期波段低點 + 位移動能），所有波段點計算加 `.shift(1)`。
- **FR-004**: 進場 MUST 通過四維度確認：結構（MSS/BOS）、動能（收陽線）、趨勢（價格 > 當日開盤且 > VWAP；日線級別免除 VWAP）、波動（單根振幅 > 1.2×ATR）。
- **FR-005**: 部位管理 MUST 實作三階段止盈：獲利 1.5×ATR 平倉 50% → 止損移至保本 → 剩餘部位以吊燈式止損（Rolling Max(High, n) − m×ATR）跟蹤，吊燈線只升不降。
- **FR-006**: 階段 1 持倉 MUST 於 `time_limit` 根 K 線內未觸發止盈時強制平倉（bar-count 時間止盈）。
- **FR-007**: 所有策略參數 MUST 由 `config/config.yaml` 載入並經 Pydantic 驗證，支援 per-ticker 覆寫。
- **FR-008**: 回測 MUST 計入手續費、證交稅與滑價；訊號 K 線與成交 K 線之間 MUST 無看前偏誤。
- **FR-009**: 資料擷取 MUST 通過資料契約驗證（欄位齊全、時間遞增、無負價）後方可入庫（SQLite + CSV）。
- **FR-010**: 排程監控 MUST 對已發送的（ticker, bar_time, alert_type）去重，避免重複推播。

### Key Entities

- **K 線（Bar）**: datetime, open, high, low, close, volume — 系統唯一原始輸入。
- **衍生指標（Indicators）**: TR, ATR, VWAP, 三關價（upper/middle/lower）, Ladder_Level, Chandelier_Exit, MSS_Signal, BOS_Signal。
- **部位（Position）**: 方向（現行僅多頭）、進場價、規模、止損價、階段（0/1/2）。
- **交易（Trade）**: 進出場時間與價格、損益、離場原因（止損／時間／吊燈）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 首位使用者使用後能清楚說出當前交易方向（看多/看空/觀望）。`[MANUAL]`
- **SC-002**: 介面顯示具體高低參考價（三關價、階梯價數值），無模糊預測語句。`[MANUAL]`
- **SC-003**: 歷史回測計算之階梯線與即時計算之階梯線完全吻合，誤差為 0。（自動化 → spec 004）
- **SC-004**: 新一根分鐘級 K 線到達後，演算法模組計算時間 < 100ms。（自動化 → spec 004）
- **SC-005**: 缺漏 K 線經插補後，後續指標時序正確；極端異常值被過濾並產生警告。（自動化 → spec 004）

## Assumptions

- 本基準為 **Long-Only**：僅產生與管理多頭部位。此為正式產品範圍決策
  （2026-07-11，非暫時狀態）；空頭市況的系統行為是「三關價濾網擋下做多、
  維持空手」。重啟做空的條件見 spec 003 決策記錄（需先有台指期支援）。
- MSS 偵測 **不含 FVG 確認**（見 spec 002）。
- 標的為台股 ETF 與個股（yfinance 數據源）；日線為主，5 分鐘線為輔。
- 單一使用者、單機／GitHub Actions 排程運行；無多用戶併發需求。
