# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 買進持有對照基準 (Buy-and-Hold Benchmark)

## 為什麼需要這個模組

在此之前，本專案所有報告的績效數字都是**絕對報酬，沒有任何對照組**——
`grep -r 'buy.?hold|benchmark|大盤'` 在全部 .py 檔零命中（2026-08-07 實測）。

後果是機會成本完全隱形。「2330 全歷史 -14.15%」這個數字本身無從判讀：
它可能代表策略有問題，也可能代表那段期間市場本來就跌——但同期買進持有
是多少，報告從來沒說。反過來，「00631L +36.44%」看起來是賺的，然而它是
2 倍槓桿的台股 ETF，同期買進持有可能高出一個數量級。

**沒有對照組的報酬數字無法支撐任何決策。**

## 與績效模組的分工

`performance.py` 刻意「與回測引擎解耦、只由淨值曲線推導」。買進持有需要
成本模型與 sizing 元件（否則就是零成本績效，違反憲章原則 II），故獨立成模組，
不去污染 performance.py 的解耦性；算完淨值曲線後仍交由
`performance.compute_performance_metrics` 出指標，兩邊用同一把尺。

## 會計語意刻意鏡像回測引擎

要可比較，兩邊的會計必須逐項對齊，故本模組**刻意複製** `backtester.py` 的
下列語意，而非另立一套：

- **進場在第 1 根的開盤**（不是第 0 根收盤）。策略是第 N 根出訊號、第 N+1 根
  開盤成交，最早可能的成交點就是索引 1。從第 0 根算起會讓對照組白賺一根。
- **口數/股數走同一個 sizer**，期貨的 sizing 價用第 0 根的 `unadj_close`
  （spec 011 FR-004：名目值型計算一律用未調整價）。
- **進出場都付摩擦成本**，稅基同樣走未調整價。
- **期貨的未實現損益 = 口數 × Δ調整後價 × 乘數**（back-adjust 保留點差）。

## 判讀時務必連曝險一起看

