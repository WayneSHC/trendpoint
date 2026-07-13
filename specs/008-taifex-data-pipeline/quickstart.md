# Quickstart / 驗證指南: 台指期資料管線 + Instrument 抽象

證明資料層抽象端到端可運作、且現貨路徑不變。實作細節在 `tasks.md`。

## 前置

- Python 3.10+，依賴已安裝；`config/config.yaml` 存在。
- 既有 equity 資料（`trendpoint.db` 或 `data/*.csv`）可用於 parity 對照。
- **無需真 TAIFEX 資料**：期貨路徑以 Csv/Mock adapter 驗證。

## 驗證情境

### V1 — 現貨路徑位元不變（SC-001）

```bash
pytest -q            # 全套（含 spec 004 parity）須維持綠
python run_backtest.py   # 代表標的回測數字與重構前逐位元相同
```
確認既有 `stock_*` 表名不變、equity 回測結果不變。

### V2 — 期貨資料端到端（SC-002）

在 `config.yaml` 宣告一個 `asset_class=futures`、`source=mock` 的 instrument，執行匯入與載入：
```bash
pytest -q tests/test_futures_pipeline_e2e.py
```
預期：mock（含 rollover 跳空）經 adapter→驗證→存（`fut_*` 表）→載，得到符合資料契約的連續 OHLCV。

### V3 — 表命名集中 + regex（SC-003）

```bash
pytest -q tests/test_table_naming.py
```
斷言 `table_name_for` 對 equity 回傳原 `stock_*`、對 futures 回傳 `fut_*`；regex 同時接受兩者。並確認 ~7 處呼叫點皆改用 helper（grep 檢查無殘留 `f"stock_{...}"` 硬編）。

### V4 — 期貨回測 fail-fast（SC-004）

```bash
pytest -q tests/test_futures_backtest_guard.py
```
斷言：對 futures instrument，**入口層**與**引擎層**皆拋明確錯誤、零績效數字；equity 正常回測不受影響。

### V5 — 向後相容解析（SC-005）

```bash
pytest -q tests/test_instrument_registry.py
```
斷言純字串 ticker（如 `2330.TW`）解析為 `equity`/`yfinance` Instrument；既有 config 不需改即可載入。

## Success Criteria ↔ 測試對照（憲章 III）

| SC | 驗證 | 對應 |
|---|---|---|
| SC-001 現貨位元不變 | V1 | 全套 pytest + parity + run_backtest 對照 |
| SC-002 期貨資料端到端 | V2 | `test_futures_pipeline_e2e.py` |
| SC-003 表命名集中/regex | V3 | `test_table_naming.py` + grep 檢查 |
| SC-004 期貨回測 fail-fast | V4 | `test_futures_backtest_guard.py`（雙層） |
| SC-005 向後相容解析 | V5 | `test_instrument_registry.py` |

## 全套

```bash
pytest -q
```
合併前必須全綠（憲章工作流 2）；資料層改動不得改變既有 equity 回測數字。
