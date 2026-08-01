# Contract: 均線觸價通知

**Modules**: `ma_lines.py`（新增） | **Consumers**: `monitor_signals.py`、`app.py`

原則：新增功能總開關**預設關閉**，關閉時全系統行為與實作前逐字相同。

---

## 1. `compute_ma_set()`（新增純函式）

```python
def compute_ma_set(daily_close: pd.Series,
                   periods: dict[str, int]) -> dict[str, float | None]:
```

### 前置條件

- `daily_close` 為**已收盤**日線收盤價序列，時序遞增（呼叫端負責排除當日
  進行中的 K 線）。
- `periods` 為 `{線別: 週期}`，週期皆 `>= 2`（呼叫端已由 Pydantic 驗證）。

### 後置條件

- 回傳 `{線別: 均線值 或 None}`，鍵集合與 `periods` 相同。
- `len(daily_close) < period` 之線 **MUST** 回傳 `None`。
- 均線為**簡單移動平均**（最後 `period` 根收盤價的算術平均）。
- 純函式：無 I/O、不讀 config、不 import `monitor_signals` / `backtester`。
- 不就地修改輸入。

### 禁止事項

- **禁止** `rolling(window=period, min_periods=1)` 或任何等效的補償——
  那會由不足的資料算出假均線（`ladder_system.py:463` 的 `min_periods=1`
  是為回測暖機期而設，語意相反，**不得沿用**）。
- **禁止**回傳 NaN 代替 `None`（`NaN > x` 恰好為 False 是實作巧合，
  非契約；同 `ladder_system.py:645-649` 的既有教訓）。
- **禁止**在資料不足時回傳「最長可用的均線」——使用者要的是年線，
  給一條 30 日線並標為年線是欺騙。

---

## 2. `detect_cross_below()`（新增純函式）

```python
def detect_cross_below(prev_price: float,
                       curr_price: float,
                       ma_set: dict[str, float | None]) -> list[str]:
```

### 語意

```text
對每條 ma 值不為 None 的線：
    穿越成立 ⟺ prev_price > ma  AND  curr_price <= ma
```

`<=` 對應原始需求的「達到或低於」——觸及即算，不必跌破。

### 後置條件

- 回傳觸發的線別清單（可為空 list）。
- `ma` 為 `None` 的線 **MUST** 被略過，不得因 `None` 比較而拋錯。
- 純函式：同一輸入恆得同一輸出。
- **僅偵測向下穿越**；向上突破不在本案範圍。

---

## 3. `monitor_signals.check_new_signals()`（行為擴充）

### 接線契約（FR-008 的落點，最高風險）

新增邏輯 **MUST** 是現貨分支尾端的**額外一段**：

```text
（既有）現貨分支 → fetch 5 分線 → build_indicator_frame → 六種告警   ← 完全不動
（新增）           → 讀 stock_*_daily → compute_ma_set
                   → detect_cross_below（用既有 prev_bar/latest_bar 的 close）
                   → 逐條推播
```

**MUST NOT** 以下列任一方式實作：

| 反模式 | 後果 |
|---|---|
| 把 `monitor_signals.py:167` 的 5 分線 fetch 改成日線 | 既有六種告警全部從 5 分線改判為日線，行為徹底改變 |
| 讓均線判定共用 `build_indicator_frame` 的輸出 | 該輸出來自 5 分線，算不出月線以上的任何一條 |
| 用「前一日日線收盤」作為穿越前值 | 開盤跳空跌破會漏判 |

### 後置條件

- **總開關關閉（預設）** → 行為與本案實作前**逐字相同**：不讀日線表、
  不發任何均線通知、既有六種告警的判定與訊息內容不變。
- 比較價取自既有 `select_closed_bar_indices()` 的 `prev_bar` / `latest_bar`
  （`monitor_signals.py:94-106`），不使用進行中的棒。
- 去重呼叫 `is_alert_already_sent(ticker, bar_time, alert_type)`，
  其中 `bar_time` 填**交易日**、`alert_type` 為線別
  （命名見 [data-model.md](../data-model.md) §3）。
  **此粒度與既有六種告警不同（那些填 K 線時間戳），須在程式碼註解寫明理由，
  避免被後人「統一」掉。**
- 期貨標的 **MUST** 完全不進入本段邏輯（FR-010）。
- 日線表不存在或為空 **MUST** 跳過該標的的均線判定並輸出可辨識提示，
  **MUST NOT** 拋錯中斷其他標的的監控（FR-012）。

### 訊息內容（FR-009）

須含：標的、線別（月／季／半年／年）、均線值、當前價、乖離幅度、時間。
盤中時框的既有註記（`intraday_note`）仍適用。

---

## 4. `app.py`（現況面板，US4／FR-013）

### 契約

- 於**單一標的檢視**（非 PORTFOLIO 模式）新增一張均線現況表，
  每條線一列，欄位見 [data-model.md](../data-model.md) §4。
- 資料不足的線 **MUST** 顯示「資料不足」，**MUST NOT** 顯示空白或 0。
- **MUST NOT** 觸發任何推播（FR-014）——儀表板只讀不發。
- 沿用該檢視既有的日線載入（`app.py:404-414` 的 `table_name_for(..., "daily")`），
  不新增資料來源。
- 演算法一律呼叫 `ma_lines` 的純函式，**不得**在 UI 層內嵌計算
  （CLAUDE.md：UI 僅負責呈現）。

---

## 5. `config` schema（新增 `alerts` 區塊）

```python
class MaLineConfig(BaseModel):
    enabled: bool = True
    period: int = Field(..., ge=2)

class MaAlertConfig(BaseModel):
    ma_alerts_enabled: bool = False          # 總開關，預設關閉
    monthly:     MaLineConfig = MaLineConfig(period=20)
    quarterly:   MaLineConfig = MaLineConfig(period=60)
    half_yearly: MaLineConfig = MaLineConfig(period=120)
    yearly:      MaLineConfig = MaLineConfig(period=240)

# SystemConfig 新增：
alerts: MaAlertConfig = Field(default_factory=MaAlertConfig)
```

### 契約

- 與既有 `data` / `strategy` / `trading_cost` / `portfolio` 並列
  （`config/config.py:291-300`），**不放進 `SingleStrategyParams`**——
  那是會進回測與 optimizer 的策略參數，本案是通知偏好（research.md D3）。
- 總開關關閉時，四條線的個別設定不生效。
- 週期 `>= 2`（比照既有 `adx_period` / `ma_period` 的驗證慣例）。
