# Contract: 進場閘門

**Modules**: `risk_gates.py`（新增） | **Consumers**: `backtester.py`、`run_ablation.py`

原則：新增參數皆有預設值，且預設值使既有行為**逐筆、逐根位元不變**
（FR-009 在介面層的表述）。

---

## 1. `settlement_days()`（新增純函式）

```python
def settlement_days(index: pd.DatetimeIndex) -> set[datetime.date]:
```

### 前置條件

- `index` 為遞增的 `DatetimeIndex`（既有資料契約保證）。
- 呼叫端負責判斷是否為期貨（本函式不知道資產類別）。

### 後置條件

- 回傳索引涵蓋之每個（年, 月）的結算日：該月第三個週三；
  若該日不在 `index` 的日期集合中，取其後第一個交易日。
- 純函式：同一輸入恆得同一輸出，**不含任何價量資訊**。
- 日內索引亦正確（先取 `.date()` 去重）。
- 該月第三個週三之後無交易日時（資料尾端截斷），該月不列入——**不得拋錯**。

### 禁止事項

- **禁止**引入外部交易日曆套件或出網查詢（離線 CI 必須能跑）。
- **禁止**硬編碼結算日清單（憲章原則 V）。

---

## 2. `DrawdownGate`（新增有狀態類別）

```python
class DrawdownGate:
    def __init__(self, initial_equity: float, limit_pct: float, resume_pct: float): ...
    @property
    def blocked(self) -> bool: ...
    def update(self, equity: float) -> None: ...
```

### 語意

- `update(equity)`：以當根權益更新 `peak` 與 `blocked`（狀態轉移見
  [data-model.md](../data-model.md) §1）。
- `blocked`：唯讀查詢，反映**最後一次 `update` 為止**的狀態。

### 後置條件

- `resume_pct < limit_pct` 由呼叫端（schema）保證；本類別可另加防禦性斷言。
- `peak` 單調不減。
- `peak <= 0` 時不得除零（防禦；正常情況由爆倉防護先終止回測）。
- **無 I/O、不讀 config、不 import 引擎模組**——單向依賴，可獨立單元測試。

### 呼叫順序契約（憲章原則 I 的落點，最關鍵）

```text
每根迴圈：
  1. 開頭：讀 gate.blocked（反映 i-1 為止）→ 參與進場判定
  2. 尾端：gate.update(當根權益)
```

**禁止**在同一根內先 `update` 再讀 `blocked`——那會使閘門用到當根權益，
構成看前偏誤。SC-004 專門守此點。

---

## 3. `BacktestEngine.run_backtest()`（簽名擴充）

```python
def run_backtest(self, df, ...,                    # 既有參數不變
                 use_dd_gate: bool = False,        # 新增
                 dd_limit_pct: float = 0.20,       # 新增
                 dd_resume_pct: float = 0.10,      # 新增
                 use_settlement_gate: bool = False,# 新增
                 ...) -> Dict[str, Any]:
```

### 接線契約（FR-002 的落點，最高風險）

閘門 **MUST** 接在開新倉判定區塊內、`if is_entry:`（`backtester.py:308`）之前：

```python
if not gate_ok:
    is_entry = False
    short_entry = False
```

**MUST NOT** 以下列任一方式實作：

| 反模式 | 後果 |
|---|---|
| 迴圈開頭 `continue` | 連出場判定與權益 append 一起跳過——封鎖期間停損不執行、權益曲線斷點 |
| 折進 `global_ok`（`:246`） | 消融無法區分來源；封鎖原因無從記錄 |
| 擴充 `check_entry_signal` | 該函式為無狀態純判定，塞入路徑相依狀態會破壞其真值表可測性 |

### 後置條件

- 兩道閘門皆關閉（預設）→ 回測結果與本案實作前**逐筆、逐根**相同
  （含 `equity_curve` 每根數值與欄位集）。
- 閘門 **MUST NOT** 影響任何出場路徑、部位風控狀態更新、爆倉防護。
- 閘門對多空**無方向性**：`is_entry` 與 `short_entry` 同時被 AND。
- `use_settlement_gate` 對現貨標的無效果且不報錯（FR-007）；
  判斷沿用引擎既有的 `is_futures`。
- `disabled_filters` 含 `'dd_gate'` / `'settlement_gate'` 時，
  對應閘門視為恆開（消融語意，與既有消融鍵一致）。
- 任一閘門啟用 → `equity_curve` 增 `block_reason` 欄；皆關閉 → 不輸出該欄。

---

## 4. `run_ablation.py`（清單擴充）

```python
ABLATION_TARGETS = [
    ...,                                  # 既有 9 列不變（含 spec 012 新增列）
    ("停用回撤閘門",   "dd_gate"),        # 新增
    ("停用結算日閘門", "settlement_gate"),# 新增
]
```

### 契約

- 執行消融時對應的 `use_*_gate` 須為 `True`，否則該列與基準列相同、無資訊量。
- 未啟用時該列 **MUST** 明示「未啟用」，不得靜默輸出與基準相同的數字。
- 消融輸出 **MUST** 含 **Calmar 與 MDD**——本案的裁決指標是風險調整後
  （FR-014）。若既有輸出僅有總報酬與期望值，須一併補上這兩欄，
  否則 SC-011 無法達成。

---

## 5. `config` schema（擴充）

```python
use_dd_gate: bool = False
dd_limit_pct: float = Field(default=0.20, gt=0.0, lt=1.0)
dd_resume_pct: float = Field(default=0.10, ge=0.0, lt=1.0)
use_settlement_gate: bool = False
```

### 跨欄位驗證

`dd_resume_pct < dd_limit_pct`（嚴格小於；相等亦須拒絕）。
以 model validator 實作，錯誤訊息須指出兩個值與遲滯的用意。

### 契約

- 四參數皆可經 `ticker_overrides` 覆寫（FR-012）。
- 預設值僅為形式佔位（閘門預設關閉故不生效）；實際值由 SC-015 校準後決定。
