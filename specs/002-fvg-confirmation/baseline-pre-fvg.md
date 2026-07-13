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

## T012 基準重現閘門結果 — ✅ 通過

`use_fvg=False`（config 暫關）重跑 `run_backtest.py` + `run_portfolio_backtest.py`，
六個 trades CSV 的 sha256 與上表**逐一完全相同**（含組合 8.22%/34）。證明：
- `use_fvg=False` 路徑與 spec-001 基準逐位元一致；
- FVG 邏輯未洩入基準路徑（迴歸閘門成立）。

## T013 SC-001 MSS 計數比較 — ✅ 通過（M=3，無歸零）

`detect_market_structure`（period=10）非零 MSS 計數：

| 標的 | MSS(use_fvg=False) | MSS(use_fvg=True) | 下降 | M |
| :--- | ---: | ---: | ---: | ---: |
| 2330.TW | 173 | 165 | −8 | 3 |
| 0050.TW | 263 | 245 | −18 | 3 |
| 00878.TW | 111 | 99 | −12 | 3 |
| 00919.TW | 81 | 77 | −4 | 3 |
| 00631L.TW | 191 | 181 | −10 | 3 |

每檔皆下降且 > 0 → SC-001 成立。R3 的「MSS 可能歸零」風險未實現，`fvg_lookback=3` 無需放寬。

## T014 SC-002 消融報告 — 零 delta（附機制解釋）

`run_ablation.py` 的「停用 FVG 確認」列與「基準 (全濾網)」**逐項完全相同**（五標的）：

| 標的 | 基準 = 停用 FVG（總報酬 / 交易數 / 勝率 / PF） |
| :--- | :--- |
| 2330.TW | +15.83% / 11 / 36.4% / 1.95 |
| 0050.TW | +8.80% / 12 / 66.7% / 2.04 |
| 00878.TW | −0.78% / 8 / 75.0% / 0.88 |
| 00919.TW | +10.00% / 5 / 60.0% / 1.58 |
| 00631L.TW | +22.01% / 8 / 75.0% / 4.34 |

**SC-002 的經驗答案：FVG 對回測 EV 的貢獻為 0（零 delta）。** 數字即交付（quickstart「正負皆可」）。

> **後續（spec 007，2026-07-12）**：下述「結構性根因（mss ⊆ bos）」已由 spec 007 解除——
> MSS 校正為 fractal 反轉訊號並取得獨立進場，現已能影響回測 P&L（007 SC-002 非零 delta）。
> 惟 FVG 的**邊際** P&L 價值在現有資料上仍約為 0（反轉進場稀疏且恰皆伴隨 FVG）——
> 見 `specs/007-mss-entry-distinction/baseline-pre-mss.md` US3 段。

### 為何為零——關鍵結構發現（mss ⊆ bos）

`detect_market_structure` 中 `bull_mss = bull_bos & strong_volume`、
`bear_mss = bear_bos & strong_volume`——**每個 MSS bar 必然也是同向 BOS bar**。
而單標的（`backtester.py:176`）與組合（`portfolio_backtester.py:355`）的結構進場條件
都是 `mss_signal==±1 或 bos_signal==±1`。因 mss==±1 之處 bos 必==±1，
FVG 把某些 mss 歸零**永遠改變不了 `mss OR bos` 的結果** → 交易零變化。

**結論**：
- spec 002 在**訊號層**（SC-001）與**即時監控假反轉告警**（monitor 單獨顯示 mss）
  達成目的——FVG 確實減少 MSS 假訊號。
- 但在**回測 P&L 層零影響**，因當前進場邏輯令 MSS 與 BOS 冗餘。
- 這揭露一個更深的既有設計問題（MSS 在進場路徑形同虛設，被 BOS 涵蓋），
  但**修正它超出 spec 002 範圍**（spec 002 只負責「用 FVG 確認 MSS」，
  不負責重新設計 MSS 如何被消費）。建議另開規格處理「讓 MSS 在進場中具區別性」。
