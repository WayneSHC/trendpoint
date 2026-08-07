# Contract: 累積歷史 CSV

**Feature**: `016-intraday-evaluation-protocol`

累積歷史是本協定唯一的**持久化狀態**，經 Actions artifact 跨執行滾動傳遞
（D1）。它同時是 artifact 的內容契約與 `intraday_snapshot.py` 的 I/O 契約。

## 檔案佈局（artifact 內）

```text
accumulated/
├── <TICKER>.csv          # 逐標的累積歷史，檔名以 '.' 換 '_'（2330_TW.csv）
└── chain_state.json      # 鏈結中繼資料（見下）
```

## CSV 綱要

```text
datetime,open,high,low,close,volume
2026-05-14 09:05:00,1085.0000,1090.0000,1084.0000,1089.0000,1234000
```

| 欄 | 型別 | 約束 |
|---|---|---|
| `datetime` | ISO 8601（`%Y-%m-%d %H:%M:%S`，台北時間、無時區標註） | 嚴格遞增、無重複 |
| `open` `high` `low` `close` | float，**固定 4 位小數** | > 0、非 NaN、`high >= low` |
| `volume` | int64 | >= 0 |

**寫入不變式**（違反即硬失敗，不得靜默修正）：

1. 欄序固定如上；不得增欄、不得改名。
2. 價格一律格式化為 4 位小數——**這是指紋穩定的前提**，不是美觀考量。
3. 無索引名以外的額外索引欄、無 BOM、行尾為 `\n`。

## chain_state.json 綱要

```json
{
  "chain_origin": "2026-05-14 09:05:00",
  "chain_broken": false,
  "criteria_version": "v1",
  "tickers": {
    "2330.TW": {
      "fingerprint": "<sha256>",
      "bars": 3130,
      "first_ts": "2026-05-14 09:05:00",
      "last_ts": "2026-08-06 13:25:00",
      "merge_events": [
        {
          "merged_at_fingerprint": "<sha256>",
          "bars_before": 3130, "bars_after": 3400, "bars_added": 270,
          "overlap_bars": 2860, "conflicts": 3,
          "conflict_first_ts": "2026-07-31 10:00:00",
          "conflict_last_ts": "2026-07-31 11:15:00"
        }
      ],
      "gaps": [
        {"start_ts": "...", "end_ts": "...", "missing_trading_days": 7, "kind": "schedule_lapse"}
      ]
    }
  }
}
```

`kind` 為列舉：`weekend_or_holiday` | `schedule_lapse` | `chain_restart`。
下游一律以 `kind` 判斷，不得以人類可讀標籤字串比對。

## 合併語意（FR-013 / FR-014）

```text
merge(existing, incoming) -> (merged_frame, MergeEvent)
```

- 以時間戳外連接；**重疊處保留 `existing` 的值**（先到者為準，research.md R3）。
- 逐欄比較重疊處；任一欄不同即 `conflicts += 1`，並更新衝突時間範圍。
- 後置條件：`merged_frame` 索引嚴格遞增、無重複，且
  `bars_after >= max(len(existing), len(incoming))`。

## 鏈結中斷語意（FR-023）

前次 artifact 取不回時（首次執行／逾 90 天保留期／上次 run 失敗）：

- `chain_broken` 設為 `true`，`chain_origin` 重設為本次快照起點；
- 每個標的插入一筆 `kind: "chain_restart"` 的 `Gap`；
- 該事實**必須**進入報告的 `inputs` 區——靜默從零開始並照常出報告即為缺陷。

## 保留期與頻率（FR-022）

artifact `retention-days: 90`（平台上限）。排程頻率須顯著低於保留期
（預設每週一次），使單次失敗不致斷鏈。**此二者是同一條約束的兩端**，
改動任一端前須一併檢視另一端。