買進持有的曝險接近 100%，策略通常遠低於此（本專案實測多在 10% 以下）。
拿總報酬單獨比對買進持有是不公平的比法——**曝險欄位因此一律隨指標回傳**，
呈現端不得只印報酬。低曝險換得的閒置資金有其他用途，這是策略的隱性優勢；
反之，若策略在**曝險遠低**的情況下 Sharpe 仍輸給買進持有，那就沒有爭辯空間了。
"""

from typing import Any, Dict, Optional

import pandas as pd

from performance import compute_performance_metrics

# 最短可用序列：第 0 根供 sizing、第 1 根成交、至少再一根才有報酬率可算
_MIN_BARS = 3


def _cost_basis_price(row, field: str, side: str, exec_price: float,
                      cost_model, is_futures: bool) -> float:
    """成本/稅基價。鏡像 `backtester.cost_basis_price`：期貨走未調整價、現貨走成交價。"""
    if not is_futures:
        return exec_price
    return cost_model.slip(float(row[f"unadj_{field}"]), side)


def buy_and_hold(df: pd.DataFrame,
                 initial_capital: float,
                 cost_model,
                 sizer,
                 *,
                 point_value: float = 1.0,
                 is_futures: bool = False) -> Dict[str, Any]:
    """第 1 根開盤買進、末根收盤賣出，全程持有。

    參數:
        df: 含 OHLCV 的 DataFrame（期貨另需 `unadj_*` 四欄，spec 011 FR-008）
        initial_capital: 初始資金（須與被對照的策略同值，否則不可比）
        cost_model / sizer: 與策略同一組元件（`trading_costs.for_asset_class`）
        point_value: 每點價值（現股 1.0、期貨為契約乘數）
        is_futures: 期貨會計語意（不付名目、只扣摩擦成本；損益走點差 × 乘數）

    回傳:
        `{"available": True, ...performance 指標..., "shares", "entry_price",
          "exit_price", "total_costs"}`；不可用時 `{"available": False, "reason": ...}`
    """
    if df is None or len(df) < _MIN_BARS:
        return {"available": False, "reason": f"序列少於 {_MIN_BARS} 根，無法建立對照"}

    if is_futures and "unadj_close" not in df.columns:
        # 硬失敗不 fallback：語意同 spec 011 FR-008。用調整後價當名目值會讓
        # 對照組的口數與成本錯得離譜，而數字看起來仍然合理。
        raise ValueError(
            "期貨買進持有對照需要 unadj_* 欄位（spec 011 FR-004）；"
            "缺欄時不得以調整後價替代。"
        )

    sig_row = df.iloc[0]      # 「訊號根」——只用它已知的資訊做 sizing
    fill_row = df.iloc[1]     # 成交根

    entry_price = cost_model.slip(float(fill_row["open"]), "buy")
    sizing_price = float(sig_row["unadj_close"]) if is_futures else entry_price
    shares = sizer.size(initial_capital, sizing_price)

    if shares <= 0.0:
        return {"available": False,
                "reason": f"初始資金不足以建立 1 單位部位（sizing 價 {sizing_price:,.2f}）"}

    entry_costs = cost_model.entry_costs(
        _cost_basis_price(fill_row, "open", "buy", entry_price, cost_model, is_futures),
        shares)

    if is_futures:
        cash = initial_capital - entry_costs.total          # 保證金為佔用非支出
    else:
        cash = initial_capital - shares * entry_price - entry_costs.commission

    close = df["close"].astype(float)
    if is_futures:
        held_value = shares * (close - entry_price) * point_value
        position_value = shares * close * point_value       # 名目曝險
    else:
        held_value = shares * close
        position_value = held_value

    equity = pd.Series(float(initial_capital), index=df.index, dtype=float)
    equity.iloc[1:] = cash + held_value.iloc[1:]
    exposure_series = pd.Series(0.0, index=df.index, dtype=float)
    exposure_series.iloc[1:] = position_value.iloc[1:]

    # 末根收盤平倉。**末點必須以滑價後的成交價結算**，不是原始收盤價——
    # 中途各根是「未實現、按收盤市值」，最後一根卻是真的成交，要付賣出滑價。
    # 初版在此只把手續費/稅計入末點、部位仍以原始收盤結算，於是滑價被漏掉：
    # 期貨每口少算 1 tick × 乘數（實測 200 元），現貨少算約成交金額的 0.05%。
    # 對照組的成本被系統性低估，會讓策略看起來比實際更差。
    exit_row = df.iloc[-1]
    exit_price = cost_model.slip(float(exit_row["close"]), "sell")
    exit_costs = cost_model.exit_costs(
        _cost_basis_price(exit_row, "close", "sell", exit_price, cost_model, is_futures),
        shares)
    if is_futures:
        realized = shares * (exit_price - entry_price) * point_value
    else:
        realized = shares * exit_price
    equity.iloc[-1] = cash + realized - exit_costs.total

    metrics = compute_performance_metrics(equity, initial_capital,
                                          position_value=exposure_series)
    metrics.update({
        "available": True,
        "shares": float(shares),
        "entry_price": float(entry_price),
        "exit_price": float(exit_price),
        "total_costs": float(entry_costs.total + exit_costs.total),
    })
    return metrics


def format_benchmark_line(bm: Dict[str, Any], label: str = "買進持有") -> str:
    """單行對照，供各報告直接嵌入。"""
    if not bm.get("available"):
        return f"  [{label}] 不可用：{bm.get('reason', '未知')}"
    return (f"  [{label}] 總報酬 {bm['total_return']:+.2%}｜CAGR {bm['cagr']:+.2%}｜"
            f"MDD {bm['max_drawdown']:.2%}｜Sharpe {bm['sharpe_ratio']:.2f}｜"
            f"Calmar {bm['calmar_ratio']:.2f}｜曝險 {bm.get('exposure') or 0.0:.1%}")


def compare_to_benchmark(strategy_summary: Dict[str, Any],
                         bm: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """策略對買進持有的差額。無法比較時回傳 None。

    **只回傳差額、不下判定**：孰優孰劣取決於曝險與資金用途，那是使用者的
    決定而非本函式的。呈現端須同時顯示曝險（見模組 docstring）。
    """
    if not bm.get("available") or not strategy_summary:
        return None
    return {
        "d_total_return": strategy_summary.get("total_return", 0.0) - bm["total_return"],
        "d_max_drawdown": strategy_summary.get("max_drawdown", 0.0) - bm["max_drawdown"],
        "d_sharpe": strategy_summary.get("sharpe_ratio", 0.0) - bm["sharpe_ratio"],
        "d_calmar": strategy_summary.get("calmar_ratio", 0.0) - bm["calmar_ratio"],
    }
