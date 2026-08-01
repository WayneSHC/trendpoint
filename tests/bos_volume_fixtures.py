# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - spec 012 BOS 量能確認濾網的合成序列產生器（T001）。

本案驗收要求序列同時含「BOS 成立但量能萎縮」與「BOS 成立且量能放大」兩類進場
（SC-003），真實市場資料無法保證兩者都落在測試窗內，故一律以合成序列驗收
（見 specs/012-bos-volume-confirmation/quickstart.md A 段）。

價格與量能直接沿用 `acceptance_fixtures.make_klines`（repo 既有的固定 seed
隨機漫步，量能為 lognormal(13.0, 0.4)——σ=0.4 使量能自然分布於
1.5× 均量門檻的兩側）。本模組只做 **seed 與長度的挑選**，不自行造價。

## 挑選依據（離線窮舉，非任意值）

`seed=23` / `n=600` 日線：預設參數下有 **7 筆進場**，其中 **1 筆判定根的量能
超過 1.5× 20 日均量、6 筆未達**。這個比例是本案要的——濾網啟用後多數進場
應被擋下（有鑑別力），但仍留有通過者（證明擋的是量能而非全部）。

若改動此 fixture，`tests/fixtures/012_baseline_*` 必須在**未改碼**狀態下重新
凍結，否則 SC-001 的比對對象就不再是「實作前行為」。
"""

import pandas as pd

from acceptance_fixtures import make_klines, with_unadj


# 與 contracts/bos-volume-filter.md §1 的預設值一致；fixture 的挑選以此為前提
DEFAULT_PERIOD = 20
DEFAULT_MULT = 1.5


def daily_klines(n: int = 600, seed: int = 23) -> pd.DataFrame:
    """主 fixture：≥400 根日線，含量能足與量能不足兩類 BOS 進場。"""
    return make_klines(n, freq="1D", seed=seed)


def mss_reversal_klines(n: int = 800, seed: int = 71) -> pd.DataFrame:
    """SC-008 專用：以 `mss_reversal_entry=True` 執行時會產生 **2 筆 MSS 反轉進場**，
    且兩筆判定根的量能**皆未達** 1.5× 門檻。

    這正是 FR-005 要證的情境——反轉分支不套用本濾網，故這兩筆在濾網啟用後
    仍須成立。主 fixture（`daily_klines`）在預設參數下沒有 MSS 反轉進場，
    因此另備此序列，而非把主 fixture 調成兩用（會犧牲其量能鑑別力）。
    """
    return make_klines(n, freq="1D", seed=seed)


def futures_daily_klines(n: int = 600, seed: int = 23) -> pd.DataFrame:
    """期貨版（補 spec 011 未調整參考價欄位），供空方鏡像對稱測試使用。"""
    return with_unadj(daily_klines(n, seed))


def expected_volume_ok(df: pd.DataFrame,
                       period: int = DEFAULT_PERIOD,
                       mult: float = DEFAULT_MULT) -> pd.Series:
    """量能確認的**獨立**參考實作，供測試與 `ladder_system` 的實作互為對照。

    刻意不從 `ladder_system` import——兩份實作若共用同一段程式碼，測試就只是在
    確認「函式等於它自己」。此處逐字依 contracts §1 的定義書寫。
    """
    vol_ma = df["volume"].rolling(period).mean().shift(1)
    return (vol_ma.notna() & (vol_ma > 0) & (df["volume"] > vol_ma * mult))
