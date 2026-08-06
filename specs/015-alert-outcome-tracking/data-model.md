# Phase 1 資料模型：推播訊號的事後表現追蹤（A 段）

**Feature**: `specs/015-alert-outcome-tracking` | **Date**: 2026-08-06

> **本案不觸碰任何既有持久化 schema**：不新增 SQLite 資料表、不改
> `sent_alerts` 主鍵（`monitor_signals.py:44-51`）、不改 `stock_*_daily` /
> `fut_*_daily` 的資料契約、不動 `db_security.TABLE_NAME_PATTERN`
> （`db_security.py:19`）。唯一新增的持久化產物是版本庫內的 JSONL 檔（D1／D2）。

---

## 1. 告警紀錄（Alert Record）

一次訊號**偵測**的時點快照。每個 JSONL 行為一筆。

### 1.1 主鍵

```
(ticker, bar_time, alert_type)
```

與 `sent_alerts` 的去重鍵**同構但語意不同**：`sent_alerts` 記「已通知使用者」，
本紀錄記「訊號成立」。兩者刻意分離（research.md D4）。

### 1.2 欄位

| 欄位 | 型別 | 可變 | 說明 |
|---|---|---|---|
| `ticker` | str | ✕ | 標的識別碼，與 `cfg.data.tickers` / instrument id 同源 |
| `bar_time` | str (ISO 8601) | ✕ | 判定所用**已收盤** K 線的時間。5 分線含時間，日線為日期 |
| `alert_type` | str | ✕ | 見 §1.3 列舉 |
| `direction` | int | ✕ | `+1` 看多／`−1` 看空，由 `alert_type` 決定（§1.3） |
| `timeframe` | str | ✕ | `"5m"` 或 `"daily"`，取自產生該告警的資料路徑 |
| `close` | float | ✕ | 告警當下已收盤棒的收盤價，**前瞻報酬的基準價** |
| `ladder` | float \| null | ✕ | 階梯參考價。三關價與均線告警無此值時為 `null` |
| `upper_price` | float \| null | ✕ | 上關價 |
| `lower_price` | float \| null | ✕ | 下關價 |
| `atr` | float \| null | ✕ | 告警當下 ATR |
| `param_fingerprint` | str | ✕ | 參數識別值，格式見 §3 |
| `notified` | bool | ✎ | 是否確實推播成功。**僅允許 `false → true`**（D3） |
| `detected_at` | str (ISO 8601) | ✕ | 寫入時的系統時間，供稽核用；**不參與任何計算** |
| `outcomes` | object | ✎ | 前瞻結果，見 §2。初始為三個 `null` |

**FR-023 欄位白名單**：以上即為全部欄位。**不得**新增任何憑證、token、
通知收件識別（chat id、LINE user id）或其他個資欄位。此白名單由測試斷言。

**不可變性**：`✕` 欄位一經寫入即不得改動。upsert 遇既有主鍵時，
只更新 `notified`（單向升級）與 `outcomes`（僅補 `null`）。

### 1.3 `alert_type` 列舉與方向

| `alert_type` | direction | 來源 | timeframe |
|---|---|---|---|
| `BULLISH_MSS` | +1 | `monitor_signals.py:217` | 現貨 `5m`／期貨 `daily` |
| `BEARISH_MSS` | −1 | `:224` | 同上 |
| `BULLISH_BOS` | +1 | `:235` | 同上 |
| `BEARISH_BOS` | −1 | `:242` | 同上 |
| `BREAK_UPPER_BAND` | +1 | `:251` | 同上 |
| `BREAK_LOWER_BAND` | −1 | `:259` | 同上 |
| 均線觸價（spec 014，四條線各一型） | −1 | `:279` `check_ma_touch_alerts` | `daily` |

均線觸價為**向下穿越**事件（spec 014 FR-004），方向恆為 `−1`。

**`timeframe` 不可由 `alert_type` 推導**：同一種 `BULLISH_MSS` 在現貨走 5 分線、
在期貨走日線（`monitor_signals.py:161`）。故必須獨立記錄（FR-006）。

---

## 2. 前瞻結果（Forward Outcome）

附掛於告警紀錄的 `outcomes` 物件。三個視窗各自獨立、可部分回填。

```
outcomes: {
  "t1": <Outcome | null>,
  "t3": <Outcome | null>,
  "t5": <Outcome | null>
}
```

### 2.1 `Outcome` 結構

| 欄位 | 型別 | 說明 |
|---|---|---|
| `date` | str (ISO date) | 該視窗對應的日線交易日 |
| `close` | float | 該交易日收盤價 |
| `ret` | float | `close / <紀錄的 close> − 1`，原始報酬 |
| `ret_adj` | float | `ret × direction`，方向調整後報酬（FR-015） |

### 2.2 視窗定義（research.md D6）

設 `D` = `bar_time` 的日期部分。T+N 為該標的日線表中
**日期嚴格大於 `D` 的第 N 根**（`N ∈ {1, 3, 5}`）。
以表中實際存在的列計數 → 自動略過假日與停牌，不需交易日曆。

### 2.3 三態（FR-014／SC-014）

