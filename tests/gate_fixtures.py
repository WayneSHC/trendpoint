# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - spec 013 進場閘門的合成序列產生器（T001）。

本案驗收需要**精確控制**權益路徑與日期結構：回撤跨過封鎖門檻、回撤回升解除
封鎖、封鎖期間持倉觸發停損、索引含／缺當月第三個週三。真實市場資料無法保證
這些邊界同時出現在測試窗內，故一律以合成序列驗收
（見 specs/013-entry-gate-risk-limits/quickstart.md A 段）。

全部產生器皆為決定性：固定 seed、無隨機呼叫、無 I/O。所有價格序列直接沿用
`acceptance_fixtures.make_klines`（repo 既有的固定 seed 隨機漫步），本模組只做
**索引重排**與**參數挑選**——這兩件事才是本案的變因。

## 各變體的挑選依據（皆為離線窮舉挑出，非任意值）

| 變體 | 產生器 | 挑選依據 |
|---|---|---|
| (a) | `losing_then_recovering_klines` | seed=7 是少數在 dd 門檻 4%／恢復 3% 下呈現 **封鎖→解除→封鎖→解除→封鎖** 完整循環、且解除後仍有後續進場的序列；多數 seed 只會單向封鎖後鎖死 |
| (b) | `blocked_with_open_position_klines` | seed=3 在 dd 門檻 1%／恢復 0.5% 下，於**閘門已封鎖且持倉未平**時由停損出場（第 121 根）——正是 SC-003 要守的情境 |
| (c) | `futures_daily_frame()` | seed=23 / shift=0 下有 **2 筆進場恰落在結算日**（2024-12-18、2025-10-15），使結算日閘門的效果可被觀察，而非「本來就沒進場」 |
| (d) | `futures_daily_frame(drop_settlement=...)` | 由 (c) 的索引刻意抽掉某月第三個週三，驗證封鎖日後推 |

## 關於回撤閘門的一個結構性性質（實作時發現，記於此以免後人誤判測試設計）

單標的、且權益峰值自回測起點累計時，**空手且已封鎖的帳戶無法自行恢復**：
沒有部位就沒有權益變動，回撤永遠停在封鎖時的水位。因此
`dd_resume_pct` 的解除路徑只在「封鎖發生時仍持有部位、其後未實現損益回升」
才走得到。變體 (a) 即為此情境；變體 (b) 則是另一半（封鎖後鎖死）。
兩個轉折無法在同一段「空手窗」中同時觀察，測試因此拆成兩條路徑。
"""

from typing import Optional

import pandas as pd

from acceptance_fixtures import make_klines, with_unadj


def losing_then_recovering_klines(n: int = 3000) -> pd.DataFrame:
    """變體 (a)：含連續虧損段與其後回升段的 5 分線序列。

    兩組門檻各驗一件事（皆已離線確認）：

    - `dd_limit_pct=0.04` / `dd_resume_pct=0.03`：走出 封鎖→解除→封鎖→解除→封鎖
      的完整循環，且解除之後仍有後續進場——「恢復進場」可被觀察的前提。
    - `dd_limit_pct=0.02` / `dd_resume_pct=0.005`：封鎖後鎖死，進場數 10 → 1，
      封鎖效果的鑑別力最強。

    亦為 T002 基準凍結（`tests/fixtures/013_baseline_*.csv`）所用的序列。
    """
    return make_klines(n, freq="5min", seed=7)


def blocked_with_open_position_klines(n: int = 1500) -> pd.DataFrame:
    """變體 (b)：封鎖發生時仍持有部位、其後由停損出場（SC-003 的守門情境）。

    搭配 `dd_limit_pct=0.01` / `dd_resume_pct=0.005`：閘門於持倉期間封鎖，
    該部位隨後在**封鎖狀態下**觸發停損離場（第 121 根）。
    """
    return make_klines(n, freq="5min", seed=3)


def _daily_index(n: int, shift_days: int = 0) -> pd.DatetimeIndex:
    """自 2024-01-02 起的 n 個工作日索引（可整體位移，用於挑選結算日對齊）。"""
    start = pd.Timestamp("2024-01-02") + pd.Timedelta(days=shift_days)
    return pd.bdate_range(start=start, periods=n, name="datetime")


def third_wednesday(year: int, month: int) -> pd.Timestamp:
    """該年月的第三個週三（純日曆推算，與 risk_gates 的實作互為獨立對照）。"""
    first = pd.Timestamp(year=year, month=month, day=1)
    offset = (2 - first.dayofweek) % 7          # 週三 = 2
    return first + pd.Timedelta(days=offset + 14)


def futures_daily_frame(n: int = 700,
                        seed: int = 23,
                        shift_days: int = 0,
                        drop_settlement: Optional[str] = None) -> pd.DataFrame:
    """變體 (c)/(d)：期貨日線序列（含 spec 011 未調整參考價欄位）。

    參數:
        drop_settlement: 形如 "2024-06" 的年月；給定時把該月第三個週三**自索引抽掉**
                         （模擬該日為假日），供 SC-007 驗證封鎖日後推至次一交易日。

    價格序列與索引刻意解耦：`make_klines` 產生價格，索引另行指定。位移索引不改變
    價格路徑（進場點的「第幾根」不變），只改變其落在哪一天——這正是挑選
    「進場恰落在結算日」所需的自由度。
    """
    base = make_klines(n, freq="1D", seed=seed)
    idx = _daily_index(n, shift_days)

    if drop_settlement is not None:
        year, month = (int(x) for x in drop_settlement.split("-"))
        target = third_wednesday(year, month)
        keep = idx != target
        if keep.all():
            raise ValueError(f"{drop_settlement} 的第三個週三 {target.date()} 不在索引內，無從抽掉")
        idx = idx[keep]
        base = base.iloc[: len(idx)]
        idx = idx[: len(base)]

    return with_unadj(base.set_axis(idx))
