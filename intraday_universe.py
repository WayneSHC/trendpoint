# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 盤中評估的標的納入準則（spec 016）。

修的是 2026-08-06 探查的缺陷 3：那次的 8 檔標的是臨時選定、非
`config.data.tickers`，構成選擇偏誤。選擇偏誤污染的是**所有**後續數字，
比缺樣本外切分更根本——故本模組的唯一職責是讓「哪些標的進入評估」
成為一個可被檢驗、可被質疑、可被版本化的決定。

## 兩條不變式，改動前請先想清楚

1. **輸入僅限 lookback 期間的 OHLCV**。任何回測、訊號、績效輸入即違反
   FR-010。由 `tests/test_intraday_universe.py::test_perturbation_insensitive`
   守住——人為改變回測輸出後納入清單必須不變。

2. **lookback 位於評估窗之前且不重疊**（research.md R5）。FR-010 只禁止
   引用回測產出，但那不夠：用評估窗自身的資料判定納入（例如「這段期間
   日均量夠大」），形式上不算回測產出，實質上仍受評估期間市況影響——
   那是選擇偏誤的時序版本。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 準則項識別。報告的 failed_criteria 一律用這些鍵，不用人類可讀句子——
# 下游要能把「未達哪一項」對回組態中的具體門檻。
CRITERION_VOLUME = "min_avg_daily_volume"
CRITERION_GAP = "max_gap_ratio"
CRITERION_CV = "max_bars_per_day_cv"
CRITERION_TICK = "max_tick_ratio"
CRITERION_LOOKBACK = "insufficient_lookback"
CRITERION_EXCLUDED = "explicitly_excluded"
ALL_CRITERIA = (
    CRITERION_VOLUME,
    CRITERION_GAP,
    CRITERION_CV,
    CRITERION_TICK,
    CRITERION_LOOKBACK,
    CRITERION_EXCLUDED,
)


@dataclass
class UniverseDecision:
    ticker: str
    included: bool
    failed_criteria: List[str]
    measured: Dict[str, Optional[float]]

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "included": self.included,
            "failed_criteria": sorted(self.failed_criteria),
            "measured": {k: self.measured[k] for k in sorted(self.measured)},
        }


def criteria_version(cfg_ie) -> str:
    """由門檻值集合導出穩定識別。門檻改變即改版（FR-012）。

    版本識別存在的理由：門檻改了而版本沒改，新舊結論就會被誤當成同一
    條件下的對照——那是最容易發生、也最難事後察覺的一類錯誤。
    """
    payload = "|".join(str(x) for x in [
        cfg_ie.lookback_days,
        cfg_ie.min_avg_daily_volume,
        cfg_ie.max_gap_ratio,
        cfg_ie.max_bars_per_day_cv,
        cfg_ie.max_tick_ratio,
        sorted(cfg_ie.excluded_tickers),
    ])
    return "v1-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def split_lookback_and_eval(
    df: pd.DataFrame, lookback_days: int
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """切出「評估窗之前」的 lookback 與其後的評估窗，兩者**不重疊**。

    lookback 不足（總交易日數 <= lookback_days）時回傳 (None, None)：
    此時沒有任何合法的判定資料，該標的必須被排除而非以評估窗資料代打。
    """
    if df is None or len(df) == 0:
        return None, None
    days = sorted({ts.date() for ts in df.index})
    if len(days) <= lookback_days:
        return None, None
    lookback_days_set = set(days[:lookback_days])
    lb_mask = np.array([ts.date() in lookback_days_set for ts in df.index])
    return df[lb_mask].copy(), df[~lb_mask].copy()


# ---------------------------------------------------------------------------
# 四個維度的量測。全部只吃 OHLCV，且全部是資料體質／市場結構的函式——
# 沒有一個牽涉報酬、勝率或訊號數。這條分界線是 SC-005 能通過的原因。
# ---------------------------------------------------------------------------


def avg_daily_volume(df: pd.DataFrame) -> float:
    per_day = df["volume"].groupby(df.index.date).sum()
    return float(per_day.mean()) if len(per_day) else 0.0


