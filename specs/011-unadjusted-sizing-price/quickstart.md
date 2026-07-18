# Quickstart: 驗收路徑與 SC 對照

**Feature**: 011-unadjusted-sizing-price | **Date**: 2026-07-18

## 前置：raw 資料在哪裡（省下 ~12 分鐘網路回填）

全歷史 raw 表**已存在**，但在另一個 worktree 的 DB：

```
.claude/worktrees/awesome-liskov-efbdf6/trendpoint.db
```

實測內容（2026-07-18 核對，與 spec 010 T017 記錄一致）：

| 表 | 列數 | 範圍 | 備註 |
|----|------|------|------|
| `fut_TXF_raw_daily` | 34,722 | 1998-07-21 ~ 2026-07-17 | 連續層重建的輸入 |
| `fut_TXF_daily` | 6,947 | 同上 | close −5,312 ~ 48,633；**目前僅 5 欄，無 unadj_\*** |
| `fut_MTX_daily` | — | — | mock 來源，不經 rollover |

`trendpoint.db` 為 gitignored 產物。實作時把上述 DB 複製到工作目錄即可，
**不需重跑網路回填**；連續層本就是每次全量重算（`run_ingestion.py:82-84`），
raw 在手就能重建。若 raw 遺失才需 `python run_ingestion.py`（~340 請求 ×
2s 節流 ≈ 12 分鐘，需出網）。

現況即 FR-008 的觸發條件——連續表只有 `datetime/open/high/low/close/volume`，
拿它跑期貨回測必須拋錯（SC-007 的天然測資，不必另造）。

## 驗收步驟

```bash
# 0. 準備資料（一次性）
cp ".claude/worktrees/awesome-liskov-efbdf6/trendpoint.db" ./trendpoint.db

# 1. 硬失敗先驗（SC-007）——此時連續表尚未重建，應拋錯並提示重建
python run_backtest.py --instrument TXF     # 預期：ValueError，訊息含表名與重建指令

# 2. 重建連續層（產生 unadj_* 四欄，FR-002）
python run_ingestion.py                     # raw 已在庫 → 走增量；連續層整表重建

# 3. 確認欄位與正性（SC-004）
sqlite3 trendpoint.db "PRAGMA table_info(fut_TXF_daily);"
sqlite3 trendpoint.db "SELECT COUNT(*) FROM fut_TXF_daily WHERE unadj_close <= 0 OR unadj_open <= 0;"
#   預期：欄位含 unadj_open/high/low/close；違規計數 = 0

# 4. 全歷史回測（SC-001）
python run_backtest.py --instrument TXF
#   預期：完整跑完、不因保證金基準失真觸發爆倉護欄

# 5. 全套測試（憲章硬性關卡）
pytest -q                                   # 既有 182 passed 須維持全綠 + 新增測試
```

## SC ↔ 測試對照（憲章原則 III）

每條驗收標準都對應可自動化測試，無 `[MANUAL]` 項。

| SC | 內容 | 對應測試 | 位置 |
|----|------|---------|------|
| SC-001 | 全歷史跑通、1999-06-21 口數合理 | 期貨回測整合測試 + 手算錨定 | `tests/test_backtester.py` |
| SC-002 | 抽驗 ≥20 交易日 unadj 等於原始近月收盤 | rollover 透傳測試（早期/近年各抽樣） | `tests/test_rollover.py` |
| SC-003 | 修正前後訊號與每點損益 100% 一致 | 進出場序列與 Δ 逐筆比對 | `tests/test_backtester.py` |
| SC-004 | unadj 覆蓋率 100% 且全正 | 資料契約測試 + 全歷史整合 | `tests/test_real_data_integration.py` |
| SC-005 | 既有測試全綠 + 新測試通過 | `pytest -q` | 全套 |
| SC-006 | 稅額以未調整成交價計、手算一致且恆正 | 稅基單元測試（含早年負調整價情境） | `tests/test_trading_costs.py` |
| SC-007 | 缺欄資料執行期貨回測明確失敗、無結果產出 | 缺欄硬失敗測試 | `tests/test_backtester.py` |
| SC-008 | 截斷重建後 unadj 不變（調整後允許變） | 截斷不變性測試 | `tests/test_rollover.py` + `tests/test_lookahead_bias.py` |

## 回歸守門（不可退讓）

- **現貨位元不變**：現貨回測結果與本案前逐筆相同（008b SC-001 承諾延續）。
- **MTX/mock 不變**：mock 期貨不經 rollover，其 `unadj_* = 調整後值`，
  回測結果與本案前完全一致（FR-009 的等價退化）。
- **監控路徑不變**：`monitor_signals.py` 不做 sizing，僅多收四欄。
  以 `python monitor_signals.py --once` 確認訊號輸出不變。

## 已知的驗收難點

**SC-003 的「修正前後比對」需要兩個版本的輸出。** 修正後舊版無法直接重跑
（連續表已含新欄、但舊碼不讀它，故舊碼在新表上仍可跑）——實務做法是在實作
**開始前**先以現況跑一次 TXF 回測並保存 trades CSV 作為基準，或在測試內以
「強制使用調整後價」的參數化方式構造對照組。後者較可靠，建議採之。

**SC-001 的 1999-06-21 錨定**需要當年真實 TX 價位（約 7,500）作為期望值來源；
該值可由重建後的 `unadj_close` 自行提供，測試斷言應寫成「口數 = floor(權益 ×
使用率 ÷ (unadj_close × 200 × margin_rate))」的手算式，而非硬編碼口數，
以免資金參數調整時測試失效。
