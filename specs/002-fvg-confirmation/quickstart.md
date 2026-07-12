# Quickstart: FVG 確認的驗證指南（spec 002）

**Prerequisites**: repo 根目錄、Python 3.10+、`trendpoint.db` 已建（`python run_ingestion.py`
或用 data/*_daily.csv 重建）。單元/parity 測試離線可跑。

## 1. 跑新測試

```bash
pytest -q tests/test_fvg_confirmation.py tests/test_lookahead_bias.py tests/test_acceptance_parity.py
```

預期：全綠。含 FVG 偵測單元、mss 閘門真值表、`use_fvg=False` 零差異、
FVG tail-tamper、`use_fvg=True` parity 變體。

## 2. SC-001：FVG 確認後 MSS 數量下降且非零

```bash
# 對五檔標的比較 use_fvg True/False 的 MSS 計數（實作期以小腳本或測試斷言）
python run_backtest.py            # 預設 use_fvg=true
```

驗收線：每檔 `use_fvg=True` 的 MSS 數量 <（同標的）`use_fvg=False`，且 > 0。
**若某標的歸零**（M 太緊）：放寬 `config.yaml` 的 `fvg_lookback`（M）並記錄
（research.md R3 的緩解）。此比較數字寫入 PR。

## 3. SC-002：消融報告顯示 FVG 的 EV 貢獻

```bash
python run_ablation.py            # 表中應多出「停用 FVG 確認」一列
```

預期：輸出含 baseline vs 停用 FVG 的報酬/MDD/Sharpe/筆數/勝率/PF 對照，
可讀出 FVG 對每筆交易期望值的正負貢獻（數字即交付，正負皆可）。

## 4. SC-003 + 基準重現閘門（合併前必過）

```bash
# (a) 基準重現：use_fvg=False 時回測與 spec 001 逐位元相同
#     實作期取 FVG 前的 main 基準（交易 CSV sha256），關 FVG 重跑比對
python run_backtest.py            # 需先在 config 設 use_fvg=false 或用消融
# (b) 看前偏誤三層防線
pytest -q tests/test_lookahead_bias.py tests/test_acceptance_parity.py
```

驗收線：`use_fvg=False` 的六個 trades CSV 與 FVG 前基準 sha256 相同；
`use_fvg=True` 的 parity 與 tail-tamper 全綠。

## 5. 無 Numba 模式

```bash
pip uninstall -y numba
pytest -q tests/test_acceptance_parity.py tests/test_ladder_system.py tests/test_lookahead_bias.py
pip install numba
```

（`test_acceptance_parity.py` 已在 CI no-numba 清單；FVG 為純 pandas，
不涉 numba，兩模式必一致。）

## 對應關係（憲法 III 稽核）

| spec 002 驗收標準 | 驗證 |
| :--- | :--- |
| SC-001 MSS 下降且非零 | `run_backtest.py` 計數比較 + 測試斷言 |
| SC-002 消融 EV 報告 | `run_ablation.py` 的「停用 FVG 確認」列 |
| SC-003 看前偏誤測試 | `test_fvg_confirmation.py` + `test_lookahead_bias.py` + parity 變體 |
