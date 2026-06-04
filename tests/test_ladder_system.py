"""
Range Navigator - 核心技術指標單元測試模組 (pytest)

本模組針對 ladder_system.py 中所有核心量化指標之數學邏輯進行獨立測試，
以保證各指標公式（如三關價、ATR、EMA、布林通道）之計算結果符合數學規格。
"""

import pytest
import numpy as np
import pandas as pd
from ladder_system import (
    calculate_tr,
    calculate_atr,
    calculate_three_bands,
    calculate_ema,
    calculate_bollinger_bands,
    calculate_vwap
)

def test_three_bands_formula():
    """
    測試台指期三關價數學公式正確性
    已知基準數據：昨日最高 = 20300.0, 昨日最低 = 19900.0
    中關價 = (昨日最高 + 昨日最低) / 2 = 20100.0
    上關價 = 昨日最低 + (昨日最高 - 昨日最低) * 1.382 = 19900.0 + 400.0 * 1.382 = 20452.8
    下關價 = 昨日最高 - (昨日最高 - 昨日最低) * 1.382 = 20300.0 - 400.0 * 1.382 = 19747.2
    """
    yesterday_high = 20300.0
    yesterday_low = 19900.0
    
    upper, mid, lower = calculate_three_bands(yesterday_high, yesterday_low)
    
    assert abs(mid - 20100.0) < 1e-5, "中關價數學公式錯誤"
    assert abs(upper - 20452.8) < 1e-5, "上關價數學公式錯誤"
    assert abs(lower - 19747.2) < 1e-5, "下關價數學公式錯誤"

def test_tr_calculation():
    """
    測試真實波幅 (True Range, TR) 之計算邏輯
    已知數據：
    - Row 0: Open=100, High=105, Low=95, Close=100 (TR = 105 - 95 = 10.0)
    - Row 1: Open=100, High=112, Low=108, Close=110 (Previous Close=100)
             TR = max(112-108, |112-100|, |108-100|) = max(4, 12, 8) = 12.0
    """
    high = pd.Series([105.0, 112.0])
    low = pd.Series([95.0, 108.0])
    close = pd.Series([100.0, 110.0])
    
    tr = calculate_tr(high, low, close)
    
    assert tr.iloc[0] == 10.0, "TR 第一筆計算錯誤"
    assert tr.iloc[1] == 12.0, "TR 第二筆計算錯誤"

def test_ema_calculation():
    """
    測試指數移動平均線 (EMA) 計算
    """
    prices = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
    ema = calculate_ema(prices, span=3)
    
    # 驗證計算出的序列長度與 index 是否一致
    assert len(ema) == len(prices)
    assert not ema.isnull().any()

def test_bollinger_bands_calculation():
    """
    測試布林通道 (Bollinger Bands) 計算
    """
    prices = pd.Series([10.0] * 20)  # 20 筆完全相同的價格
    upper, middle, lower = calculate_bollinger_bands(prices, period=10, num_std=2.0)
    
    # 由於價格無波動，標準差為 0，上軌、中軌、下軌應完全等於原價
    assert abs(middle.iloc[-1] - 10.0) < 1e-5
    assert abs(upper.iloc[-1] - 10.0) < 1e-5
    assert abs(lower.iloc[-1] - 10.0) < 1e-5

def test_vwap_calculation():
    """
    測試成交量加權平均價 (VWAP) 計算
    """
    dates = pd.date_range(start="2026-05-25 09:00:00", periods=3, freq="1min")
    df = pd.DataFrame(index=dates)
    df['close'] = [100.0, 102.0, 101.0]
    df['volume'] = [10.0, 20.0, 30.0]
    
    vwap = calculate_vwap(df)
    
    # VWAP = (100*10 + 102*20 + 101*30) / (10 + 20 + 30) = (1000 + 2040 + 3030) / 60 = 6070 / 60 = 101.16667
    expected = (100.0 * 10.0 + 102.0 * 20.0 + 101.0 * 30.0) / 60.0
    assert abs(vwap.iloc[-1] - expected) < 1e-5
