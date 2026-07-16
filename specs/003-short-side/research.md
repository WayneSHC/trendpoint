# Research: 台指期做空（Short Side, Futures-Only）

**Phase 0 產出** | 零 NEEDS CLARIFICATION。D1–D3 於 brainstorming 定案、D4 為
approved 架構、D5 於 clarify 定案、D6–D7 為計畫期決策。

## D1：進場範圍 — 完整鏡像

- **Decision**: 空頭 BOS 續勢 + 看跌 MSS 反轉（007 短腿解封）兩條路徑皆做。
- **Rationale**: 多空對稱完整、007 BLOCKED-003 一併解除；鏡像測試（D5）可襲兩邊。
- **Alternatives considered**: 僅 BOS 續勢（留尾巴）；僅 MSS 反轉（覆蓋有限）。

## D2：推播 — 能力完備、待真源生效

- **Decision**: monitor_signals 迭代期貨 instrument + mock 源訊息標示；TAIFEX 真源不入範圍。
- **Rationale**: 使用者選含推播；期貨僅 mock 源（008a 延後真 adapter），mock 訊號
  不可當真訊號推——標示 dry-run 是唯一誠實作法。查證發現空頭 MSS/BOS 訊息文案
  **已存在**（monitor_signals.py:140-156），工作量僅剩 instrument 迭代與標示。
- **Alternatives considered**: 推播不入範圍（使用者否決）；把真資料源拉進來（大幅超scope）。

## D3：開關語意 — 資產類別硬邊界 + 旗標預設關

- **Decision**: equity 結構上不存在空方路徑（引擎閘門 `enable_short AND is_futures`）；
  期貨經 `enable_short` 啟用、預設 false。
- **Rationale**: 現貨做空之借券/損耗建模已於 2026-07-11 決策排除；預設 false 使
  008b 期貨 long-only 基準零回歸（由構造保證，非靠測試逮）。
- **Alternatives considered**: 預設開（基準直接變動，回歸對照麻煩）。

## D4：架構 — 方法 A（in-place 方向分支）

- **Decision**: `check_entry_signal` 增 direction 參數、`manage_position` 補 -1 分支、
  引擎方向因子；無新模組、無平行類。
- **Rationale**: `PositionManager.direction` 本為此預留；007 進場分流結構現成；
  008b 元件天然對稱零改動。對稱性由 D5 鏡像測試釘住。
- **Alternatives considered**: 鏡像變換法（把空方跑成翻轉後的多方——量能/OHLC
  翻轉語意坑多，僅用於**測試**不用於實作）；ShortPositionManager 平行類（複製漂移）。

## D5：SC-002 — 雙法驗證（clarify）

- **Decision**: 數值鏡像變換為主（p'=2C−p、high↔low 對調、量能不變→多空訊號/交易
  一一對應）+ 關鍵行為手工情境對（1 口 floor、爆倉方向、吊燈只降不升、止損穿越）。
- **Rationale**: 變換測試全鏈覆蓋、抓未知不對稱；手工對補強變換照不到的離散行為。
- **Alternatives considered**: 僅變換（離散行為邊角弱）；僅手工（漏掉的不對稱拓不到）。

## D6：`enable_short` 放置與硬邊界檢查點（計畫期決策）

- **Decision**: `SingleStrategyParams.enable_short: bool = False`——經
  `ticker_overrides` 自然獲得 per-instrument 粒度（沿用既有機制，憲章 V）。
  空方反轉進場受 **enable_short AND mss_reversal_entry** 聯合控制（各管一軸：
  空方能力 × 反轉路徑；與多方對稱）。硬邊界雙層：
  (1) config 驗證——對**現貨 ticker** 的 override 明設 `enable_short: true` →
  載入 fail-fast（SC-004）；`default.enable_short=true` 不算「對現貨啟用」
  （旗標語意=「期貨可做空」，對現貨無效）；
  (2) 引擎閘門——空方進場分支 gated on `is_futures`，任何組態下現貨零空單。
- **Rationale**: 用現有 params+overrides 機制最集中；雙層防護使明確錯誤早爆、
  隱含情況仍安全。
- **Alternatives considered**: 全域獨立旗標（失去 per-instrument 粒度）；
  instrument 欄位（做空是策略行為非資料屬性，放 Instrument 語意錯位）。

## D7：引擎會計與交易動作命名（計畫期決策）

- **Decision**: 方向因子 `d = pm.direction`（±1）貫穿：
  已實現損益 = `d × units × (exit − entry) × point_value`；未實現同式；
  爆倉檢查沿 008b（權益 ≤ 0 當根強制結清）——空方由上漲觸發，機制不變。
  交易動作：空方進場 `SELL_SHORT`、部分回補 `COVER_HALF`、全回補 `COVER_ALL`
  （與多方 BUY/SELL_HALF/SELL_ALL 平行）；`_calculate_metrics` 配對擴充空方
  （SELL_SHORT→COVER_ALL，profit 以方向因子計），多方配對路徑**逐字不動**（parity）。
  `manage_position` 簽名：新增可選參數 `chandelier_short: float = None`
  （既有呼叫零改動）；-1 分支鏡像：止損上穿、目標 = entry − 1.5×ATR、
  吊燈只降不升（`chandelier_short < stop_loss` 時下移）。
- **Rationale**: 動作命名區分多空使日誌可讀、配對明確；可選參數保 back-compat；
  方向因子是最小侵入的統一式（d=+1 時退化為 008b 現式）。
- **Alternatives considered**: 復用 BUY/SELL 動作 + direction 欄位（配對邏輯要
  猜方向，易錯）；manage_position 改收單一 `chandelier`（呼叫端語意混淆，且破壞
  既有測試關鍵字呼叫）。
