# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - B 段實測驅動腳本 (Research: B-Segment Runner)

spec 012 / 013 的驗收都切成兩段：A 段離線可完成（合成資料即足），B 段需要
**真實市場資料**才能回答「這功能到底有沒有用」。本腳本就是 B 段的執行體，
把原本散落在各 spec quickstart 裡的人工步驟收斂成一條可重現的指令。

對應驗收條目：

    spec 013 SC-015  以 monte_carlo 的回撤分布深尾校準 dd_limit_pct
                     （spec 稱「p95 回撤」＝**幅度**的 p95；帶號分布下取第 5 百分位）
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
import hashlib
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


# 封鎖比例達此值即判為「閘門實質停用策略」，不再以 MDD／Calmar 論成敗。
# 取 0.5 是保守的下限：熔斷機制若封鎖了半數以上的交易日，它已經不是在管理
# 風險而是在取代策略。實測值遠超此線（TXF 為 98.8%），故本門檻的精確位置
# 不影響既有結論；它存在的目的是讓「策略被關掉」無法再偽裝成「風險改善」。
DISABLING_BLOCK_RATIO = 0.5

# 名目槓桿達此倍數即在報告中示警。5× 對應「指數反向 20% 即歸零」——
# 台股史上單年跌幅超過此值者不在少數，故這已是研究用途的上限而非安全值。
# 組態現值為 0.055 / 0.055 = 1×（P1 之後由 9.09× 調降，見 config.yaml 說明）。
HIGH_LEVERAGE_WARN = 5.0


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
    elif is_futures:
        # 期貨資本與現貨分開：大台一口名目值遠大於現貨的 100 萬，
        # 低槓桿下以現貨資本會連一口都下不起（0 筆交易）。
        engine.initial_capital = float(cfg.backtest.futures_init_capital)
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
        "blocked_bars": _blocked_bars(res),
        "margin_dead": _margin_dead(res, df, sizer, is_futures),
    }


def _margin_dead(res: Dict[str, Any], df: pd.DataFrame, sizer, is_futures: bool) -> bool:
    """期貨帳戶是否已「保證金死亡」——權益不足以再下 1 口。

    `blown_up` 只在**權益 ≤ 0** 時觸發（backtester.py FR-011），抓不到慢性失血。
    但在保證金交易下，權益跌破一口保證金後 `FuturesSizer.size` 回傳 0 口，
    策略就此靜默停止——帳戶功能上已經死了，旗標卻仍是 False。

    TXF 實測即如此：權益剩約 2.3%（≈23,000），而 TXF@20,000 的每口保證金是
    220,000，連一口都下不起。「28 年只有 7 筆交易」因此不是策略的特性，
    是**帳戶早早死掉後再也沒錢進場**。不標記這件事，該序列的所有指標都會被
    當成對策略的評價來讀。
    """
    if not is_futures:
        return False
    curve = res.get("equity_curve")
    if curve is None or len(curve) == 0:
        return False
    eq_df = pd.DataFrame(curve) if not isinstance(curve, pd.DataFrame) else curve
    final_equity = float(eq_df["equity"].iloc[-1])
    # 以序列末端的未調整價評估：名目值型計算一律用未調整價（spec 011）
    price_col = "unadj_close" if "unadj_close" in df.columns else "close"
    return sizer.size(final_equity, float(df[price_col].iloc[-1])) < 1.0


def _blocked_bars(res: Dict[str, Any]) -> int:
    """閘門實際封鎖的根數。

    這是區分「未改善」與**「未測到」**的唯一依據——兩者的指標差全是 0，
    但在決策上完全相反：前者是「有證據說沒用」，後者是「沒有證據」。

    `block_reason` 是條件輸出欄（僅任一閘門啟用時存在，見 backtester.py），
    故欄位不存在＝閘門未啟用，回傳 0。
    """
    curve = res.get("equity_curve")
    if curve is None or len(curve) == 0:
        return 0
    df = pd.DataFrame(curve) if not isinstance(curve, pd.DataFrame) else curve
    if "block_reason" not in df.columns:
        return 0
    return int((df["block_reason"].astype(str) != "").sum())