def gap_ratio(df: pd.DataFrame) -> float:
    """盤中缺口根數比率：以每日根數中位數為基準的缺額佔比。"""
    per_day = pd.Series(1, index=df.index).groupby(df.index.date, sort=True).sum()
    if len(per_day) == 0:
        return 1.0
    median = float(per_day.median())
    if median <= 0:
        return 1.0
    missing = float(np.clip(median - per_day.values, 0, None).sum())
    return missing / (median * len(per_day))


def bars_per_day_cv(df: pd.DataFrame) -> float:
    per_day = pd.Series(1, index=df.index).groupby(df.index.date, sort=True).sum()
    if len(per_day) < 2 or per_day.mean() == 0:
        return 0.0
    return float(per_day.std(ddof=0) / per_day.mean())


def tick_ratio(df: pd.DataFrame) -> float:
    """最小非零價差 / 中位價。檔位過粗代表價格離散，日內訊號會失真。"""
    prices = np.sort(pd.unique(df["close"].values))
    if len(prices) < 2:
        return 1.0
    diffs = np.diff(prices)
    diffs = diffs[diffs > 0]
    if len(diffs) == 0:
        return 1.0
    median_price = float(np.median(df["close"].values))
    if median_price <= 0:
        return 1.0
    return float(diffs.min() / median_price)


def measure(df: pd.DataFrame) -> Dict[str, float]:
    """四個維度的實測值。報告會原樣呈現，供讀者自行檢驗判定是否合理。"""
    return {
        "avg_daily_volume": round(avg_daily_volume(df), 4),
        "gap_ratio": round(gap_ratio(df), 6),
        "bars_per_day_cv": round(bars_per_day_cv(df), 6),
        "tick_ratio": round(tick_ratio(df), 8),
        "lookback_trading_days": int(pd.Series(df.index.date).nunique()),
    }


def apply_criteria(ticker: str, df: Optional[pd.DataFrame], cfg_ie) -> UniverseDecision:
    """對單一標的套用準則。`df` 應為 **lookback 段**，非評估段。

    顯式排除清單先行：槓桿／反向 ETF 的複利路徑相依性使其日內報酬與一般
    現貨不可比，混入會讓跨標的離散度失去意義（research.md R5）。
    """
    if ticker in set(cfg_ie.excluded_tickers):
        return UniverseDecision(ticker, False, [CRITERION_EXCLUDED], {})
    if df is None or len(df) == 0:
        return UniverseDecision(ticker, False, [CRITERION_LOOKBACK], {})

    m = measure(df)
    failed: List[str] = []
    if m["avg_daily_volume"] < cfg_ie.min_avg_daily_volume:
        failed.append(CRITERION_VOLUME)
    if m["gap_ratio"] > cfg_ie.max_gap_ratio:
        failed.append(CRITERION_GAP)
    if m["bars_per_day_cv"] > cfg_ie.max_bars_per_day_cv:
        failed.append(CRITERION_CV)
    if m["tick_ratio"] > cfg_ie.max_tick_ratio:
        failed.append(CRITERION_TICK)

    return UniverseDecision(ticker, not failed, failed, m)


def build_universe(
    histories: Dict[str, pd.DataFrame], cfg_ie
) -> Tuple[List[UniverseDecision], Dict[str, pd.DataFrame]]:
    """對一組標的套用準則，回傳 (逐標的決定, 通過者的**評估段**)。

    回傳評估段而非完整歷史，是為了讓下游不可能誤用 lookback 段——
    介面上拿不到，就不會有人不小心把判定資料也拿去評估。
    """
    decisions: List[UniverseDecision] = []
    included: Dict[str, pd.DataFrame] = {}
    for ticker in sorted(histories):
        lookback, evaluation = split_lookback_and_eval(
            histories[ticker], cfg_ie.lookback_days
        )
        decision = apply_criteria(ticker, lookback, cfg_ie)
        decisions.append(decision)
        if decision.included and evaluation is not None and len(evaluation):
            included[ticker] = evaluation
    return decisions, included
