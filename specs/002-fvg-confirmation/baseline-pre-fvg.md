# Baseline — FVG 前迴歸錨點（spec 002）

**取得時間**: 2026-07-12 | **錨定 commit**: `3a340c3`（FVG 任何引擎改碼之前）
**用途**: T012 基準重現閘門的比對基準——`use_fvg=False`（或 `disabled_filters={'fvg'}`）
重跑後，下列六個 trades CSV 的 sha256 必須逐一相同，證明 FVG 邏輯未洩入基準路徑。

**重建指令**（db 為 gitignored）：`python run_ingestion.py`，或由 `data/*_daily.csv` 重建
（表名 `stock_{ticker}_daily`）。

## 1. 單標的回測（`python run_backtest.py`）

| 標的 | 總報酬率 | 交易次數 | 勝率 | 盈虧比 (PF) |
| :--- | ---: | ---: | ---: | ---: |
| 2330.TW | +15.83% | 11 | 36.36% | 1.95 |
| 0050.TW | +8.80% | 12 | 66.67% | 2.04 |
| 00878.TW | −0.78% | 8 | 75.00% | 0.88 |
| 00919.TW | +10.00% | 5 | 60.00% | 1.58 |
| 00631L.TW | +22.01% | 8 | 75.00% | 4.34 |

## 2. 組合回測（`python run_portfolio_backtest.py`）

| 指標 | 值 |
| :--- | ---: |
| 最終淨值 | 1,082,171.70 |
| 總報酬率 | +8.22% |
| 最大帳戶回撤 (MDD) | −4.60% |
| 總交易次數 | 34 |
| 整體勝率 | 64.71% |
| 整體盈虧比 (PF) | 1.99 |

## 3. 六個交易 CSV 的 sha256（FVG 前錨點）

```
197e810d6925e6aec7ee64b66a2ed2247176a92181e7c7b128d545328c4c7a51  data/0050_TW_backtest_trades.csv
4b97467e1dd78691ac347fa263e8094c93eba5b8dc555c2b6d7ab0ee62fcf25c  data/00631L_TW_backtest_trades.csv
488a5060bbfc7e32d3aa536ed96b5d1cf049bfe2af7dca88923c9889d1999d63  data/00878_TW_backtest_trades.csv
08bf0244ec049f7dd8ccdfd347b22834a81227eb56e506432ae8dbdedd031561  data/00919_TW_backtest_trades.csv
f8f624f340b4981819eb4d1f8533a40599ad6784bb8558bcb42f5734c220cfd1  data/2330_TW_backtest_trades.csv
2e5e4249d0a5483c13e3b56beba664fbca011d4ac5ed427dfe49babdbfeeca87  data/portfolio_backtest_trades.csv
```

---

## T012 基準重現閘門結果（改碼後填入）

> `use_fvg=False` 重跑後六 CSV sha256 是否與上表逐一相同：**待填**

## T013 SC-001 MSS 計數比較（改碼後填入）

> 五標的 `use_fvg` True/False 的 MSS 非零計數；每檔需下降且 > 0：**待填**
