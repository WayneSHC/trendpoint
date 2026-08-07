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
from benchmark import buy_and_hold, format_benchmark_line
from config import load_config
from db_security import safe_load_db_data, table_name_for
from instruments import equity_instrument
from trading_costs import for_asset_class

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
    ("停用 BOS 量能確認", "bos_volume"),
    # spec 013：風控閘門。與上列訊號濾網的判讀方向相反（見模組 docstring）
    ("停用回撤閘門", "dd_gate"),
    ("停用結算日閘門", "settlement_gate"),
]

# ---------------------------------------------------------------------------
# 累加階梯（由裸訊號逐層加回濾網）
# ---------------------------------------------------------------------------
#
# 上面的 ABLATION_TARGETS 是 **leave-one-out**：每列只關掉一道濾網。那回答的是
# 「拿掉這一道會怎樣」，回答不了**「這堆濾網之前，訊號本身有沒有東西」**——
# 而後者才是決定「還要不要調濾網」的樞紐問題。
#
# 邏輯上的理由：**濾網不能創造邊際，只能濃縮邊際。** 濾網只會減少交易，不會
# 憑空生出好交易。若裸訊號的期望值 ≤ 0，任何濾網組合都救不了它——量測值能被
# 抬上去，只可能是小樣本的抽樣運氣（spec 012 的實測正是如此：00878 濾到剩 2 筆、
# 勝率 100%、PF=inf）。
#
# 故本階梯從「僅結構訊號」起跑，逐層加回，讓**交易數與期望值的軌跡**可被讀出：
# 邊際是在哪一層被殺掉的？還是從頭就沒有？
#
# ## 順序不是任意的
#
# `global` 必須排在 `regime` **之前**。進場端的算式是
# `global_ok = global_filter_ok or ('global' in disabled)`，而
# `global_filter_ok = (close > mid_price) and regime_ok`——只要 `global` 仍被停用，
# `regime_ok` 對進場就毫無影響。若把 regime 排在 global 前面，那一階會是純粹的
# 空轉，並讓讀者誤以為市況濾網沒有作用。
#
# ## 為何不含 `structure`
#
# 停用 structure 等於「每根都符合結構條件」，那不是裸訊號，是**無條件進場**
# ——語意上接近「一直在市」，而那個對照已由 benchmark.py 的買進持有提供，
# 且後者的會計更正確（不會每次出場後隔根再進場、重複付摩擦成本）。
#
# ## 為何不含 `bos_volume`
#
# 它在組態預設關閉（`use_bos_volume: false`），停用與否對進場零影響，加進來
# 只會多一列與前一列完全相同的數字。其效果已由 spec 012 的 B 段實測單獨量過。
_LADDER_ORDER = [
    ("momentum", "動能確認（收紅K）"),
    ("trend", "趨勢確認（開盤價/VWAP）"),
    ("volatility", "波動確認（1.2x ATR 位移）"),
    ("global", "三關價（全域）"),
    ("regime", "市況濾網（ADX/長均線/ER）"),
    ("fvg", "FVG 確認"),
]
_LADDER_KEYS = frozenset(k for k, _ in _LADDER_ORDER)


def build_cumulative_ladder():
    """回傳 [(顯示名稱, disabled_filters), ...]，由裸訊號到全濾網逐層加回。

    第 i 列啟用 `_LADDER_ORDER[:i]`，其餘停用；故 disabled 集合逐列**嚴格縮小**
    （單調性由 tests/test_ablation_ladder.py 釘住——順序若被改亂，軌跡就不可讀）。
    """
    rows = [("① 裸訊號（僅結構 MSS/BOS）", frozenset(_LADDER_KEYS))]
    for i, (_, name) in enumerate(_LADDER_ORDER, start=1):
        enabled = {k for k, _ in _LADDER_ORDER[:i]}
        label = f"{'①②③④⑤⑥⑦'[i]} ＋{name}"
        rows.append((label, frozenset(_LADDER_KEYS - enabled)))
    rows[-1] = (f"{rows[-1][0]}　←＝全濾網基準", rows[-1][1])
    return rows


# 風控閘門的消融鍵 → 該列有意義所需的組態旗標
RISK_GATE_KEYS = {
    "dd_gate": "use_dd_gate",
    "settlement_gate": "use_settlement_gate",
}