def calibrate_dd_limit(baseline: Dict[str, Any], n_sims: int = 5000) -> Dict[str, Any]:
    """spec 013 SC-015：自基準的逐筆報酬重抽，取回撤分布的**深尾**作為門檻參考起點。

    重點在於「分布」而非歷史單一路徑——歷史 MDD 只是眾多可能路徑中的一條，
    拿它當風險預算會系統性低估。回傳含 warning 時代表樣本數不足，
    **該數字不可用於定案**。

    ## 取哪一端（踩過的坑）

    spec 一路寫「p95 回撤」，指的是**回撤幅度**的第 95 百分位，也就是「二十次
    裡最壞的那一次」。但 `bootstrap_trades` 回傳的 `max_drawdown` 是**帶號的
    負值**，於是幅度的 p95 對應到帶號分布的**第 5 百分位**——
    `np.percentile(mdds, 95)` 取到的反而是最淺的那一側。

    本函式初版就是取了 95，結果每檔標的都校準出 0.00% 的門檻（帶號分布的
    上緣多半是「完全沒有回撤」的幸運路徑）。`monte_carlo.format_monte_carlo_report`
    早已寫明正確慣例：「風險預算應以回撤分布的 5 百分位（最深一側）為準」。
    """
    ## 為何在此攔輸入契約錯誤
    # `bootstrap_trades` 對 <= -100% 的逐筆報酬硬失敗（分母語意壞掉的哨兵）。
    # 在**函式庫**層硬失敗是對的；但本驅動是多標的批次研究工具，讓一檔的壞資料
    # 炸掉整輪會連帶丟失其他標的已算完的結果。故此處轉為「該檔不可校準」，
    # 原因原文照登於報告——失敗仍然可見，只是不再連坐。
    try:
        mc = bootstrap_trades(baseline.get("trade_returns") or [], n_sims=n_sims, seed=42)
    except ValueError as e:
        return {"available": False, "reason": f"逐筆報酬率不合契約：{e}"}
    if mc.get("n_source_trades", 0) == 0:
        return {"available": False, "reason": mc.get("warning", "無交易紀錄")}

    deep_mdd = mc["max_drawdown"][5]
    return {
        "available": True,
        "n_source_trades": mc["n_source_trades"],
        "deep_max_drawdown": deep_mdd,
        # 門檻取回撤幅度的絕對值
        "suggested_dd_limit_pct": abs(deep_mdd),
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
        # 封鎖**比例**才有判讀意義：6919 根在 7000 根的序列上是「策略被關掉」，
        # 在 700000 根上則是正常的熔斷。絕對根數看不出這個差別。
        row["blocked_ratio"] = (row.get("blocked_bars", 0) / len(df)) if len(df) else 0.0
        rows.append(row)

    return rows


def print_report(ticker: str, instrument, calibration: Dict[str, Any],
                 rows: List[Dict[str, Any]],
                 fingerprint: Optional[Dict[str, Any]] = None,
                 leverage: Optional[float] = None) -> None:
    kind = "期貨（含空方）" if instrument.asset_class == AssetClass.FUTURES else "現貨"
    print(f"\n{'=' * 96}")
    print(f"B 段實測：{ticker}（{kind}）")
    print("=" * 96)

    if fingerprint:
        print(f"\n[資料指紋] {fingerprint['table']}｜{fingerprint['rows']} 根｜"
              f"{fingerprint['start']} ~ {fingerprint['end']}｜sha256:{fingerprint['sha256']}")
        print("  回填規格時務必一併記錄此指紋——沒有它，數字無從重現亦無從稽核。")

    if leverage is not None:
        print(f"\n[槓桿] 名目槓桿上限 = margin_utilization / margin_rate = {leverage:.2f}×")
        print(f"  指數反向約 {1.0 / leverage:.1%} 即令權益歸零。此值與價位無關，"
              f"由組態直接決定。")
        if leverage >= HIGH_LEVERAGE_WARN:
            print("  ⚠ 在此槓桿下，回測結果主要反映的是**槓桿設定**而非策略優劣；"
                  "spec 012/013 的裁決不應以此序列為據。")

    print("\n[spec 013 SC-015] 回撤門檻校準（蒙地卡羅重抽）")
    if not calibration.get("available"):
        print(f"  無法校準：{calibration.get('reason')}")
    else:
        print(f"  基準交易筆數      : {calibration['n_source_trades']}")
        print(f"  回撤分布深尾      : {calibration['deep_max_drawdown']:.2%}"
              f"（帶號分布第 5 百分位 = 幅度第 95 百分位）")
        print(f"  建議 dd_limit_pct : {calibration['suggested_dd_limit_pct']:.4f}"
              f"（= |深尾回撤|，僅為參考起點）")
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

    dead = [r["label"] for r in active if r.get("margin_dead")]
    if dead:
        print(f"\n⚠ 保證金死亡：{'、'.join(dead)}")
        print("  期末權益已不足以再下 1 口，策略在序列結束前即靜默停止進場。")
        print("  （blown_up 只在權益 ≤ 0 時觸發，抓不到這種慢性失血。）")
    if base.get("margin_dead"):
        print("\n**基準已保證金死亡，本標的不進行判讀。**")
        print("  基準的交易筆數反映的是「帳戶何時沒錢」而非策略的進場頻率，")
        print("  以它為比較基準得到的任何差值都不具意義。請先調整槓桿再重跑。")
        return

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

        # 風控閘門有三種結果，不是兩種：
        #   封鎖 0 根        → **未測到**（不是未改善——把沒有證據印成有證據說沒用，
        #                      會直接導向相反的決策）
        #   封鎖絕大多數根數 → **策略被停用**（MDD 當然變好，因為幾乎不再交易；
        #                      這不是風險管理的成果）
        #   兩者之間         → 才輪得到 MDD／Calmar 判定
        #
        # 中間那類的真實案例：TXF 在 dd_limit=0.20 下封鎖 6919 根、只剩 1 筆交易，
        # MDD 從 -98.5% 改善到 -22.8%。舊版判為「風險調整後改善」——但那是把
        # 策略關掉的結果，且回撤閘門在單標的回測中是單向閂鎖（權益跌到谷底後
        # 空手則權益不再變動，回撤永遠回不到恢復門檻之上），一旦latch 便不再解除。
        blocked = int(r.get("blocked_bars", 0))
        ratio = float(r.get("blocked_ratio", 0.0))
        if blocked == 0:
            risk_line = ("封鎖 0 根 → **未觸發，無對照數據**"
                         "（本門檻下閘門從未啟動，不得據此判定有效或無效）")
        elif ratio >= DISABLING_BLOCK_RATIO:
            risk_line = (f"封鎖 {blocked} 根（{ratio:.1%}）、交易數 {d_trd:+d} → "
                         f"**閘門實質停用策略，非風險改善**"
                         f"（MDD {d_mdd:+.2%} 係因幾乎不再交易；"
                         f"單標的回測中回撤閘門為單向閂鎖，空手後回撤不再回復）")
        else:
            risk_line = (f"封鎖 {blocked} 根（{ratio:.1%}）、MDD {d_mdd:+.2%}、"
                         f"Calmar {d_cal:+.2f}、交易數 {d_trd:+d} → "
                         f"{'風險調整後改善' if (d_mdd > 0 or d_cal > 0) else '風險調整後未改善'}")

        if kind == "signal":
            print(f"  [訊號濾網] {r['label']}：{signal_line}（總報酬 {d_ret:+.2%} 僅供參考）")
        elif kind == "risk":
            tail = "" if blocked == 0 else f"（總報酬 {d_ret:+.2%} **不作為判準**）"
            print(f"  [風控閘門] {r['label']}：{risk_line}{tail}")
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


def fingerprint(df: pd.DataFrame, table: str) -> Dict[str, Any]:
    """對**實際餵進回測的資料**取指紋。

    工作流程另有一個對 `data/*.csv` 取雜湊的步驟，但那涵蓋不了真正的風險：
    匯入失敗的標的根本不會產出 CSV，於是它完全不被記錄——而它在資料庫裡的
    **舊表照樣被讀取**。B 段 run 30706957226 就是如此：0050.TW 沒有 CSV，
    walk-forward 卻報出與前一次逐項相同的數字。

    所以指紋必須貼著回測的輸入取，而且與數字印在同一份報告裡：
    兩份報告對不上時，先比指紋即可區分「策略改了」與「資料變了」。
    """
    cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    payload = df[cols].to_csv(float_format="%.17g").encode("utf-8") if cols else b""
    return {
        "table": table,
        "rows": len(df),
        "start": str(df.index.min()),
        "end": str(df.index.max()),
        "sha256": hashlib.sha256(payload).hexdigest()[:16],
    }


def load_frame(instrument, db_path: str) -> Optional[Tuple[pd.DataFrame, Dict[str, Any]]]:
    tf = "daily" if "daily" in instrument.timeframes else instrument.timeframes[0]
    table = table_name_for(instrument, tf)
    try:
        df = safe_load_db_data(db_path, table)
    except Exception as e:
        print(f"  略過 {instrument.id}：讀取資料表失敗（{e}）。")
        return None
    if df is None or df.empty:
        print(f"  略過 {instrument.id}：無資料（請先執行 run_ingestion.py）。")
        return None
    return df, fingerprint(df, table)


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
        loaded = load_frame(inst, db_path)
        if loaded is None:
            continue
        df, fp = loaded
        # SC-015 必須早於 SC-014：門檻要先校準，否則對照跑的是形式佔位值
        baseline = evaluate(df, inst, cfg, {})
        calibration = calibrate_dd_limit(baseline, n_sims=n_sims)
        dd_limit = calibration.get("suggested_dd_limit_pct") if calibration.get("available") else None
        # 門檻須落在 schema 值域 (0, 1)；極端值退回不指定（用預設佔位值並註明）
        if dd_limit is not None and not (0.0 < dd_limit < 1.0):
            print(f"  提示：{inst.id} 校準出的門檻 {dd_limit:.4f} 超出值域，改用 schema 預設。")
            dd_limit = None

        rows = run_scenarios(df, inst, cfg, dd_limit_pct=dd_limit)
        lev = None
        if inst.asset_class == AssetClass.FUTURES:
            fut = cfg.trading_cost.futures
            lev = fut.margin_utilization / fut.margin_rate if fut.margin_rate else None
        print_report(inst.id, inst, calibration, rows, fingerprint=fp, leverage=lev)
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
