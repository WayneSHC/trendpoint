# Phase 1 Data Model: 進場閘門（回撤上限 + 結算日封鎖）

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-07-30

本案不觸碰任何持久化 schema。以下為**狀態機**、**預計算集合**、**組態參數**
與**輸出欄**四類實體。

## 1. 回撤閘門狀態機（`DrawdownGate`）

有狀態元件。與 repo 其他元件的關鍵差異：它**路徑相依**，不是 df 的函數
（見 research.md D2）。

### 狀態

| 狀態 | 語意 |
|---|---|
| `OPEN` | 允許開新倉（初始狀態） |
| `BLOCKED` | 禁止開新倉；出場不受影響 |

### 內部欄位

| 欄位 | 型別 | 初始值 | 說明 |
|---|---|---|---|
| `peak` | `float` | 初始資金 | 歷史權益峰值（自回測起點累計，不設滾動窗） |
| `blocked` | `bool` | `False` | 當前是否封鎖 |

### 狀態轉移

```text
dd = (equity - peak) / peak          # ≤ 0，peak > 0 恆成立
OPEN    → BLOCKED : dd <= -dd_limit_pct
BLOCKED → OPEN    : dd >= -dd_resume_pct
其餘情形維持原狀態（遲滯區間 -dd_limit_pct < dd < -dd_resume_pct）
```

### 更新時點（憲章原則 I 的落點）

**必須在每根迴圈尾端**（權益 append 處 `backtester.py:581`）以當根權益更新；
閘門於**下一根**開頭讀取。搬到迴圈開頭即構成看前偏誤——見 research.md D2。

### 邊界

| 情境 | 行為 | 依據 |
|---|---|---|
| 第一根（尚無峰值） | `peak = 初始資金`，`dd = 0` → `OPEN` | spec Edge Cases |
| `equity > peak` | 更新 `peak`，`dd = 0` | 定義 |
| `peak <= 0` | 不可能發生——爆倉防護在權益 ≤ 0 時已終止回測（`backtester.py:538`）。實作仍須防除零 | 防禦性 |
| `dd_resume_pct == 0.0` | 合法：需回撤完全回復才解除（遲滯的特例） | research.md D3 |

## 2. 結算日集合（`settlement_days`）

無狀態純函式，可預計算。

| 屬性 | 值 |
|---|---|
| 簽名 | `settlement_days(index: pd.DatetimeIndex) -> set[datetime.date]` |
| 定義 | 索引涵蓋的每個（年, 月）取第三個週三；若該日不在索引的交易日集合中，取其後第一個交易日 |
| 適用 | **僅期貨**（FR-007）。現貨標的不消費此集合 |
| 比較粒度 | `date`（日內資料時，同一日的所有棒一致封鎖） |

### 邊界

| 情境 | 行為 | 依據 |
|---|---|---|
| 第三個週三為假日（不在索引） | 取其後第一個交易日 | FR-006、SC-007 |
| 該月第三個週三之後無任何交易日（資料尾端截斷） | 該月無結算日（集合中不含） | 防禦性；不得拋錯 |
| 索引為日內（5 分線） | 先取 `.date()` 去重再判定 | — |
| 索引未涵蓋完整月份 | 僅對涵蓋到的月份產出 | — |

## 3. 組態參數（新增四項）

置於 `config/config.yaml` 的 `strategy.default`，可經
`strategy.ticker_overrides.<ticker>` 覆寫（FR-012）。

| 參數 | 型別 | 預設 | 值域 | 說明 |
|---|---|---|---|---|
| `use_dd_gate` | `bool` | `False` | — | 啟用回撤閘門。**預設關閉**（FR-009） |
| `dd_limit_pct` | `float` | `0.20` | `> 0`, `< 1` | 回撤達此值封鎖開新倉（正數表示幅度） |
| `dd_resume_pct` | `float` | `0.10` | `>= 0`, `< 1` | 回撤回復至此值以內解除封鎖 |
| `use_settlement_gate` | `bool` | `False` | — | 啟用結算日封鎖（僅期貨生效） |

### 跨欄位驗證（FR-005）

```text
dd_resume_pct < dd_limit_pct        # 嚴格小於，相等亦拒絕
```

以 Pydantic model validator 實作。相等會導致邊界逐根翻動（flapping），
故不可放行——**這是必須被 schema 擋下的設定，不是使用者的自由**。

### 預設值的地位（重要）

`dd_limit_pct: 0.20` / `dd_resume_pct: 0.10` **僅為形式佔位**。因閘門本身預設
關閉，這兩個值在預設狀態下不生效。實際採用值須由 SC-015 以 `monte_carlo`
的 p95 回撤分布校準後決定並記錄依據——回撤門檻是最容易被後見之明挑選的參數。

### 為何沒有「結算日前後 N 日」參數

FR-006 只要求封鎖結算日當日。加一個 window 參數會是第五個旋鈕，
而其合理值缺乏先驗依據（不像回撤門檻可由 p95 校準）。若實測顯示需要，
另案處理。

## 4. 輸出欄：`block_reason`

| 屬性 | 值 |
|---|---|
| 名稱 | `block_reason` |
| 型別 | `str`（未封鎖為空字串 `""`） |
| 位置 | `equity_curve` DataFrame（逐根時序） |
| 存在條件 | 僅當 `use_dd_gate` 或 `use_settlement_gate` 為真；否則**不輸出** |
| 值域 | `""` / `"drawdown"` / `"settlement"` / `"drawdown+settlement"` |

### 為何掛 `equity_curve` 而非 `trades`

封鎖事件的本質是「**沒有**發生交易」。放進 `trades` 會製造「不是交易的交易列」，
而 `_calculate_metrics`（`backtester.py:617+`）以 `action` 欄分類統計，
多出的列會污染勝率與交易筆數。詳見 research.md D5。

### 條件輸出的安全性（已核對）

`_calculate_metrics` 只取 `df_equity['equity']` 與 `.get('position_value')`
（`backtester.py:609-616`）；既有測試只斷言 `res["equity_curve"]["equity"]`
（`test_professional_upgrades.py:100`、`test_real_data_integration.py:171`、
`test_short_futures_e2e.py:58`）。無任何欄位集斷言，故新增條件欄安全。

## 5. 消融鍵（新增兩項）

`run_ablation.py` 的 `ABLATION_TARGETS` 新增：

```text
("停用回撤閘門",   "dd_gate")
("停用結算日閘門", "settlement_gate")
```

引擎對 `disabled_filters` 中出現的鍵，將對應閘門視為**恆開**（不封鎖）。

**前提**：消融的意義是「相對基準關掉某道機制」，故執行消融時對應的
`use_*_gate` 必須為 `True`；未啟用時該列須明示「未啟用，略過」，
不得靜默輸出與基準相同的數字（沿用 spec 012 data-model §4 的同一原則）。

## 6. 實體關係

```text
config（4 參數）
   ↓
DrawdownGate（狀態機，路徑相依）──┐
settlement_days（預計算集合）────┴→ gate_ok（單一布林）
                                        ↓
                        AND 掉 is_entry / short_entry
                                        ↓
                              block_reason（條件輸出欄）
```

`gate_ok` 的合成發生在接線**前一步**，這是「兩道閘門共用出口、但可獨立開關」
得以成立的位置（research.md D6）。
