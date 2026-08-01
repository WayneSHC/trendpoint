# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - B 段實測驅動腳本 (Research: B-Segment Runner)

spec 012 / 013 的驗收都切成兩段：A 段離線可完成（合成資料即足），B 段需要
**真實市場資料**才能回答「這功能到底有沒有用」。本腳本就是 B 段的執行體，
把原本散落在各 spec quickstart 裡的人工步驟收斂成一條可重現的指令。

對應驗收條目：

    spec 013 SC-015  以 monte_carlo 的 p95 回撤校準 dd_limit_pct
    spec 013 SC-014  回撤閘門／結算日閘門的啟用前後對照
    spec 012 SC-010  BOS 量能確認的啟用前後對照

## 為什麼不直接用 run_ablation.py

兩個原因。其一，消融的語意是「相對基準關掉某道機制」，故被消融的機制**必須
先在組態裡啟用**——但本案三項功能一律預設關閉，直接跑消融只會得到三列
「未啟用，略過」。其二，`run_ablation_for_ticker` 不帶資產類別，跑不了期貨
（成本模型／口數／做空皆需注入），而 SC-014 明確要求期貨含空方的對照。

本腳本因此自行組裝情境矩陣，並沿用 `run_backtest.py` 的 `for_asset_class`
元件工廠，使現貨與期貨走同一條程式碼路徑。

## 判讀原則（**兩把不同的尺，不可混用**）

- **訊號濾網**（BOS 量能確認）：看扣成本後**期望值／Profit Factor**。
  啟用後交易數必然下降；若期望值未改善，這道濾網只是在扼殺樣本數。
- **風控閘門**（回撤上限／結算日）：看 **MDD／Calmar**。
  啟用後**總報酬必然下降**——它的工作就是少做交易。
  **以總報酬判定風控閘門無效，是這個專案最容易犯的判讀錯誤。**

## 這支腳本不會做的事

它**不會**改寫 `config/config.yaml`，也不會替你決定要不要採用。組態覆寫全部
在記憶體內完成，跑完即消失。採用與否需要另外的 out-of-sample 確認
（`run_walk_forward.py`），單次回測對照不足以支撐——回撤門檻尤其容易被
後見之明挑選。

用法::

    python run_b_segment.py                    # 跑設定檔所有標的
    python run_b_segment.py 0050.TW            # 只跑指定標的
    python run_b_segment.py --sims 20000       # 加大蒙地卡羅模擬次數
