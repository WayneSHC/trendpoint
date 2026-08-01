# Phase 0 Research: 均線觸價通知

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-07-30

五個設計決策。本案的最高風險（誤把既有 5 分線路徑改成日線）在 D1 收斂。

---

## D1：兩條資料路徑並存，新增的是「額外一段」而非替換

**Decision**: 在 `check_new_signals` 的**現貨分支尾端**新增一段獨立的均線判定，
自行從 DB 讀 `stock_*_daily`。`monitor_signals.py:167` 的 5 分線 fetch
與其下游六種告警**完全不動**。

**Rationale**: 5 分線路徑是**刻意設計**——`monitor_signals.py:165` 註解明寫
「下載最新 5 天的 5 分鐘線，以獲取最新即時資料」，且 `alert_scheduler.yml:7`
的 cron 為交易時段每 30 分鐘，該節奏只有配盤中資料才說得通
（`CLAUDE.md` 監控段已記錄此為刻意設計而非缺陷）。

最容易犯的錯是「既然要日線，那就把取數改成日線吧」——那會使既有六種告警
（MSS／BOS 多空、三關價上下關）全部從 5 分線改判為日線，行為徹底改變。
使用者要的是**多一種通知**，不是**換掉現有的通知**。

**Alternatives considered**:
- **把整個監控改讀日線**：否決，理由如上。這是本案的頭號誤實作風險。
- **均線也用 5 分線資料算**：否決。5 天約 270 根 5 分鐘棒，連月線（20 日）
  都算不出來，年線（240 日）更不可能。
- **新增獨立的均線監控腳本**：否決。會產生第二套推播設定、第二套去重、
  第二個排程，且與既有監控在同一時段重複取數。

---

## D2：穿越判定的「前值」取自同一條 5 分線序列

**Decision**: 穿越 = `prev_bar['close'] > ma` 且 `latest_bar['close'] <= ma`，
其中 `latest_bar` / `prev_bar` 即既有的
`select_closed_bar_indices(...)` 產物（`monitor_signals.py:94-106`）。

**Rationale**: 與既有三關價判定同源（`monitor_signals.py:230`）：

```python
if latest_bar['close'] > latest_bar['upper_price'] and prev_bar['close'] <= prev_bar['upper_price']:
```

沿用同一範式有兩個好處：語意一致（同一頻道內所有告警都是穿越事件），
以及**跳空跌破會被正確捕捉**——開盤第一根若已在均線下方，
而前一根（昨日尾盤）在均線上方，條件成立。

若改用「前一日日線收盤」作為前值，跳空跌破當日的判定會失敗：
前一日收盤在均線上方、當日收盤才知道，而盤中根本不會觸發。

**注意兩者的均線值相同**：`ma` 由日線算出、在同一交易日內為常數，
故 `prev_bar` 與 `latest_bar` 比較的是同一條線，不像三關價那樣前後根各有各的值。

**Alternatives considered**:
- **前值取前一日日線收盤**：否決（跳空跌破漏判，理由如上）。
- **前值取「上次判定時的價格」（需持久化）**：否決。需要新增狀態表，
  而 5 分線序列本身已提供前值，零成本。

---

## D3：新參數放 `alerts` 區塊，不放 `SingleStrategyParams`

**Decision**: `config/config.yaml` 新增頂層 `alerts` 區塊，
`SystemConfig` 新增 `alerts: MaAlertConfig` 欄位
（比照既有 `data` / `strategy` / `trading_cost` / `portfolio` 的並列結構，
`config/config.py:291-300`）。

**Rationale**: `SingleStrategyParams`（`config/config.py:74-173`）承載的是
**策略參數**——會進入回測、會被 optimizer 掃描、會影響訊號。
均線通知的週期與開關是**通知偏好**：不進回測、不影響任何訊號、不該被尋優。

混進去會有兩個具體壞處：(a) optimizer 的參數空間會多出四個與績效無關的維度；
(b) `ticker_overrides` 的語意被稀釋——它現在的意思是「這檔用不同的策略參數」，
不該同時是「這檔用不同的通知設定」。

**Alternatives considered**:
- **放 `SingleStrategyParams`**：否決，理由如上。
- **放 `DataConfig`**：否決，語意不符（那是資料來源設定）。
- **不進 config、寫成常數**：否決（違反憲章原則 V）。

---

## D4：資料不足時回傳 `None`，且嚴禁 `min_periods=1`

**Decision**: `compute_ma_set()` 對根數不足的均線回傳 `None`；
呼叫端必須顯式檢查。**禁止**使用 `rolling(window=n, min_periods=1)`。

**Rationale**: 本 repo 有一個**正好相反**的既有寫法會誤導實作者：

```python
# ladder_system.py:463（calculate_regime_filter）
long_ma = df['close'].rolling(window=ma_period, min_periods=1).mean().shift(1)
# min_periods 防止前段資料全為 NaN 而封死整段回測（資料不足時以現有均值替代）
```

那個 `min_periods=1` 是為了**回測**——寧可放行也不要讓 200 日暖機期吞掉整段
歷史。但用在**通知**上後果完全相反：一檔上市 30 天的股票會被算出一條
「年線」並推播給使用者，那是誤報。**誤報比漏報糟**。

回傳 `None` 而非 NaN 的理由同樣有先例（`ladder_system.py:645-649` 的
`atr_ready`）：`NaN > x` 恰好為 False，看起來剛好正確，但那是實作巧合——
一旦中間插入 `fillna()` 或改用 numpy 比較就會翻轉。`None` 迫使呼叫端顯式處理。

**Alternatives considered**:
- **`min_periods=1`**：否決（假均線 → 誤報）。
- **回傳 NaN**：否決（依賴隱性行為）。
- **資料不足時回傳最長可用的均線**：否決。使用者要的是「年線」，
  給他一條 30 日線並標為年線是欺騙。

---

## D5：去重鍵的 `bar_time` 填交易日，不填 5 分線時間戳

**Decision**: 呼叫 `is_alert_already_sent(ticker, bar_time, alert_type)` 時，
`bar_time` 傳入**交易日**（`latest_time.date()`），
`alert_type` 為線別（如 `MA_CROSS_BELOW_YEARLY`）。

**Rationale**: 既有去重表的主鍵是 `(ticker, bar_time, alert_type)`
（`monitor_signals.py:44-50`）。把 `bar_time` 填成交易日，
「每標的每線每交易日至多一則」就由**既有主鍵天然保證**——
零 schema 變更、零額外狀態、零新程式碼。

這也順帶處理了 FR-005 想防的情境：同一交易日內價格在均線附近反覆穿越
（30 分鐘輪詢下可能發生多次），第二次起會被主鍵擋掉。

**注意與既有六種告警的差異**：那六種填的是 K 線時間戳（5 分線棒），
語意是「每根 K 線至多一則」。本案填交易日是**刻意的不同粒度**，
因為均線在同一交易日內是常數，同一天內的多次穿越指的是同一件事。
此差異須在程式碼註解中寫明，避免被後人「統一」掉。

**Alternatives considered**:
- **填 5 分線時間戳**：否決。同一天內反覆穿越會發多則，正是 FR-005 要防的。
- **新增獨立的去重表**：否決。既有機制已足夠，新增表是純粹的複雜度淨增。

---

## 未解決項

無。spec 中 0 個 `[NEEDS CLARIFICATION]`；唯一需使用者裁決的設計選擇
（穿越事件 vs 狀態播報）已於 2026-07-30 確認採穿越，
並以儀表板現況面板（US4/FR-013）補其盲點。
