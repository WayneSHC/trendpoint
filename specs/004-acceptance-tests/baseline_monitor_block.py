# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 004 T003：monitor_signals.py 重構前指標組裝區塊的逐字凍結副本。

`compute_monitor_block()` 是 2026-07-12 重構前 monitor_signals.check_new_signals
第 117-141 行的原樣搬運（僅把 `ladder_system.` 前綴保留、輸入改為參數傳入）。
T007 迴歸閘門用它與 build_indicator_frame(include_regime=False) 在同一份
固定合成 df 上做零差異比對。本檔為歷史凍結件，重構合併後不再修改。
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import ladder_system  # noqa: E402


def compute_monitor_block(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # 2. 計算技術指標與多空訊號
    # 真實波幅與 ATR
    tr = ladder_system.calculate_tr(df['high'], df['low'], df['close'])
    df['atr'] = ladder_system.calculate_atr(tr, period=14)
    df['vwap'] = ladder_system.calculate_vwap(df)

    # 結構訊號
    mss, bos = ladder_system.detect_market_structure(df, period=10)
    df['mss'] = mss
    df['bos'] = bos

    # 多空階梯軌跡
    df['ladder'] = ladder_system.calculate_ladder_levels(df, df['atr'], k=2.0)

    # 計算今日三關價
    df['date'] = df.index.date
    daily_ohlcv = df.groupby('date').agg({'high': 'max', 'low': 'min'})
    yesterday_ohlcv = daily_ohlcv.shift(1)

    df['yesterday_high'] = df['date'].map(yesterday_ohlcv['high']).fillna(df['high'].iloc[0])
    df['yesterday_low'] = df['date'].map(yesterday_ohlcv['low']).fillna(df['low'].iloc[0])
    df['mid_price'] = (df['yesterday_high'] + df['yesterday_low']) / 2.0
    df['diff'] = df['yesterday_high'] - df['yesterday_low']
    df['upper_price'] = df['yesterday_low'] + df['diff'] * 1.382
    df['lower_price'] = df['yesterday_high'] - df['diff'] * 1.382
    return df


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tests"))
    from acceptance_fixtures import make_klines

    frame = compute_monitor_block(make_klines(600, "5min"))
    cols = ["atr", "vwap", "mss", "bos", "ladder",
            "mid_price", "upper_price", "lower_price"]
    print(frame[cols].tail(3).to_string())
    print(f"checksum: {pd.util.hash_pandas_object(frame[cols].dropna()).sum()}")
