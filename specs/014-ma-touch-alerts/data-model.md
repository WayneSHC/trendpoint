# Phase 1 Data Model: 均線觸價通知

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-07-30

本案不觸碰任何持久化 schema——不新增資料表、不改 `sent_alerts` 主鍵、
不改任何行情表結構。以下為**純記憶體實體**與**組態**兩類。

## 1. 均線組（MA set）

| 屬性 | 值 |
|---|---|
| 產生位置 | `ma_lines.compute_ma_set()` |
| 輸入 | 日線收盤價序列（來自 `stock_*_daily`） |
| 輸出 | `dict[線別 → float | None]` |
| 消費者 | `monitor_signals.py`（穿越判定）、`app.py`（現況表） |

### 四條線的定義

| 線別 | 鍵 | 預設週期（交易日） | 備註 |
|---|---|---|---|
| 月線 | `monthly` | 20 | 部分券商用 21 |
| 季線 | `quarterly` | 60 | 部分券商用 62 |
| 半年線 | `half_yearly` | 120 | 部分券商用 124 |
| 年線 | `yearly` | 240 | 部分券商用 248／250 |

週期值由設定檔決定（FR-001），上表為預設。均線類型為 **SMA**——台股慣例的
月／季／年線即為簡單移動平均；`ladder_system.calculate_ema` 存在但語意不符。

### 計算定義

```text
ma(線別) = mean(已收盤日線收盤價[-period:])
```

**必須僅使用已收盤日線**（FR-002）。若 DB 含當日進行中的日線（收盤後執行的
情境），計算前須排除。

### 資料不足的處理（FR-006，最容易誤實作處）

```text
len(已收盤日線) < period  →  該線回傳 None
```

**禁止** `rolling(window=period, min_periods=1)`。本 repo 有一個語意相反的
既有寫法會誤導實作者：`ladder_system.py:463` 的 `calculate_regime_filter`
刻意用 `min_periods=1`，那是為了避免 200 日暖機期封死**整段回測**；
用在**通知**上會由 30 根日線算出一條假年線推播給使用者。

**回傳 `None` 而非 NaN**：`NaN > x` 恰好為 False 看似正確，但那是實作巧合
（同 `ladder_system.py:645-649` 的 `atr_ready` 教訓）。`None` 迫使呼叫端顯式處理。

### 單線獨立性

某條線資料不足 **MUST NOT** 影響其他線（FR-006）。例如 150 根日線時：
月線、季線、半年線有值，年線為 `None`。

## 2. 穿越事件（cross-below event）

| 屬性 | 值 |
|---|---|
| 產生位置 | `ma_lines.detect_cross_below()` |
| 輸入 | 前一比較價、當前比較價、均線組 |
| 輸出 | 觸發的線別清單（可為空） |

### 判定式

```text
對每條 ma 值不為 None 的線：
    穿越成立 ⟺ prev_price > ma  AND  curr_price <= ma
```

`<=` 對應原始需求的「達到或低於」——**觸及**均線即算，不必跌破。

### 比較價的來源（FR-003）

`prev_price` / `curr_price` 取自既有 5 分線序列的
`select_closed_bar_indices()` 產物（`monitor_signals.py:94-106` 的
`prev_bar['close']` / `latest_bar['close']`）——與既有三關價判定同源
（`:230`），確保跳空跌破被正確捕捉。

**與三關價的一個差異**：三關價的上下關價逐根不同，故前後根各比各的值；
本案的 `ma` 在同一交易日內為常數，前後根比的是**同一條線**。

### 為什麼是事件而非狀態

去重鍵含 `bar_time`（`monitor_signals.py:44-50`），狀態式判定
（`curr_price <= ma` 即觸發）會在價格持續低於均線期間**每根發一次**。
使用者已於 2026-07-30 確認採事件語意；其盲點（開啟功能時已在線下的標的
永不觸發）由 `app.py` 的現況表補上（US4／FR-013）。

### 方向

**僅偵測向下穿越**。向上突破（站回均線）是對稱但獨立的需求，未要求即不做
（避免推播量倍增）。需要時可沿用同一機制擴充。

## 3. 去重鍵（沿用既有 `sent_alerts`）

既有主鍵：`(ticker, bar_time, alert_type)`（`monitor_signals.py:44-50`）。

