"""
TrendPoint - 量化績效指標模組 (Performance Analytics)

本模組提供資產管理級別的完整績效報表，僅看總報酬與 MDD 是不夠的：
1. 年化報酬 (CAGR)、年化波動率
2. 風險調整後報酬：Sharpe、Sortino、Calmar
3. 回撤分析：最大回撤 (MDD)、最長回撤持續期間
4. 曝險時間 (Exposure)：資金實際暴露於市場的時間比例
5. 滾動 Sharpe（檢驗績效穩定度，避免單一期間運氣撐起全部報酬）

所有指標皆由淨值曲線推導，與回測引擎解耦，可同時服務單標的與組合回測。
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

TRADING_DAYS_PER_YEAR = 252

def infer_periods_per_year(index: pd.DatetimeIndex) -> float:
    """
    依時間索引的中位數間隔推斷年化倍率（日線 252、5 分線約 252*54 等）。
    """
    if len(index) < 3:
        return float(TRADING_DAYS_PER_YEAR)

    median_interval = pd.Series(index).diff().median()
    if median_interval >= pd.Timedelta(days=1):
        return float(TRADING_DAYS_PER_YEAR)

    # 日內數據：以每日 K 線根數估算（台股一般交易時段 4.5 小時）
    bars_per_day = pd.Timedelta(hours=4.5) / median_interval
    return float(TRADING_DAYS_PER_YEAR * max(bars_per_day, 1.0))

def max_drawdown_stats(equity: pd.Series) -> Dict[str, Any]:
    """
    計算最大回撤深度與最長回撤持續期間（從前高到回到前高的最長時間）。
    """
    peaks = equity.cummax()
    drawdowns = (equity - peaks) / peaks
    mdd = float(drawdowns.min()) if len(drawdowns) else 0.0

    # 回撤持續期間：處於水下 (equity < 前高) 的最長連續區段
    underwater = equity < peaks
    longest = 0
    current = 0
    for is_under in underwater.values:
        if is_under:
            current += 1
            longest = max(longest, current)
        else:
            current = 0

    duration_days = None
    if isinstance(equity.index, pd.DatetimeIndex) and len(equity) > 1:
        median_interval = pd.Series(equity.index).diff().median()
        duration_days = float(longest * (median_interval / pd.Timedelta(days=1)))

    return {
        "max_drawdown": mdd,
        "max_underwater_bars": int(longest),
        "max_underwater_days": duration_days,
    }

def compute_performance_metrics(equity: pd.Series,
                                initial_capital: float,
                                position_value: Optional[pd.Series] = None,
                                periods_per_year: Optional[float] = None,
                                risk_free_rate: float = 0.0) -> Dict[str, Any]:
    """
    由淨值曲線計算完整績效指標。

    參數:
        equity (pd.Series): 淨值曲線（DatetimeIndex）
        initial_capital (float): 初始資金
        position_value (pd.Series, optional): 部位市值序列，用於計算曝險時間
        periods_per_year (float, optional): 年化倍率，未指定時自動推斷
        risk_free_rate (float): 年化無風險利率（預設 0）

    回傳:
        Dict: 完整績效指標
    """
    if equity is None or len(equity) < 2:
        return {}

    equity = equity.astype(float)

    if periods_per_year is None:
        periods_per_year = infer_periods_per_year(equity.index)

    returns = equity.pct_change().dropna()

    final_equity = float(equity.iloc[-1])
    total_return = (final_equity - initial_capital) / initial_capital

    # 年化報酬 (CAGR)
    n_periods = len(returns)
    years = n_periods / periods_per_year
    if years > 0 and final_equity > 0 and initial_capital > 0:
        cagr = (final_equity / initial_capital) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    # 年化波動率
    period_vol = float(returns.std(ddof=1)) if n_periods > 1 else 0.0
    annual_vol = period_vol * np.sqrt(periods_per_year)

    # Sharpe Ratio（以期間報酬計算後年化）
    rf_per_period = risk_free_rate / periods_per_year
    excess = returns - rf_per_period
    if period_vol > 0:
        sharpe = float(excess.mean()) / period_vol * np.sqrt(periods_per_year)
    else:
        sharpe = 0.0

    # Sortino Ratio（僅以下行波動為分母）
    downside = excess[excess < 0]
    downside_vol = float(np.sqrt((downside ** 2).mean())) if len(downside) > 0 else 0.0
    if downside_vol > 0:
        sortino = float(excess.mean()) / downside_vol * np.sqrt(periods_per_year)
    else:
        sortino = np.inf if float(excess.mean()) > 0 else 0.0

    # 回撤分析
    dd_stats = max_drawdown_stats(equity)
    mdd = dd_stats["max_drawdown"]

    # Calmar Ratio = 年化報酬 / |MDD|
    calmar = cagr / abs(mdd) if mdd < 0 else (np.inf if cagr > 0 else 0.0)

    # 曝險時間：實際持有部位的 K 線比例（交易次數少且曝險低代表資金大量閒置）
    exposure = None
    if position_value is not None and len(position_value) > 0:
        exposure = float((position_value.astype(float) > 0.0).mean())

    return {
        "total_return": total_return,
        "cagr": float(cagr),
        "annual_volatility": float(annual_vol),
        "sharpe_ratio": float(sharpe),
        "sortino_ratio": float(sortino),
        "calmar_ratio": float(calmar),
        "max_drawdown": mdd,
        "max_underwater_bars": dd_stats["max_underwater_bars"],
        "max_underwater_days": dd_stats["max_underwater_days"],
        "exposure": exposure,
        "periods_per_year": float(periods_per_year),
        "n_periods": int(n_periods),
    }

def rolling_sharpe(equity: pd.Series, window: int = 126, periods_per_year: Optional[float] = None) -> pd.Series:
    """
    滾動 Sharpe Ratio：檢驗績效是否來自全期間的穩定貢獻，
    而非單一幸運區段（過擬合系統最常見的特徵）。
    """
    if periods_per_year is None:
        periods_per_year = infer_periods_per_year(equity.index)

    returns = equity.astype(float).pct_change()
    roll_mean = returns.rolling(window).mean()
    roll_std = returns.rolling(window).std(ddof=1)
    return (roll_mean / roll_std.replace(0, np.nan)) * np.sqrt(periods_per_year)

def format_performance_report(metrics: Dict[str, Any], title: str = "績效報表") -> str:
    """
    將績效指標格式化為可讀報表字串。
    """
    if not metrics:
        return f"[{title}] 無足夠數據生成報表"

    lines = [f"========== {title} =========="]
    fmt = [
        ("總報酬率", "total_return", "{:+.2%}"),
        ("年化報酬 (CAGR)", "cagr", "{:+.2%}"),
        ("年化波動率", "annual_volatility", "{:.2%}"),
        ("Sharpe Ratio", "sharpe_ratio", "{:.2f}"),
        ("Sortino Ratio", "sortino_ratio", "{:.2f}"),
        ("Calmar Ratio", "calmar_ratio", "{:.2f}"),
        ("最大回撤 (MDD)", "max_drawdown", "{:.2%}"),
        ("最長水下期間 (天)", "max_underwater_days", "{:.0f}"),
        ("市場曝險時間", "exposure", "{:.1%}"),
    ]
    for label, key, f in fmt:
        val = metrics.get(key)
        if val is None:
            continue
        if isinstance(val, float) and np.isinf(val):
            lines.append(f"{label:<18}: inf")
        else:
            lines.append(f"{label:<18}: {f.format(val)}")
    return "\n".join(lines)
