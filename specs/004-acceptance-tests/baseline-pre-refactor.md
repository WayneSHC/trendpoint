# T003 基準：`build_indicator_frame()` 重構前（2026-07-12）

**環境**: 本地 macOS、Python 3.12（uv + requirements.txt）、
`trendpoint.db` 由 `data/*_daily.csv` 重建（5 表：0050/00631L/00878/00919/2330 日線）。
**程式狀態**: branch `004-acceptance-tests` @ tasks.md 完成、引擎碼未動
（等同 main `d1cbc26` 的引擎）。

## 單標的回測（`python run_backtest.py`）

| 標的 | 總報酬率 | 交易次數 | 勝率 | 最終淨值 |
| :--- | ---: | ---: | ---: | ---: |
| 2330.TW | +15.83% | 11 | 36.36% | 1,158,306.44 |
| 0050.TW | +8.80% | 12 | 66.67% | 1,088,007.20 |
| 00878.TW | −0.78% | 8 | 75.00% | 992,193.16 |
| 00919.TW | +10.00% | 5 | 60.00% | 1,100,006.52 |
| 00631L.TW | +22.01% | 8 | 75.00% | 1,220,136.44 |

## 組合回測（`python run_portfolio_backtest.py`）

- 總報酬率 **+8.22%**、總交易次數 **34**、MDD −4.60%、勝率 64.71%、最終淨值 1,082,171.70

## 逐筆交易檔 sha256（重構後重跑必須逐位相同）

```
197e810d6925e6aec7ee64b66a2ed2247176a92181e7c7b128d545328c4c7a51  data/0050_TW_backtest_trades.csv
4b97467e1dd78691ac347fa263e8094c93eba5b8dc555c2b6d7ab0ee62fcf25c  data/00631L_TW_backtest_trades.csv
488a5060bbfc7e32d3aa536ed96b5d1cf049bfe2af7dca88923c9889d1999d63  data/00878_TW_backtest_trades.csv
08bf0244ec049f7dd8ccdfd347b22834a81227eb56e506432ae8dbdedd031561  data/00919_TW_backtest_trades.csv
f8f624f340b4981819eb4d1f8533a40599ad6784bb8558bcb42f5734c220cfd1  data/2330_TW_backtest_trades.csv
2e5e4249d0a5483c13e3b56beba664fbca011d4ac5ed427dfe49babdbfeeca87  data/portfolio_backtest_trades.csv
```

## Monitor 指標區塊基準（`baseline_monitor_block.py`，固定合成 df：`make_klines(600, "5min")`）

- 凍結副本 = 重構前 `monitor_signals.py:117-141` 逐字搬運
- 比對欄位 `atr, vwap, mss, bos, ladder, mid_price, upper_price, lower_price`
- `pd.util.hash_pandas_object(...).sum()` checksum：**9912754165909347695**
- 尾三列數值已記錄於腳本輸出（atr≈0.827/0.814/0.827、ladder≈99.632、
  mid≈100.735、upper≈104.788、lower≈96.683）

## T007 迴歸閘門（重構後結果：全數通過，2026-07-12）

- [x] `run_backtest.py` 五標的：報酬率/筆數逐位相同（15.83%/11、8.80%/12、−0.78%/8、10.00%/5、22.01%/8）
- [x] `run_portfolio_backtest.py`：**8.22% / 34 筆**逐位相同
- [x] 六個 trades CSV sha256 逐一相同（見上方雜湊；`diff -q` 全部 IDENTICAL）
- [x] `build_indicator_frame(include_regime=False)` vs 凍結副本：同 df 十個欄位全部零差異（`assert_series_equal` 通過）
- [x] `pytest -q` 全綠（60 passed）

**結論**：`build_indicator_frame()` 抽取為純機械重構——回測與監控兩端輸出
逐位元不變，重構無害性成立（憲法 I + 工作流程第 3 條）。