| 欄位 | 本案填入 | 語意 |
|---|---|---|
| `ticker` | 標的代號 | 同既有 |
| `bar_time` | **交易日**（`latest_time.date()`） | 每交易日至多一則 |
| `alert_type` | 線別（如 `MA_CROSS_BELOW_YEARLY`） | 每條線獨立計數 |

**「每標的每線每交易日至多一則」由既有主鍵天然保證**——零 schema 變更、
零額外狀態。

**與既有六種告警的粒度差異（須在程式碼註解寫明，避免被後人「統一」掉）**：
既有六種填的是 K 線時間戳（5 分線棒），語意為「每根 K 線至多一則」；
本案填交易日，因為均線在同一交易日內是常數——同一天內的多次穿越
指的是同一件事。

### alert_type 命名

| 線別 | `alert_type` |
|---|---|
| 月線 | `MA_CROSS_BELOW_MONTHLY` |
| 季線 | `MA_CROSS_BELOW_QUARTERLY` |
| 半年線 | `MA_CROSS_BELOW_HALF_YEARLY` |
| 年線 | `MA_CROSS_BELOW_YEARLY` |

與既有六種（`BULLISH_MSS` / `BEARISH_MSS` / `BULLISH_BOS` / `BEARISH_BOS` /
`BREAK_UPPER_BAND` / `BREAK_LOWER_BAND`）無命名衝突。

## 4. 現況項（status row，US4／FR-013）

`app.py` 單一標的檢視的表格列，每條線一列：

| 欄 | 內容 | 資料不足時 |
|---|---|---|
| 線別 | 月／季／半年／年線 | 同左 |
| 均線值 | `ma` | **「資料不足」** |
| 目前價 | 最新已收盤價 | 同左 |
| 位置 | 在上／在下 | **「資料不足」** |
| 乖離 | `(price - ma) / ma` | **「資料不足」** |

**資料不足 MUST 顯示為「資料不足」，不得顯示空白或 0**（FR-013）——
與 FR-006 同一原則：不得以看似正常的數值誤導。

**MUST NOT 觸發任何推播**（FR-014）：儀表板只讀不發。

## 5. 組態（新增 `alerts` 區塊）

`SystemConfig` 新增 `alerts` 欄位，與既有 `data` / `strategy` /
`trading_cost` / `portfolio` 並列（`config/config.py:291-300`）。

**不放進 `SingleStrategyParams`**：那承載的是會進回測、會被 optimizer 掃描、
會影響訊號的**策略參數**；本案是**通知偏好**（見 research.md D3）。

| 參數 | 型別 | 預設 | 值域 | 說明 |
|---|---|---|---|---|
| `ma_alerts_enabled` | `bool` | `False` | — | 總開關，**預設關閉** |
| `monthly.enabled` | `bool` | `True` | — | 月線開關（總開關開啟時才生效） |
| `monthly.period` | `int` | `20` | `>= 2` | 月線週期 |
| `quarterly.enabled` / `.period` | `bool` / `int` | `True` / `60` | `>= 2` | 季線 |
| `half_yearly.enabled` / `.period` | `bool` / `int` | `True` / `120` | `>= 2` | 半年線 |
| `yearly.enabled` / `.period` | `bool` / `int` | `True` / `240` | `>= 2` | 年線 |

### 開關的兩層語意

- **總開關關閉**（預設）→ 完全不發、不讀日線表、行為與實作前逐字相同。
- **總開關開啟、單線關閉** → 該線不判定不發，其餘線正常。

### 為何總開關預設關閉

新增的通知類型不應在使用者未要求時自行啟用——尤其本案會改變推播行為
（多出訊息）。FR-007 的要求。

## 6. 實體關係

```text
config.alerts（開關 + 週期）
        ↓
stock_*_daily（DB，10 年日線）──→ compute_ma_set() ──→ 均線組
                                                          ↓
5 分線序列（既有路徑）──→ prev/latest 已收盤棒 ──→ detect_cross_below()
                                                          ↓
                                              穿越事件清單 ──→ 推播（去重鍵含交易日）
                                                          
均線組 ──────────────────────────────────────────→ app.py 現況表（不發推播）
```

**兩條資料路徑在此圖中平行進入**——這是 FR-008 的視覺化：
5 分線路徑（既有）與日線路徑（新增）並存，前者繼續服務既有六種告警。
