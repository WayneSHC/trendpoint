# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 進場閘門的風控元件 (Risk Gates, spec 013)

本模組為**路徑相依的風控元件**，與 `ladder_system.py` 的無狀態指標層刻意分離。

差別不是風格問題而是本質問題：指標是 DataFrame 的函數（同一根的值不依賴計算
順序，可向量化、可 `.shift(1)` 檢查），而回撤閘門是**權益路徑的函數**——它讀
自己造成的結果（封鎖 → 不進場 → 權益不變 → 維持封鎖），構成回饋迴路，無法
用向量化指標表達。把它塞進指標層會讓「看前偏誤只需檢查 shift」這條簡單規則
失效；獨立成模組，並以呼叫順序契約（見 `DrawdownGate.update` docstring）把
時序責任明確交給呼叫端。

本模組**不得** import `backtester` / `ladder_system`（單向依賴），亦無任何 I/O
與 config 讀取——所有參數由呼叫端注入，故可完全獨立單元測試。
"""

import datetime as dt
from bisect import bisect_left
from typing import Set

import pandas as pd

__all__ = ["settlement_days", "third_wednesday_of", "DrawdownGate"]


def third_wednesday_of(year: int, month: int) -> dt.date:
    """該年月的第三個週三（台指期結算日的日曆定義）。"""
    first = dt.date(year, month, 1)
    offset = (2 - first.weekday()) % 7            # weekday(): 週三 = 2
    return first + dt.timedelta(days=offset + 14)


def settlement_days(index: pd.DatetimeIndex) -> Set[dt.date]:
    """回傳索引涵蓋之各月的台指期結算日（date 集合）。

    定義：該月第三個週三；若該日不在索引的交易日集合中（假日／停市），
    取**其後第一個交易日**。日內索引先取 `.date()` 去重，故同一日的所有棒
    共用同一判定。

    刻意不引入外部交易日曆套件、也不硬編碼結算日清單：
    前者使離線 CI 失效，後者違反憲章原則 V（參數集中化）。索引本身就是
    這份資料的交易日曆——這是唯一不需要外部真值的來源。

    邊界：
    - 該月第三個週三之後索引已無任何交易日（資料尾端截斷）→ 該月不列入，不拋錯。
    - 索引起點晚於當月第三個週三時，按上述規則仍會取到起點後的第一個交易日。
      這是「取其後第一個交易日」的字面結果；本函式不猜測資料窗外發生過什麼。
    """
    dates = sorted({ts.date() for ts in index})
    if not dates:
        return set()

    out: Set[dt.date] = set()
    for year, month in sorted({(d.year, d.month) for d in dates}):
        target = third_wednesday_of(year, month)
        i = bisect_left(dates, target)
        if i < len(dates):                        # 之後仍有交易日才成立
            out.add(dates[i])
    return out


class DrawdownGate:
    """回撤閘門狀態機：權益自峰值回落達門檻時封鎖開新倉，回升至恢復門檻時解除。

    兩個門檻之間為**遲滯區**（hysteresis）：區間內維持原狀態。若只用單一門檻，
    權益在門檻附近震盪會使閘門逐根翻動，交易與否取決於小數點後幾位。

    狀態轉移（data-model.md §1）::

        dd = (equity - peak) / peak          # <= 0
        OPEN    → BLOCKED : dd <= -limit_pct
        BLOCKED → OPEN    : dd >= -resume_pct
        其餘維持原狀態

    ### 呼叫順序契約（憲章原則 I 的落點，最關鍵）

    每根迴圈：

    1. **開頭**讀 `blocked`（反映第 i-1 根為止的權益）→ 參與本根進場判定
    2. **尾端**以本根權益呼叫 `update()`

    **禁止**在同一根內先 `update()` 再讀 `blocked`——那會讓進場判定用到當根
    權益（而當根權益含當根收盤價），構成看前偏誤。`tests/test_lookahead_bias.py`
    的 SC-004 專門守此點。
    """

    def __init__(self, initial_equity: float, limit_pct: float, resume_pct: float):
        if not (0.0 <= resume_pct < limit_pct):
            raise ValueError(
                f"回撤閘門門檻不合法：resume_pct={resume_pct} 必須 >= 0 且嚴格小於 "
                f"limit_pct={limit_pct}。兩者相等會使閘門在門檻附近逐根翻動"
                "（flapping），遲滯區間存在的意義即在於此。"
            )
        self.limit_pct = float(limit_pct)
        self.resume_pct = float(resume_pct)
        self.peak = float(initial_equity)
        self._blocked = False

    @property
    def blocked(self) -> bool:
        """是否封鎖開新倉——反映**最後一次 `update()` 為止**的狀態。"""
        return self._blocked

    def drawdown(self, equity: float) -> float:
        """相對歷史峰值的回撤（<= 0）；peak <= 0 時回傳 0.0（防除零）。"""
        if self.peak <= 0.0:
            return 0.0
        return (float(equity) - self.peak) / self.peak

    def update(self, equity: float) -> None:
        """以**當根**權益更新峰值與封鎖狀態。必須在每根迴圈尾端呼叫。"""
        eq = float(equity)
        if eq > self.peak:
            self.peak = eq

        dd = self.drawdown(eq)
        if self._blocked:
            if dd >= -self.resume_pct:
                self._blocked = False
        else:
            if dd <= -self.limit_pct:
                self._blocked = True
