"""
TrendPoint - 蒙地卡羅交易序列重抽模組 (Monte Carlo Trade Resampling)

歷史回測的最大回撤只是「歷史上剛好出現的那一次」。把逐筆交易報酬
打亂重抽數千次，觀察總報酬與最大回撤的分布，95 百分位的回撤才是
真正該準備承受的風險預算。

用法:
    from monte_carlo import bootstrap_trades
    result = bootstrap_trades(summary["trade_returns"], n_sims=10000, seed=42)
"""

import numpy as np
from typing import Dict, Any, List, Optional


def _equity_path_mdd(returns: np.ndarray) -> float:
    """
    由逐筆交易報酬率建構複利淨值路徑，回傳最大回撤（負值）。
    """
    equity = np.cumprod(1.0 + returns)
    peaks = np.maximum.accumulate(equity)
    drawdowns = (equity - peaks) / peaks
    return float(drawdowns.min()) if len(drawdowns) else 0.0


def bootstrap_trades(trade_returns: List[float],
                     n_sims: int = 10000,
                     n_trades: Optional[int] = None,
                     seed: int = 42) -> Dict[str, Any]:
    """
    對逐筆交易報酬率執行有放回重抽 (Bootstrap)。

    參數:
        trade_returns (List[float]): 逐筆交易報酬率序列（小數，如 0.02 = +2%）
        n_sims (int): 模擬次數（預設 10,000 次）
        n_trades (int, optional): 每次模擬抽取的交易筆數，預設等於原始筆數
        seed (int): 隨機種子（固定以確保可重現）

    回傳:
        Dict: {
            "n_source_trades": 原始交易筆數,
            "total_return": {percentile: value},
            "max_drawdown": {percentile: value},
            "prob_loss": 總報酬為負的機率,
        }

    注意:
        交易筆數低於 30 筆時，重抽分布本身也不可靠——那不是蒙地卡羅
        能補救的問題，是樣本數的問題。此時回傳結果會帶 warning 欄位。
    """
    returns = np.asarray(trade_returns, dtype=float)
    n_source = len(returns)

    if n_source == 0:
        return {
            "n_source_trades": 0,
            "warning": "無交易紀錄，無法執行蒙地卡羅重抽。",
        }

    if n_trades is None:
        n_trades = n_source

    rng = np.random.default_rng(seed)

    total_returns = np.empty(n_sims)
    mdds = np.empty(n_sims)

    for s in range(n_sims):
        sample = rng.choice(returns, size=n_trades, replace=True)
        total_returns[s] = float(np.prod(1.0 + sample) - 1.0)
        mdds[s] = _equity_path_mdd(sample)

    percentiles = [5, 25, 50, 75, 95]

    result: Dict[str, Any] = {
        "n_source_trades": n_source,
        "n_sims": n_sims,
        "total_return": {p: float(np.percentile(total_returns, p)) for p in percentiles},
        "max_drawdown": {p: float(np.percentile(mdds, p)) for p in percentiles},
        "prob_loss": float((total_returns < 0).mean()),
    }

    if n_source < 30:
        result["warning"] = (
            f"交易樣本僅 {n_source} 筆 (<30)，任何統計推論都不可靠。"
            "請先拉長回測期間或擴大標的池，再談風險分布。"
        )

    return result


def format_monte_carlo_report(result: Dict[str, Any], title: str = "蒙地卡羅交易重抽") -> str:
    """
    將蒙地卡羅結果格式化為可讀報表。
    """
    lines = [f"========== {title} =========="]

    if result.get("n_source_trades", 0) == 0:
        lines.append(result.get("warning", "無數據"))
        return "\n".join(lines)

    lines.append(f"原始交易筆數: {result['n_source_trades']} | 模擬次數: {result['n_sims']}")

    tr = result["total_return"]
    dd = result["max_drawdown"]
    lines.append("總報酬分布   :  5%={:+.2%} | 25%={:+.2%} | 中位={:+.2%} | 75%={:+.2%} | 95%={:+.2%}".format(
        tr[5], tr[25], tr[50], tr[75], tr[95]))
    lines.append("最大回撤分布 :  5%={:.2%} | 25%={:.2%} | 中位={:.2%} | 75%={:.2%} | 95%={:.2%}".format(
        dd[5], dd[25], dd[50], dd[75], dd[95]))
    lines.append(f"虧損機率: {result['prob_loss']:.1%}")
    lines.append("→ 風險預算應以回撤分布的 5 百分位（最深一側）為準，而非歷史單一路徑。")

    if "warning" in result:
        lines.append(f"⚠ {result['warning']}")

    return "\n".join(lines)
