# 003 前回歸基線（T001）

**日期**: 2026-07-16 | **基底**: main `ccb8b00`（007+008a+008b 已併）| **venv**: Python 3.13

## pytest

`pytest -q` → **133 passed**。

## 全標的回測（`python run_backtest.py`，預設 config）

| 標的 | 總報酬 | 交易數 |
|---|---|---|
| 2330.TW | 15.83% | 11 |
| 0050.TW | 5.43% | 13 |
| 00878.TW | 1.94% | 9 |
| 00919.TW | 10.00% | 5 |
| 00631L.TW | 36.44% | 7 |
| TXF（mock，long-only） | 46.74% | 4 |
| MTX（mock，long-only） | 9.16% | 1 |

T013（SC-003 硬關卡）對照錨點：`enable_short` 預設 false 下，上表**逐位元不變**。
