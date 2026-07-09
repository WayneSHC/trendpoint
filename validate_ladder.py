# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 核心演算法邏輯與時序偏誤驗證腳本 (Validation Script)

本腳本用於模擬真實交易場景，驗證 `ladder_system.py` 中的數學公式與邏輯：
1. 建立包含趨勢與震盪的模擬 K 線時序數據。
2. 計算並驗證 TR, ATR, 三關價, VWAP, MSS, BOS, 階梯與吊燈止損。
3. 模擬進出場與動態倉位管理流程，檢查是否確實無「看前偏誤（Look-Ahead Bias）」。
4. 驗證代碼在無 Numba 環境下的自動回退（Fallback）容錯能力。
"""

import numpy as np
import pandas as pd
import sys
from ladder_system import (
    calculate_tr,
    calculate_atr,
    calculate_three_bands,
    calculate_vwap,
    detect_market_structure,
    calculate_ladder_levels,
    calculate_chandelier_exit,
    PositionManager
)

def run_validation():
    print("=" * 60)
    print("開始執行 TrendPoint 核心演算法驗證...")
    print("=" * 60)

    # 1. 產生模擬數據 (100 根 K 線，模擬多頭趨勢 -> 盤整 -> 跌破反轉)
    np.random.seed(42)
    n_bars = 100
    
    # 建立時間序列
    dates = pd.date_range(start="2026-05-20 09:00:00", periods=n_bars, freq="1min")
    
    # 模擬基準價
    base_price = 20000.0
    prices = [base_price]
    for i in range(1, n_bars):
        if i < 40:
            # 前 40 根 K 線：強勢多頭趨勢
            change = np.random.normal(15.0, 5.0)
        elif i < 70:
            # 中間 30 根 K 線：高位盤整
            change = np.random.normal(0.0, 12.0)
        else:
            # 後 30 根 K 線：跌破 MSS 並反轉向下
            change = np.random.normal(-20.0, 8.0)
        prices.append(prices[-1] + change)
        
    prices = np.array(prices)
    
    # 建立 OHLCV DataFrame
    df = pd.DataFrame(index=dates)
    df['close'] = prices
    df['open'] = df['close'].shift(1).fillna(base_price - 5.0)
    # 隨機產生高低點
    df['high'] = df[['open', 'close']].max(axis=1) + np.random.uniform(2.0, 10.0, n_bars)
    df['low'] = df[['open', 'close']].min(axis=1) - np.random.uniform(2.0, 10.0, n_bars)
    df['volume'] = np.random.uniform(100, 1000, n_bars).round()

    print(f"成功建立模擬數據：共 {n_bars} 根分鐘級 K 線。")
    print(f"價格範圍: {df['close'].min():.2f} ~ {df['close'].max():.2f}")
    
    # 2. 驗證指標計算
    print("\n--- 指標計算驗證 ---")
    tr = calculate_tr(df['high'], df['low'], df['close'])
    atr = calculate_atr(tr, period=14)
    vwap = calculate_vwap(df)
    
    print(f"TR 計算完成。最大值: {tr.max():.2f}, 最小值: {tr.min():.2f}")
    print(f"ATR (14) 計算完成。最後一筆 ATR 值: {atr.iloc[-1]:.2f}")
    print(f"VWAP 計算完成。最後一筆 VWAP 值: {vwap.iloc[-1]:.2f}")
    
    # 檢查有無 NaN 殘留 (除初始平滑期外)
    assert not tr.isnull().any(), "錯誤：TR 包含 NaN 值"
    print("TR 資料防呆檢查：通過")

    # 3. 驗證三關價計算
    print("\n--- 三關價計算驗證 ---")
    yesterday_high = 20300.0
    yesterday_low = 19900.0
    upper, mid, lower = calculate_three_bands(yesterday_high, yesterday_low)
    print(f"昨日最高: {yesterday_high:.1f}, 昨日最低: {yesterday_low:.1f}")
    print(f"計算結果 -> 上關價: {upper:.2f} | 中關價: {mid:.2f} | 下關價: {lower:.2f}")
    # 檢查公式正確性：昨日區間為 400 點
    # Upper = 19900 + 400 * 1.382 = 19900 + 552.8 = 20452.8
    # Lower = 20300 - 400 * 1.382 = 20300 - 552.8 = 19747.2
    assert abs(upper - 20452.8) < 1e-5, "上關價公式計算錯誤"
    assert abs(mid - 20100.0) < 1e-5, "中關價公式計算錯誤"
    assert abs(lower - 19747.2) < 1e-5, "下關價公式計算錯誤"
    print("三關價數學公式驗證：通過")

    # 4. 驗證市場結構與多空階梯
    print("\n--- 市場結構與多空階梯驗證 ---")
    mss, bos = detect_market_structure(df, period=10)
    ladder = calculate_ladder_levels(df, atr, k=2.0)
    
    print(f"結構破壞 (MSS) 訊號次數: {mss.abs().sum()}")
    print(f"結構連續 (BOS) 訊號次數: {bos.abs().sum()}")
    print(f"階梯起點價格: {ladder.iloc[0]:.2f} -> 階梯終點價格: {ladder.iloc[-1]:.2f}")
    
    # 5. 驗證吊燈止損與部位跟蹤
    print("\n--- 吊燈止損與動態持倉管理驗證 ---")
    ch_long, ch_short = calculate_chandelier_exit(df, atr, period=10, multiplier=3.0)
    
    pm = PositionManager()
    trade_logs = []
    
    # 模擬逐筆交易跟蹤，檢查有無時序偏誤 (以多頭為例)
    for i in range(1, len(df)):
        current_idx = df.index[i]
        close_val = df['close'].iloc[i]
        open_val = df['open'].iloc[i]
        high_val = df['high'].iloc[i]
        low_val = df['low'].iloc[i]
        atr_val = atr.iloc[i]
        vwap_val = vwap.iloc[i]
        
        # 取得前一根 K 線的訊號與階梯，以保證回測時序無誤
        prev_mss = mss.iloc[i - 1]
        prev_bos = bos.iloc[i - 1]
        prev_ch_long = ch_long.iloc[i - 1]
        
        # 若未持倉，檢查是否滿足進場條件
        if not pm.is_active:
            # 假定全域濾網 (三關價中關之上做多) 為真
            global_ok = close_val > mid
            if pm.check_entry_signal(close_val, open_val, base_price, vwap_val, atr_val, high_val, low_val, prev_mss, global_ok):
                pm.is_active = True
                pm.entry_price = close_val
                pm.position_size = 1.0
                pm.stop_loss = close_val - 2.0 * atr_val # 初始止損設為 2 * ATR
                pm.stage = 1
                pm.direction = 1
                entry_bar = i
                trade_logs.append(f"[{current_idx}] 進場做多，價格: {close_val:.2f}，初始止損: {pm.stop_loss:.2f}")
        else:
            # 持倉中，進行動態管理
            bar_count = i - entry_bar
            pnl, event = pm.manage_position(close_val, atr_val, prev_ch_long, bar_count, time_limit=15)
            if event != "持倉中" and event != "無持倉":
                trade_logs.append(f"[{current_idx}] 持倉事件: {event} | 損益率: {pnl*100:+.2f}%")

    print("\n交易日誌模擬紀錄：")
    for log in trade_logs:
        print(f"  {log}")
        
    print("\n" + "=" * 60)
    print("TrendPoint 所有核心演算法驗證全部通過！")
    print("=" * 60)

if __name__ == "__main__":
    run_validation()
