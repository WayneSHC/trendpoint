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

判讀原則（**訊號濾網與風控閘門的方向相反，勿混用同一把尺**）:

  訊號濾網（structure / momentum / trend / volatility / global / regime / fvg）:
    - 停用某濾網後績效大幅惡化 → 該濾網真正貢獻期望值，保留。
    - 停用後績效不變甚至更好、且交易次數明顯增加 → 該濾網只是在扼殺
      統計樣本，考慮移除。

  風控閘門（dd_gate / settlement_gate，spec 013）:
    - 停用風控閘門**必然**使總報酬上升、交易數增加——它的工作就是少做交易。
      用上面那條啟發式去讀，一道正在正常工作的風控會被印成「只在扼殺樣本數」。
    - 正確的裁決指標是**風險調整後**：停用後 MDD 惡化（更深）或 Calmar 下降
      → 該閘門確有貢獻。總報酬下降不構成否定該閘門的理由。

用法:
    python3 run_ablation.py            # 跑設定檔所有標的
    python3 run_ablation.py 0050.TW    # 只跑指定標的
"""

import os
import sys

from backtester import BacktestEngine
from config import load_config
from db_security import safe_load_db_data, table_name_for
from instruments import equity_instrument

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
    # spec 013：風控閘門。與上列訊號濾網的判讀方向相反（見模組 docstring）
    ("停用回撤閘門", "dd_gate"),
    ("停用結算日閘門", "settlement_gate"),
]

# 風控閘門的消融鍵 → 該列有意義所需的組態旗標
RISK_GATE_KEYS = {
    "dd_gate": "use_dd_gate",
    "settlement_gate": "use_settlement_gate",
}


def _expectancy(summary: dict) -> float:
    """每筆交易的平均報酬率（期望值）。無交易時為 0.0。"""
    returns = summary.get("trade_returns") or []
    return float(sum(returns) / len(returns)) if returns else 0.0


def run_ablation_for_ticker(engine: BacktestEngine, cfg, ticker: str, df) -> list:
    """
    對單一標的執行全部消融組合，回傳結果列表。
    """
    params = cfg.strategy.get_params_for_ticker(ticker)
    results = []

    for label, disabled in ABLATION_TARGETS:
        disabled_set = frozenset([disabled]) if disabled else frozenset()

        # 消融的意義是「相對基準關掉某道機制」。風控閘門在組態上未啟用時，
        # 這一列與基準列完全相同、無任何資訊量——**明示略過**而非靜默印出
        # 一組和基準一樣的數字（那會被誤讀成「關掉閘門沒有影響」）。
        gate_flag = RISK_GATE_KEYS.get(disabled)
        if gate_flag is not None and not getattr(params, gate_flag):
            results.append({"label": label, "skipped": True,
                            "note": f"未啟用（{gate_flag}=false），略過"})
            continue

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
            use_dd_gate=params.use_dd_gate,
            dd_limit_pct=params.dd_limit_pct,
            dd_resume_pct=params.dd_resume_pct,
            use_settlement_gate=params.use_settlement_gate,
            disabled_filters=disabled_set,
            verbose=False
        )

        s = res["summary"]
        results.append({
            "label": label,
            "skipped": False,
            "is_risk_gate": gate_flag is not None,
            "total_return": s.get("total_return", 0.0),
            "max_drawdown": s.get("max_drawdown", 0.0),
            "calmar": s.get("calmar_ratio", 0.0),
            "sharpe": s.get("sharpe_ratio", 0.0),
            "total_trades": s.get("total_trades", 0),
            "win_rate": s.get("win_rate", 0.0),
            "profit_factor": s.get("profit_factor", 0.0),
            "expectancy": _expectancy(s),
        })

    return results


def print_ablation_table(ticker: str, results: list):
    """
    列印消融測試對照表。
    """
    print(f"\n========== 消融測試: {ticker} ==========")
    header = (f"{'濾網組合':<28} {'總報酬':>9} {'MDD':>8} {'Calmar':>7} {'Sharpe':>7} "
              f"{'交易數':>6} {'勝率':>7} {'PF':>6} {'期望值':>8}")
    print(header)
    print("-" * len(header))

    for r in results:
        if r.get("skipped"):
            print(f"{r['label']:<28} {r['note']}")
            continue
        pf = r["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{r['label']:<28} {r['total_return']:>+8.2%} {r['max_drawdown']:>8.2%} "
              f"{r['calmar']:>7.2f} {r['sharpe']:>7.2f} {r['total_trades']:>6d} "
              f"{r['win_rate']:>7.1%} {pf_str:>6} {r['expectancy']:>+7.3%}")

    baseline = results[0]
    print("\n判讀提示:")
    for r in results[1:]:
        if r.get("skipped"):
            continue

        # spec 013 T029：風控閘門與訊號濾網的判讀方向相反，不可共用同一把尺。
        # 停用回撤閘門必然讓總報酬上升、交易數增加（它的工作就是少做交易），
        # 套用訊號濾網的啟發式會把一道正常運作的風控印成「只在扼殺樣本數」。
        if r.get("is_risk_gate"):
            delta_mdd = r["max_drawdown"] - baseline["max_drawdown"]   # 更負 = 惡化
            delta_calmar = r["calmar"] - baseline["calmar"]
            if delta_mdd < 0 or delta_calmar < 0:
                print(f"  ✓ 「{r['label']}」後 MDD {delta_mdd:+.2%}、Calmar {delta_calmar:+.2f} —— "
                      f"該閘門確有降低風險（總報酬變化在此不作為判準）。")
            else:
                print(f"  ⚠ 「{r['label']}」後 MDD {delta_mdd:+.2%}、Calmar {delta_calmar:+.2f} —— "
                      f"風險調整後未見改善，該閘門在本樣本無貢獻。")
            continue

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
        table_name = table_name_for(equity_instrument(ticker), "daily")

        df = safe_load_db_data(db_path, table_name)
        if df is None or df.empty:
            print(f"略過 {ticker}：無數據。")
            continue

        results = run_ablation_for_ticker(engine, cfg, ticker, df)
        print_ablation_table(ticker, results)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run(target)
