"""
Range Navigator - 防看前偏誤（Look-Ahead Bias）規格驗證模組 (pytest)

本測試專門驗證回測引擎的時序嚴格度，透過「歷史資料末梢篡改法」：
1. 傳入原始模擬時序數據 D1 執行回測，記錄交易紀錄 L1。
2. 傳入經修改末端數據（未來數據）的 D2 執行回測，記錄交易紀錄 L2。
3. 斷言在數據被篡改的時間戳 t 之前的交易訊號、進出場時間與價格，在 L1 與 L2 中必須完全一致。
以確保系統不具備未來函數，保證量化決策之誠實性。
"""

import pytest
import numpy as np
import pandas as pd
from backtester import BacktestEngine

def _generate_mock_data(n_bars: int = 100) -> pd.DataFrame:
    """
    產生一組可重現的模擬 OHLCV 時序數據
    """
    np.random.seed(42)
    dates = pd.date_range(start="2026-05-25 09:00:00", periods=n_bars, freq="1min")
    
    # 產生多頭價格軌跡
    base_price = 20000.0
    price_changes = np.random.normal(5.0, 10.0, n_bars)
    prices = base_price + np.cumsum(price_changes)
    
    df = pd.DataFrame(index=dates)
    df['close'] = prices
    df['open'] = df['close'].shift(1).fillna(base_price - 5.0)
    df['high'] = df[['open', 'close']].max(axis=1) + 8.0
    df['low'] = df[['open', 'close']].min(axis=1) - 8.0
    df['volume'] = np.random.uniform(500, 2000, n_bars).round()
    
    return df

def test_no_lookahead_bias():
    """
    驗證回測引擎無看前偏誤
    """
    n_bars = 100
    df_original = _generate_mock_data(n_bars)
    
    # 建立回測引擎
    engine = BacktestEngine(
        initial_capital=1000000.0,
        commission_rate=0.001425,
        tax_rate=0.003,
        slippage_rate=0.0005
    )
    
    # 執行原始回測
    res_orig = engine.run_backtest(
        df_original,
        atr_period=14,
        k=2.0,
        ch_period=22,
        ch_multiplier=3.0,
        time_limit=15
    )
    trades_orig = res_orig["trades"]
    
    # 建立篡改版 DataFrame：將第 80 根 K 線之後的所有價格大幅修改（未來巨變）
    df_modified = df_original.copy()
    split_idx = 80
    split_time = df_original.index[split_idx]
    
    df_modified.iloc[split_idx:, df_modified.columns.get_loc('close')] *= 2.0
    df_modified.iloc[split_idx:, df_modified.columns.get_loc('open')] *= 2.0
    df_modified.iloc[split_idx:, df_modified.columns.get_loc('high')] *= 2.0
    df_modified.iloc[split_idx:, df_modified.columns.get_loc('low')] *= 2.0
    
    # 執行篡改版回測
    res_mod = engine.run_backtest(
        df_modified,
        atr_period=14,
        k=2.0,
        ch_period=22,
        ch_multiplier=3.0,
        time_limit=15
    )
    trades_mod = res_mod["trades"]
    
    # 篩選在第 80 根 K 線（t < split_time）之前的交易紀錄
    trades_orig_before = trades_orig[trades_orig["datetime"] < split_time]
    trades_mod_before = trades_mod[trades_mod["datetime"] < split_time]
    
    # 驗證筆數一致
    assert len(trades_orig_before) == len(trades_mod_before), "未來數據修改影響了歷史交易筆數！存在看前偏誤。"
    
    # 驗證每一筆交易的屬性（時間、價格、動作、股數）完全相同
    for i in range(len(trades_orig_before)):
        t_orig = trades_orig_before.iloc[i]
        t_mod = trades_mod_before.iloc[i]
        
        assert t_orig["datetime"] == t_mod["datetime"], f"第 {i} 筆交易的時間戳不一致"
        assert t_orig["action"] == t_mod["action"], f"第 {i} 筆交易的動作不一致"
        assert abs(t_orig["price"] - t_mod["price"]) < 1e-5, f"第 {i} 筆交易的執行價格不一致"
        assert abs(t_orig["shares"] - t_mod["shares"]) < 1e-5, f"第 {i} 筆交易的股數不一致"
        
    print("時序偏誤驗證通過：未來的價格變動不影響歷史既成之交易決策。")
