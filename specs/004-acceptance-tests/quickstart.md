# Quickstart: 驗收測試套件的驗證指南（spec 004）

**Prerequisites**: repo 根目錄、Python 3.10+、`pip install -r requirements.txt`（numba 可有可無）。全程離線。

## 1. 跑三個新測試檔

```bash
pytest -q tests/test_acceptance_parity.py tests/test_acceptance_latency.py tests/test_acceptance_data_quality.py
```

預期：全綠。延遲測試輸出中位數耗時（< 100ms）。

排除效能測試（本機快速迭代時）：

```bash
pytest -q -m "not performance"
```

## 2. 驗證測試「抓得到壞」（spec SC-002）

Parity 測試的有效性以人工注入 off-by-one 驗證：

```bash
# 暫時把 detect_market_structure 的 .shift(1) 拿掉一處（模擬看前偏誤回歸）
# → pytest tests/test_acceptance_parity.py 必須變紅
# 還原後必須回綠。此步驟為一次性人工驗證，結果記錄於 PR 說明。
```

同理可驗證資料品質測試：把 `validate_data_contract` 的跳動檢查閾值臨時改成
`inf` → 離群測試必須變紅。

## 3. 重構迴歸閘門（合併前必過）

`build_indicator_frame()` 抽取重構的無害性證明：

```bash
# 重構前（在 main）跑一次基準：
python run_backtest.py          # 記錄各標的交易筆數、總報酬
# 重構後（在 004 分支）重跑：
python run_backtest.py          # 數字必須逐位相同
pytest -q                       # 既有測試全綠
```

驗收線：每檔交易筆數相同、每筆成交價相同、組合總報酬相同
（引用先前基準：組合 8.22%、34 筆——若 main 有後續變更以當下重跑為準）。

## 4. 無 Numba 模式（spec Edge Case）

```bash
pip uninstall -y numba
pytest -q tests/test_acceptance_parity.py tests/test_ladder_system.py tests/test_lookahead_bias.py
pip install numba               # 還原
```

預期：結果與有 Numba 時一致（CI 的 tests.yml 自動覆蓋此矩陣）。

## 5. CI 驗證

Push 後 GitHub Actions 應顯示：

- 主跑 `pytest -q`（含 performance 測試）於 Python 3.10 與 3.12 全綠。
- no-numba 重跑清單（含 `test_acceptance_parity.py`）全綠。

## 對應關係（憲法 III 稽核用）

| spec 001 驗收標準 | 測試檔 |
| :--- | :--- |
| SC-003 回測↔即時零誤差 | `tests/test_acceptance_parity.py` |
| SC-004 新 K 線 < 100ms | `tests/test_acceptance_latency.py` |
| SC-005 插補容錯 + 離群過濾 | `tests/test_acceptance_data_quality.py` |