# 需要「該機制先啟用」才有資訊量的消融鍵 → 對應的組態旗標。
# 風控閘門與 BOS 量能確認皆預設關閉，未啟用時該列與基準列完全相同。
OPT_IN_KEYS = {**RISK_GATE_KEYS, "bos_volume": "use_bos_volume"}


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
        opt_in_flag = OPT_IN_KEYS.get(disabled)
        if opt_in_flag is not None and not getattr(params, opt_in_flag):
            results.append({"label": label, "skipped": True,
                            "note": f"未啟用（{opt_in_flag}=false），略過"})
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
            use_bos_volume=params.use_bos_volume,
            bos_volume_mult=params.bos_volume_mult,
            bos_volume_period=params.bos_volume_period,
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
            "is_risk_gate": disabled in RISK_GATE_KEYS,
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


def run_ladder_for_ticker(engine: BacktestEngine, cfg, ticker: str, df) -> list:
    """累加階梯：由裸訊號逐層加回濾網，回傳結果列表。"""
    params = cfg.strategy.get_params_for_ticker(ticker)
    results = []

    for label, disabled_set in build_cumulative_ladder():
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
            use_bos_volume=params.use_bos_volume,
            bos_volume_mult=params.bos_volume_mult,
            bos_volume_period=params.bos_volume_period,
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


def print_ladder_table(ticker: str, results: list, bare_min_trades: int = 30):
    """列印累加階梯表。重點是**交易數與期望值的軌跡**，不是任一列的絕對值。"""
    print(f"\n========== 濾網累加階梯: {ticker} ==========")
    print("由裸訊號逐層加回。濾網只能濃縮邊際、不能創造邊際——若裸訊號期望值 ≤ 0，")
    print("任何濾網組合都救不了它（量測值被抬高只可能來自小樣本的抽樣運氣）。\n")

    header = (f"{'階梯':<30} {'總報酬':>9} {'MDD':>8} {'Calmar':>7} "
              f"{'交易數':>6} {'勝率':>7} {'PF':>6} {'期望值':>8}")
    print(header)
    print("-" * len(header))
    for r in results:
        pf = r["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{r['label']:<30} {r['total_return']:>+8.2%} {r['max_drawdown']:>8.2%} "
              f"{r['calmar']:>7.2f} {r['total_trades']:>6d} "
              f"{r['win_rate']:>7.1%} {pf_str:>6} {r['expectancy']:>+7.3%}")

    bare, full = results[0], results[-1]
    print("\n判讀：")

    kept = (full["total_trades"] / bare["total_trades"]) if bare["total_trades"] else 0.0
    print(f"  · 濾網堆疊保留了 {full['total_trades']}/{bare['total_trades']} 筆交易"
          f"（{kept:.1%}），期望值 {bare['expectancy']:+.3%} → {full['expectancy']:+.3%}。")

    if bare["total_trades"] < bare_min_trades:
        print(f"  ⚠ **裸訊號僅 {bare['total_trades']} 筆（<{bare_min_trades}）——"
              f"樣本不足，本表的任何一列都不足以裁決。**")
        print("     這不是濾網的問題，是訊號本身的觸發頻率問題；先擴大標的池或拉長期間。")
    elif bare["expectancy"] <= 0.0:
        print(f"  ⚠ **裸訊號期望值 {bare['expectancy']:+.3%} ≤ 0——訊號本身無邊際。**")
        print("     此時調濾網是在雜訊上做優化：濾網只會減少交易，無法把負期望值變正。")
        print("     正確的下一步是換訊號或換標的，不是繼續調門檻。")
    else:
        print(f"  · 裸訊號期望值為正（{bare['expectancy']:+.3%}，"
              f"{bare['total_trades']} 筆），濾網堆疊值得檢視逐層貢獻。")

    # 逐層增量：找出「殺樣本但沒換到期望值」的那幾層
    print("\n  逐層增量（期望值 / 交易數）：")
    for prev, cur in zip(results, results[1:]):
        d_exp = cur["expectancy"] - prev["expectancy"]
        d_trd = cur["total_trades"] - prev["total_trades"]
        flag = ""
        if d_trd < 0 and d_exp <= 0:
            flag = "  ⚠ 殺樣本且未換到期望值"
        print(f"    {cur['label']:<30} {d_exp:+8.3%} / {d_trd:+5d}{flag}")


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

        # 買進持有對照：先印，讓下面每一列的絕對報酬都有機會成本可對照。
        # 元件與策略同源（現貨路徑，equity 元件），故兩邊會計可比。
        cm, sz = for_asset_class(equity_instrument(ticker), cfg)
        bm = buy_and_hold(df, engine.initial_capital, cm, sz)
        print(f"\n########## {ticker} ##########")
        print(format_benchmark_line(bm))

        results = run_ablation_for_ticker(engine, cfg, ticker, df)
        print_ablation_table(ticker, results)

        ladder = run_ladder_for_ticker(engine, cfg, ticker, df)
        print_ladder_table(ticker, ladder)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run(target)
