# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 進場濾網消融測試腳本 (Ablation Test)

系統的進場有多重確認（結構、動能、趨勢、波動、三關價全域濾網、市況濾網）。
每加一道濾網，勝率上升、交易次數下降——堆到最後一年只交易五次，
統計上等於沒有系統。

本腳本逐一停用每道濾網重跑回測，對比基準（全濾網）結果，
回答一個殘酷的問題：每道濾網到底貢獻了期望值，還是只是「看起來嚴謹」？

判讀原則:
    - 停用某濾網後績效大幅惡化 → 該濾網真正貢獻期望值，保留。
    - 停用後績效不變甚至更好、且交易次數明顯增加 → 該濾網只是在扼殺
      統計樣本，考慮移除。

用法:
    python3 run_ablation.py            # 跑設定檔所有標的
    python3 run_ablation.py 0050.TW    # 只跑指定標的
"""

import os
import sys

from backtester import BacktestEngine
from config import load_config
from db_security import safe_load_db_data

# 待消融的濾網清單: (顯示名稱, disabled_filters 鍵值)
ABLATION_TARGETS = [
    ("基準 (全濾網)", None),
    ("停用結構確認 (MSS/BOS)", "structure"),
    ("停用動能確認 (收紅K)", "momentum"),
    ("停用趨勢確認 (開盤價/VWAP)", "trend"),
    ("停用波動確認 (1.2x ATR 位移)", "volatility"),
    ("停用全域濾網 (三關價+市況)", "global"),
    ("停用市況濾網 (ADX/長均線)", "regime"),
    ("停用 FVG 確認", "fvg"),
]


def run_ablation_for_ticker(engine: BacktestEngine, cfg, ticker: str, df) -> list:
    """
    對單一標的執行全部消融組合，回傳結果列表。
    """
    params = cfg.strategy.get_params_for_ticker(ticker)
    results = []

    for label, disabled in ABLATION_TARGETS:
        disabled_set = frozenset([disabled]) if disabled else frozenset()

        res = engine.run_backtest(
            df=df,
            atr_period=params.atr_period,
            k=params.ladder_k,
            ch_period=params.chandelier_period,
            ch_multiplier=params.chandelier_mult,
            time_limit=params.time_limit,
            use_adx_filter=params.use_adx_filter,
            adx_period=params.adx_period,
            adx_threshold=params.adx_threshold,
            use_ma_filter=params.use_ma_filter,
            ma_period=params.ma_period,
            use_er_filter=params.use_er_filter,
            er_period=params.er_period,
            er_threshold=params.er_threshold,
            use_fvg=params.use_fvg,
            fvg_lookback=params.fvg_lookback,
            disabled_filters=disabled_set,
            verbose=False
        )

        s = res["summary"]
        results.append({
            "label": label,
            "total_return": s.get("total_return", 0.0),
            "max_drawdown": s.get("max_drawdown", 0.0),
            "sharpe": s.get("sharpe_ratio", 0.0),
            "total_trades": s.get("total_trades", 0),
            "win_rate": s.get("win_rate", 0.0),
            "profit_factor": s.get("profit_factor", 0.0),
        })

    return results


def print_ablation_table(ticker: str, results: list):
    """
    列印消融測試對照表。
    """
    print(f"\n========== 消融測試: {ticker} ==========")
    header = f"{'濾網組合':<28} {'總報酬':>9} {'MDD':>8} {'Sharpe':>7} {'交易數':>6} {'勝率':>7} {'PF':>6}"
    print(header)
    print("-" * len(header))

    for r in results:
        pf = r["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{r['label']:<28} {r['total_return']:>+8.2%} {r['max_drawdown']:>8.2%} "
              f"{r['sharpe']:>7.2f} {r['total_trades']:>6d} {r['win_rate']:>7.1%} {pf_str:>6}")

    baseline = results[0]
    print("\n判讀提示:")
    for r in results[1:]:
        delta_ret = r["total_return"] - baseline["total_return"]
        delta_trades = r["total_trades"] - baseline["total_trades"]
        if delta_ret >= 0 and delta_trades > 0:
            print(f"  ⚠ 「{r['label']}」後報酬未惡化 ({delta_ret:+.2%}) 且交易數 +{delta_trades}，"
                  f"該濾網可能只在扼殺樣本數。")


def run(target_ticker: str = None):
    cfg = load_config()
    db_path = cfg.data.database_path

    if not os.path.exists(db_path):
        print(f"錯誤：找不到資料庫 {db_path}，請先執行 run_ingestion.py 下載數據。")
        return

    tickers = [target_ticker] if target_ticker else cfg.data.tickers
    engine = BacktestEngine(config=cfg)

    for ticker in tickers:
        clean_ticker = ticker.replace(".", "_")
        table_name = f"stock_{clean_ticker}_daily"

        df = safe_load_db_data(db_path, table_name)
        if df is None or df.empty:
            print(f"略過 {ticker}：無數據。")
            continue

        results = run_ablation_for_ticker(engine, cfg, ticker, df)
        print_ablation_table(ticker, results)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run(target)