| 狀態 | 判定 | `outcomes.tN` |
|---|---|---|
| 已回填 | 第 N 根存在 | `Outcome` 物件（`ret` 可為 `0.0`） |
| 未到期／不足 | 日線表最後一根日期 ≤ `D`，或大於 `D` 的列不足 N 根 | `null` |
| 資料缺漏 | 日線表不存在或讀取失敗 | `null`，且紀錄層記警告 |

**`null` 與 `0.0` 必須在序列化後可區分**——JSON 天然滿足。
這是選 JSONL 而非 CSV 的附帶好處。

> **【實作階段修訂】未回填的視窗以「鍵不存在」表示，不寫出顯式 `null`。**
> 讀取端等同（`outcomes.get("t3") is None` 兩者皆真），但若寫出顯式 `null`，
> 紀錄建立後的**第一次回填**會把 `{}` 變成 `{"t1": null, "t3": null, "t5": null}`
> ——一次沒有帶來任何資訊的檔案變更，正是 FR-009／SC-010 要防的雜訊。
> 故 `outcomes` 只收納**已回填**的視窗。與 `0.0` 的可區分性不受影響
> （鍵不存在 vs 值為含 `ret: 0.0` 的物件），由 SC-014 的 JSON round-trip 測試守門。

**回填冪等**（FR-013／SC-013）：已為物件者**不重算、不覆寫**；
僅對 `null` 者嘗試填入。故重跑 N 次結果逐欄相同。

---

## 3. 參數識別值（Parameter Fingerprint）

**格式**（research.md D5）：底線分隔的正規字串，鍵序固定：

```
sp{structure_period}_fvg{0|1}_fl{fvg_lookback}_sn{swing_n}
_vm{volume_mult}_bv{0|1}_bvm{bos_volume_mult}_bvp{bos_volume_period}
```

範例（現行監控端預設）：`sp10_fvg1_fl3_sn2_vm1.5_bv0_bvm1.5_bvp20`

**來源**：監控端傳入 `build_indicator_frame` 的結構相關參數
（`monitor_signals.py:194-199`）。浮點數以固定小數位格式化，
確保 `1.5` 與 `1.50` 產生同一字串。

**性質**：單射——值相同 ⇔ 參數相同（不只是雜湊意義上的碰撞不太可能）。

**陷阱（記錄以防日後改用雜湊）**：**不得**使用 Python 內建 `hash()`，
其對 `str` 有 per-process 隨機化，跨輪次不穩定，會直接違反 SC-007。
若改用雜湊必須走 `hashlib`。

**維護義務**：若監控端日後改為傳入市況濾網或其他影響訊號判定的參數，
此清單**必須同步擴充**——否則兩批不可比的樣本會共用同一個識別值，
而那正是本欄位存在的理由。

---

## 4. 持久化佈局

```
alert_log/
├── 2026-08.jsonl
├── 2026-09.jsonl
└── ...
```

- **分片鍵**：`bar_time` 的年月（非寫入時間）——同一根 K 線的紀錄
  永遠落在同一分片，與寫入時機無關。
- **行序**：以 `(bar_time, ticker, alert_type)` 排序後寫回，
  使 diff 穩定、可審閱（避免同一內容因寫入順序不同而產生假 diff）。
- **編碼**：UTF-8、每行一個 JSON 物件、無尾隨空行差異。
- **寫入**：暫存檔 + `os.replace` 原子置換（D3）。

---

## 5. 組態（FR-020）

掛在既有 `alerts` 區塊下（`config/config.yaml:113`，spec 014 建立）：

```yaml
alerts:
  ma_alerts_enabled: false        # 既有，不動
  outcome_tracking:
    enabled: false                # 總開關，預設關閉（FR-019）
    log_dir: "alert_log"          # JSONL 目錄
    horizons: [1, 3, 5]           # 前瞻視窗（交易日）
    min_samples: 20               # 低於此值標示樣本不足（FR-018）
```

Pydantic 模型 `OutcomeTrackingConfig` 掛於既有 `MaAlertConfig` 所在的
`alerts` 模型下。**不放進 `SingleStrategyParams`**——它們是觀察層設定、
不是策略參數（比照 spec 014 research.md D3 的同一判準）。

**驗證規則**：`horizons` 須為遞增正整數且非空；`min_samples ≥ 1`；
`log_dir` 非空且不得以 `data/` 開頭（防止落入 gitignored 目錄，D2）。

---

## 6. 狀態轉移

單一紀錄的生命週期：

```
（不存在）
   │  偵測到訊號（去重之前）
   ▼
[已記錄, notified=false, outcomes 全 null]
   │  推播成功（同輪或後續輪次重試）
   ▼
[已記錄, notified=true,  outcomes 全 null]
   │  回填（T+1 先到期，T+3、T+5 陸續）
   ▼
[已記錄, notified=true,  outcomes 部分或全部填妥]  ← 終態
```

**不可逆**：`notified` 不由 `true` 回到 `false`（SC-005）；
已填的 `outcome` 不回到 `null`、不重算（SC-013）。

**開關關閉時**：不進入此狀態機的任何一格——不建立目錄、不建立檔案、
不讀不寫（FR-019／SC-001）。
