# Quickstart / 驗證指南: MSS 進場區別化（fractal 反轉）

用於證明本功能端到端可運作。實作細節在 `tasks.md`；此處只列可執行的驗證情境與 SC↔測試對照。

## 前置

- Python 3.10+ 環境已安裝依賴；`config/config.yaml` 存在。
- OHLCV 已載入 `trendpoint.db`（`python run_ingestion.py`），或使用 `data/*.csv` 快取。
- **spec 003（短側）之依賴**：短側反轉（做空）之回測驗證需 003 完成；在此之前，長側驗證與訊號層驗證可獨立進行。

## 驗證情境

### V1 — 單元測試（訊號正確性 + 看前偏誤）

```bash
pytest -q tests/test_mss_reversal.py tests/test_lookahead_bias.py
```
預期：swing/結構/MSS 真值表全綠；MSS fractal 遮蔽測試證明 MSS[t] 不依賴 `>t` 或未確認樞紐。

### V2 — MSS ⊄ BOS（SC-001）

在代表性標的的指標框上，斷言存在 bar 使 `mss_signal==±1` 而同向 `bos_signal` 不成立（單元或小型整合測試，`tests/test_mss_reversal.py`）。

### V3 — 前後回測非零 delta（SC-002）

```bash
python run_backtest.py            # 校正後（mss_reversal_entry=True）
# 對照：mss_reversal_entry=False（復現 007 前 BOS-only 進場）
```
比較交易數/勝率/EV/MDD，至少一項非零差異（含 `trading_cost`）。長側先行；短側待 003。

### V4 — FVG on/off 對 P&L 有作用（SC-003）

在校正後系統上跑 `use_fvg=True` vs `use_fvg=False`，比較回測 P&L/交易數差異（回應 spec 002 SC-002 懸案：現在應為非零）。

### V5 — 先平再開、無多空並存（SC-005）

檢查回測 trades，任一時點不同時持有多空；反轉觸發時對反向部位為「先平再開」。

## Success Criteria ↔ 測試對照（憲章 III）

| SC | 驗證 | 對應測試/情境 |
|---|---|---|
| SC-001 MSS ⊄ BOS | V2 | `test_mss_reversal.py` |
| SC-002 前後非零 delta | V3 | 回測對照（長側）；短側 `[BLOCKED-003]` |
| SC-003 FVG 對 P&L 有作用 | V4 | 回測 FVG on/off 對照 |
| SC-004 看前偏誤 | V1 | `test_lookahead_bias.py` |
| SC-005 先平再開/無多空並存 | V5 | 回測 trades 斷言 |
| SC-006 pytest 全綠 | `pytest -q` | CI 硬性關卡 |

## 全套測試

```bash
pytest -q
```
合併前必須全綠（憲章工作流 2）。