"""

import argparse
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from backtester import BacktestEngine
from config import load_config
from db_security import safe_load_db_data, table_name_for
from instruments import AssetClass, InstrumentRegistry, equity_instrument
from monte_carlo import bootstrap_trades
from trading_costs import for_asset_class

# 情境矩陣：(顯示名稱, run_backtest 覆寫, kind, 是否僅期貨適用)
#
# kind 決定**用哪把尺判讀**，刻意由此宣告而非從標籤字串猜——判讀方向弄反是
# 這個專案最容易犯的錯（詳見模組 docstring 的判讀原則），不該取決於某人日後
# 改了顯示名稱。"combined" 兩把尺都要看：它同時含訊號濾網與風控閘門，
# 任何單一指標都不足以判定。
SCENARIOS: List[Tuple[str, Dict[str, Any], str, bool]] = [
    ("基準（三項皆關閉）", {}, "baseline", False),
    ("啟用 BOS 量能確認", {"use_bos_volume": True}, "signal", False),
    ("啟用回撤閘門", {"use_dd_gate": True}, "risk", False),
    ("啟用結算日閘門", {"use_settlement_gate": True}, "risk", True),
    ("三項全開", {"use_bos_volume": True, "use_dd_gate": True,
                  "use_settlement_gate": True}, "combined", False),
]


def _expectancy(summary: Dict[str, Any]) -> float:
    """每筆交易的平均報酬率。無交易時為 0.0。"""
    returns = summary.get("trade_returns") or []
    return float(sum(returns) / len(returns)) if returns else 0.0


def evaluate(df: pd.DataFrame,
             instrument,
             cfg,
             overrides: Optional[Dict[str, Any]] = None,
             initial_capital: Optional[float] = None) -> Dict[str, Any]:
    """以指定覆寫執行一次回測，回傳裁決所需的指標。

    覆寫只作用於本次呼叫的 `run_backtest` 參數——**不觸碰 config 檔**。
    """
    params = cfg.strategy.get_params_for_ticker(instrument.id)
    cost_model, sizer = for_asset_class(instrument, cfg)
    point_value = instrument.contract.point_value if instrument.contract else 1.0
    is_futures = instrument.asset_class == AssetClass.FUTURES

    kwargs: Dict[str, Any] = dict(
        df=df,
        asset_class=instrument.asset_class,
        cost_model=cost_model,
        sizer=sizer,
        point_value=point_value,
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
        swing_n=params.swing_fractal_n,
        volume_mult=params.mss_volume_mult,
        mss_reversal_entry=params.mss_reversal_entry,
        # SC-014 要求期貨含空方；現貨結構上不存在空方路徑，旗標對其無作用
        enable_short=bool(is_futures),
        use_bos_volume=params.use_bos_volume,
        bos_volume_mult=params.bos_volume_mult,
        bos_volume_period=params.bos_volume_period,
        use_dd_gate=params.use_dd_gate,
        dd_limit_pct=params.dd_limit_pct,
        dd_resume_pct=params.dd_resume_pct,
        use_settlement_gate=params.use_settlement_gate,
        verbose=False,
    )
    kwargs.update(overrides or {})

    # 注意：BacktestEngine 在收到 config 時會**忽略** initial_capital 參數
    # （config 優先，backtester.py:66-72），故覆寫須在建構後直接設屬性。
    engine = BacktestEngine(config=cfg)
    if initial_capital is not None:
        engine.initial_capital = float(initial_capital)
    res = engine.run_backtest(**kwargs)
    s = res["summary"]
    return {
        "total_return": s.get("total_return", 0.0),
        "max_drawdown": s.get("max_drawdown", 0.0),
        "calmar": s.get("calmar_ratio", 0.0),
        "sharpe": s.get("sharpe_ratio", 0.0),
        "total_trades": s.get("total_trades", 0),
        "win_rate": s.get("win_rate", 0.0),
        "profit_factor": s.get("profit_factor", 0.0),
        "expectancy": _expectancy(s),
        "trade_returns": s.get("trade_returns", []),
        "blown_up": s.get("blown_up", False),
    }


def calibrate_dd_limit(baseline: Dict[str, Any], n_sims: int = 5000) -> Dict[str, Any]:
    """spec 013 SC-015：自基準的逐筆報酬重抽，取 p95 回撤作為門檻參考起點。

    重點在於「分布」而非歷史單一路徑——歷史 MDD 只是眾多可能路徑中的一條，
    拿它當風險預算會系統性低估。回傳含 warning 時代表樣本數不足，
    **該數字不可用於定案**。
    """
    mc = bootstrap_trades(baseline.get("trade_returns") or [], n_sims=n_sims, seed=42)
    if mc.get("n_source_trades", 0) == 0:
        return {"available": False, "reason": mc.get("warning", "無交易紀錄")}

    p95_mdd = mc["max_drawdown"][95]
    return {
        "available": True,
        "n_source_trades": mc["n_source_trades"],
        "p95_max_drawdown": p95_mdd,
        # 門檻取回撤幅度的絕對值；p95 代表「二十次裡最壞的那一次」
        "suggested_dd_limit_pct": abs(p95_mdd),
        "warning": mc.get("warning"),
    }


def run_scenarios(df: pd.DataFrame, instrument, cfg,
                  dd_limit_pct: Optional[float] = None) -> List[Dict[str, Any]]:
    """跑完整情境矩陣，回傳逐列結果。

    dd_limit_pct 由 SC-015 校準結果傳入；未給則沿用 schema 預設（形式佔位值）。
    """
    is_futures = instrument.asset_class == AssetClass.FUTURES
    rows: List[Dict[str, Any]] = []

    for label, overrides, kind, futures_only in SCENARIOS:
        if futures_only and not is_futures:
            rows.append({"label": label, "kind": kind, "skipped": True,
                         "note": "結算日閘門僅期貨適用，現貨標的略過"})
            continue

        eff = dict(overrides)
        if dd_limit_pct is not None and eff.get("use_dd_gate"):
            eff["dd_limit_pct"] = dd_limit_pct
            # 恢復門檻須嚴格小於封鎖門檻（schema 硬性要求）
            eff["dd_resume_pct"] = dd_limit_pct / 2.0
        if not is_futures:
            eff.pop("use_settlement_gate", None)   # 現貨無效果，不列入以免誤讀

        row = {"label": label, "kind": kind, "skipped": False}
        row.update(evaluate(df, instrument, cfg, eff))
        row.pop("trade_returns", None)
        rows.append(row)

    return rows


def print_report(ticker: str, instrument, calibration: Dict[str, Any],
                 rows: List[Dict[str, Any]]) -> None:
    kind = "期貨（含空方）" if instrument.asset_class == AssetClass.FUTURES else "現貨"
    print(f"\n{'=' * 96}")
    print(f"B 段實測：{ticker}（{kind}）")
    print("=" * 96)

    print("\n[spec 013 SC-015] 回撤門檻校準（蒙地卡羅重抽）")
    if not calibration.get("available"):
        print(f"  無法校準：{calibration.get('reason')}")
    else:
        print(f"  基準交易筆數      : {calibration['n_source_trades']}")
        print(f"  p95 最大回撤      : {calibration['p95_max_drawdown']:.2%}")
        print(f"  建議 dd_limit_pct : {calibration['suggested_dd_limit_pct']:.4f}"
              f"（= |p95 回撤|，僅為參考起點）")
        if calibration.get("warning"):
            print(f"  ⚠ {calibration['warning']}")
            print("  ⚠ 樣本數不足時本數字不可用於定案——那不是蒙地卡羅能補救的問題。")

    header = (f"\n{'情境':<24} {'總報酬':>9} {'MDD':>8} {'Calmar':>7} {'Sharpe':>7} "
              f"{'交易數':>6} {'勝率':>7} {'PF':>6} {'期望值':>8}")
    print(header)
    print("-" * (len(header) + 12))
    for r in rows:
        if r.get("skipped"):
            print(f"{r['label']:<24} {r['note']}")
            continue
        pf = r["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{r['label']:<24} {r['total_return']:>+8.2%} {r['max_drawdown']:>8.2%} "
              f"{r['calmar']:>7.2f} {r['sharpe']:>7.2f} {r['total_trades']:>6d} "
              f"{r['win_rate']:>7.1%} {pf_str:>6} {r['expectancy']:>+7.3%}")

    active = [r for r in rows if not r.get("skipped")]
    if not active:
        return
    base = active[0]
    print("\n判讀（兩把不同的尺）：")
    for r in active[1:]:
        d_ret = r["total_return"] - base["total_return"]
        d_mdd = r["max_drawdown"] - base["max_drawdown"]      # 更負 = 惡化
        d_cal = r["calmar"] - base["calmar"]
        d_exp = r["expectancy"] - base["expectancy"]
        d_pf = r["profit_factor"] - base["profit_factor"]
        d_trd = r["total_trades"] - base["total_trades"]
        kind = r.get("kind", "signal")

        signal_line = (f"期望值 {d_exp:+.3%}、PF {d_pf:+.2f}、交易數 {d_trd:+d} → "
                       f"{'期望值改善' if d_exp > 0 else '期望值未改善'}")
        risk_line = (f"MDD {d_mdd:+.2%}、Calmar {d_cal:+.2f}、交易數 {d_trd:+d} → "
                     f"{'風險調整後改善' if (d_mdd > 0 or d_cal > 0) else '風險調整後未改善'}")

        if kind == "signal":
            print(f"  [訊號濾網] {r['label']}：{signal_line}（總報酬 {d_ret:+.2%} 僅供參考）")
        elif kind == "risk":
            print(f"  [風控閘門] {r['label']}：{risk_line}"
                  f"（總報酬 {d_ret:+.2%} **不作為判準**）")
        else:
            # 混合情境：兩把尺都要看，且**不給單一結論**——訊號濾網與風控閘門
            # 的效果在此疊加，任一指標的變化都無法歸因到特定機制。
            print(f"  [混合] {r['label']}：")
            print(f"      訊號面 {signal_line}")
            print(f"      風控面 {risk_line}")
            print(f"      ⚠ 兩種機制的效果在此疊加，**無法歸因**；"
                  f"要歸因請看上面各自單獨啟用的列。")

    print("\n提醒：單次回測對照不足以支撐「改為預設啟用」的決定。")
    print("      需再以 run_walk_forward.py 取樣本外確認；門檻值尤其容易被後見之明挑選。")


def load_frame(instrument, db_path: str) -> Optional[pd.DataFrame]:
    tf = "daily" if "daily" in instrument.timeframes else instrument.timeframes[0]
    try:
        df = safe_load_db_data(db_path, table_name_for(instrument, tf))
    except Exception as e:
        print(f"  略過 {instrument.id}：讀取資料表失敗（{e}）。")
        return None
    if df is None or df.empty:
        print(f"  略過 {instrument.id}：無資料（請先執行 run_ingestion.py）。")
        return None
    return df


def run(target: Optional[str] = None, n_sims: int = 5000) -> int:
    cfg = load_config()
    db_path = cfg.data.database_path
    if not os.path.exists(db_path):
        print(f"錯誤：找不到資料庫 {db_path}，請先執行 run_ingestion.py。")
        return 1

    registry = InstrumentRegistry.from_config(cfg.data.tickers, cfg.data.instruments)
    if target:
        try:
            instruments = [registry.resolve(target)]
        except KeyError:
            instruments = [equity_instrument(target)]
    else:
        instruments = list(registry.all())

    evaluated = 0
    for inst in instruments:
        df = load_frame(inst, db_path)
        if df is None:
            continue
        # SC-015 必須早於 SC-014：門檻要先校準，否則對照跑的是形式佔位值
        baseline = evaluate(df, inst, cfg, {})
        calibration = calibrate_dd_limit(baseline, n_sims=n_sims)
        dd_limit = calibration.get("suggested_dd_limit_pct") if calibration.get("available") else None
        # 門檻須落在 schema 值域 (0, 1)；極端值退回不指定（用預設佔位值並註明）
        if dd_limit is not None and not (0.0 < dd_limit < 1.0):
            print(f"  提示：{inst.id} 校準出的門檻 {dd_limit:.4f} 超出值域，改用 schema 預設。")
            dd_limit = None

        rows = run_scenarios(df, inst, cfg, dd_limit_pct=dd_limit)
        print_report(inst.id, inst, calibration, rows)
        evaluated += 1

    if evaluated == 0:
        print("\n沒有任何標的可評估——資料庫是空的嗎？")
        return 1
    print(f"\n完成：共評估 {evaluated} 個標的。")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TrendPoint B 段實測（spec 012 SC-010 / spec 013 SC-014、SC-015）")
    parser.add_argument("ticker", nargs="?", default=None,
                        help="只跑指定標的（預設跑設定檔全部）")
    parser.add_argument("--sims", type=int, default=5000,
                        help="蒙地卡羅重抽次數（預設 5000）")
    args = parser.parse_args()
    sys.exit(run(args.ticker, n_sims=args.sims))
