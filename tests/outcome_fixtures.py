# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - spec 015 事後表現追蹤的合成資料產生器（T001）。

本案的驗收需要**精確控制**「假日缺口」「T+5 尚未到期」「推播失敗」
「同一根 K 線重複偵測」等邊界情境，真實市場資料無法保證這些一定出現在
測試窗內（見 specs/015-alert-outcome-tracking/quickstart.md §5）。

低階建構子（`frame_from_closes`／`intraday_index`）重用 spec 014 的
`tests/ma_fixtures.py`——同一個 repo 不需要兩套 OHLCV 產生器。

所有產生器皆為決定性：給定相同參數恆得相同輸出，不使用隨機數。
"""

from typing import Dict, List, Optional, Sequence

import pandas as pd

from tests.ma_fixtures import (  # noqa: F401  (frame_from_closes 供本模組與測試共用)
    frame_from_closes,
    intraday_index,
    trending_intraday_frame,
)


def daily_with_gaps(closes: Sequence[float],
                    dates: Sequence[str]) -> pd.DataFrame:
    """
    以**明確指定的日期**建構日線序列——供驗證 T+N 取的是「表中實際存在的列」
    而非日曆日。

    範例：`dates=["2026-08-05", "2026-08-06", "2026-08-07", "2026-08-10"]`
    中間缺 08-08／08-09（週末），故告警日 08-05 的 T+3 應為 **08-10**，
    而非日曆意義的 08-08。
    """
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates)), name="datetime")
    return frame_from_closes(list(closes), idx)


def daily_linear(n: int,
                 start: str = "2026-08-03",
                 base: float = 100.0,
                 step: float = 1.0) -> pd.DataFrame:
    """
    n 個交易日（工作日）的等差日線序列。

    `n` 刻意可調小：末端不足 5 根前瞻時，T+5 必須維持「未回填」而非填 0
    ——這是 SC-014 的三態驗收（FR-014）。
    """
    idx = pd.bdate_range(start=start, periods=n, name="datetime")
    return frame_from_closes([base + step * i for i in range(n)], idx)


def make_record(ticker: str = "TEST.TW",
                bar_time: str = "2026-08-05 09:30:00",
                alert_type: str = "BULLISH_MSS",
                timeframe: str = "5m",
                close: float = 100.0,
                direction: Optional[int] = None,
                notified: bool = False,
                outcomes: Optional[Dict] = None,
                param_fingerprint: str = "sp10_fvg1_fl3_sn2_vm1.5_bv0_bvm1.5_bvp20",
                ) -> Dict:
    """
    直接構造一筆紀錄（繞過 monitor），供純函式與儲存層測試使用。

    欄位集合必須與 `alert_outcomes.RECORD_FIELDS` 一致——若該白名單變動而
    本函式未同步，SC-006 會失敗，這是刻意的耦合（白名單是契約）。
    """
    import alert_outcomes as ao

    return {
        "ticker": ticker,
        "bar_time": bar_time,
        "alert_type": alert_type,
        "direction": ao.direction_for(alert_type) if direction is None else direction,
        "timeframe": timeframe,
        "close": close,
        "ladder": 98.0,
        "upper_price": 101.0,
        "lower_price": 97.0,
        "atr": 2.0,
        "param_fingerprint": param_fingerprint,
        "notified": notified,
        "detected_at": "2026-08-05T09:35:00",
        "outcomes": dict(outcomes or {}),
    }


def records_for_summary(n: int,
                        alert_type: str = "BULLISH_MSS",
                        timeframe: str = "5m",
                        rets: Optional[Sequence[float]] = None) -> List[Dict]:
    """
    產生 n 筆**已回填** t1 的紀錄，供 `summarize` 的樣本量門檻測試。

    `rets` 未給時以固定序列產生（決定性）：正負交錯，使勝率可精確預期。
    """
    values = list(rets) if rets is not None else [
        0.01 if i % 2 == 0 else -0.01 for i in range(n)
    ]
    out = []
    for i, ret in enumerate(values):
        rec = make_record(
            bar_time=f"2026-08-{(i % 20) + 1:02d} 09:{(i % 50) + 5:02d}:00",
            alert_type=alert_type,
            timeframe=timeframe,
        )
        rec["outcomes"] = {
            "t1": {"date": "2026-08-06", "close": rec["close"] * (1 + ret),
                   "ret": ret, "ret_adj": ret * rec["direction"]}
        }
        out.append(rec)
    return out
