"""
Range Navigator - 多空階梯核心演算法模組 (Ladder System Core)

本模組實現了多空階梯系統的核心交易邏輯與數據運算，包括：
1. 平均真實波幅 (ATR) 的動態調整計算。
2. 日線級別的台指期「三關價」全域濾網計算。
3. 市場結構破壞 (MSS) 與結構連續 (BOS) 的向量化偵測。
4. 成交量加權平均價 (VWAP) 與進場多重確認。
5. 吊燈式止損 (Chandelier Exit) 與分批止盈倉位管理。

本模組具備 Numba JIT 加速支援，並在未安裝 Numba 的環境下具備自動降級回退機制。
"""

import numpy as np
import pandas as pd
from typing import Tuple, Union

# Numba JIT 自動降級回退機制 (容錯防呆)
try:
    from numba import jit
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False
    # 若無安裝 Numba，則定義一個無操作的虛擬裝飾器
    def jit(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

# =========================================================================
# 1. 波動率與指標計算模組 (Volatility & Indicators)
# =========================================================================

def calculate_tr(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """
    計算真實波幅 (True Range, TR)
    公式: TR = max(High - Low, |High - Close_prev|, |Low - Close_prev|)
    """
    close_prev = close.shift(1)
    tr1 = high - low
    tr2 = (high - close_prev).abs()
    tr3 = (low - close_prev).abs()
    
    # 向量化比較取得最大值，並處理初始的 NaN 值
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.fillna(0.0)

def calculate_atr(tr: pd.Series, period: int = 14) -> pd.Series:
    """
    計算平均真實波幅 (Average True Range, ATR)，採用懷爾德平滑法 (Wilder's Smoothing)
    公式: ATR_t = ((period - 1) * ATR_prev + TR_t) / period
    """
    # 轉換為 NumPy 陣列以利底層加速
    tr_arr = tr.values
    atr_arr = np.zeros_like(tr_arr)
    
    if len(tr_arr) < period:
        return pd.Series(atr_arr, index=tr.index)
    
    # 初始 ATR 值以簡單移動平均 (SMA) 代替
    atr_arr[period - 1] = np.mean(tr_arr[:period])
    
    # 遞迴計算平滑 ATR
    _calculate_atr_jit(tr_arr, atr_arr, period)
    
    return pd.Series(atr_arr, index=tr.index)

@jit(nopython=True, cache=True)
def _calculate_atr_jit(tr_arr: np.ndarray, atr_arr: np.ndarray, period: int) -> None:
    """
    使用 Numba 加速的 ATR 平滑運算子核心
    """
    for i in range(period, len(tr_arr)):
        atr_arr[i] = ((period - 1) * atr_arr[i - 1] + tr_arr[i]) / period

def calculate_three_bands(yesterday_high: float, yesterday_low: float) -> Tuple[float, float, float]:
    """
    計算台指期「三關價」之全球濾網
    中關價 (Middle Price) = (昨日最高 + 昨日最低) / 2
    上關價 (Upper Price)  = 昨日最低 + (昨日最高 - 昨日最低) * 1.382
    下關價 (Lower Price)  = 昨日最高 - (昨日最高 - 昨日最低) * 1.382
    """
    mid_price = (yesterday_high + yesterday_low) / 2.0
    diff = yesterday_high - yesterday_low
    upper_price = yesterday_low + diff * 1.382
    lower_price = yesterday_high - diff * 1.382
    return upper_price, mid_price, lower_price

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """
    計算當日成交量加權平均價 (Volume Weighted Average Price, VWAP)
    適用於日內 K 線數據，依據交易日重置累計值。
    """
    # 複製 DataFrame 避免 side effects
    temp_df = df.copy()
    temp_df['pv'] = temp_df['close'] * temp_df['volume']
    
    # 依據日期分群計算累計值
    date_group = temp_df.groupby(temp_df.index.date)
    cum_pv = date_group['pv'].cumsum()
    cum_vol = date_group['volume'].cumsum()
    
    # 防呆：避免成交量為零導致除以零
    vwap = cum_pv / cum_vol.replace(0, np.nan)
    return vwap.ffill().fillna(temp_df['close'])

# =========================================================================
# 2. 市場結構與階梯運算模組 (Market Structure & Ladder)
# =========================================================================

def detect_market_structure(df: pd.DataFrame, period: int = 20) -> Tuple[pd.Series, pd.Series]:
    """
    偵測市場結構連續 (BOS) 與結構破壞 (MSS)
    嚴格執行 .shift(1) 以防禦「看前偏誤 (Look-Ahead Bias)」。
    """
    # 滾動計算波段最高/最低點，移位一根 K 線以代表決策當下可取得的歷史數據
    rolling_high = df['high'].rolling(window=period).max().shift(1)
    rolling_low = df['low'].rolling(window=period).min().shift(1)
    
    # BOS (結構連續)：突破同向波段點
    # 看漲 BOS: 當前收盤價突破前 N 週期最高價
    bull_bos = (df['close'] > rolling_high)
    # 看跌 BOS: 當前收盤價跌破前 N 週期最低價
    bear_bos = (df['close'] < rolling_low)
    
    # MSS (結構破壞)：突破反向波段點，需伴隨強力位移 (Displacement)
    # 此處簡化示意：當收盤價強烈反向突破，且成交量高於 20 週期均量的 1.5 倍
    vol_ma = df['volume'].rolling(window=period).mean().shift(1)
    strong_volume = df['volume'] > (vol_ma * 1.5)
    
    bull_mss = (df['close'] > rolling_high) & strong_volume
    bear_mss = (df['close'] < rolling_low) & strong_volume
    
    # 轉換為訊號序列 (1 代表多頭訊號，-1 代表空頭訊號，0 代表無訊號)
    bos_signal = pd.Series(0, index=df.index)
    bos_signal[bull_bos] = 1
    bos_signal[bear_bos] = -1
    
    mss_signal = pd.Series(0, index=df.index)
    mss_signal[bull_mss] = 1
    mss_signal[bear_mss] = -1
    
    return mss_signal, bos_signal

def calculate_ladder_levels(df: pd.DataFrame, atr: pd.Series, k: float = 2.0) -> pd.Series:
    """
    計算動態多空階梯價格線。
    階梯的觸發與調整間距設定為 k * ATR
    """
    close = df['close'].values
    atr_val = atr.values
    ladder_levels = np.zeros_like(close)
    
    # 初始階梯水平設定為初始收盤價
    ladder_levels[0] = close[0]
    
    # 遞迴計算階梯調整邏輯，支援 Numba 加速
    _calculate_ladder_levels_jit(close, atr_val, ladder_levels, k)
    
    return pd.Series(ladder_levels, index=df.index)

@jit(nopython=True, cache=True)
def _calculate_ladder_levels_jit(close: np.ndarray, atr: np.ndarray, ladder_levels: np.ndarray, k: float) -> None:
    """
    Numba 加速之階梯軌跡更新邏輯 (時序時滯防禦)
    """
    current_level = ladder_levels[0]
    is_long = True # 預設初始為多頭狀態
    
    for i in range(1, len(close)):
        # 取得前一根 K 線的階梯與波動數據，防看前偏誤
        prev_level = current_level
        threshold = k * atr[i - 1]
        
        if is_long:
            # 多頭狀態下：若價格向上突破，階梯跟隨上移；若價格跌破前階梯達閾值，轉為空頭
            if close[i] > prev_level + threshold:
                current_level = close[i]
            elif close[i] < prev_level - threshold:
                is_long = False
                current_level = close[i]
        else:
            # 空頭狀態下：若價格向下突破，階梯跟隨下移；若價格突破前階梯達閾值，轉為多頭
            if close[i] < prev_level - threshold:
                current_level = close[i]
            elif close[i] > prev_level + threshold:
                is_long = True
                current_level = close[i]
                
        ladder_levels[i] = current_level

# =========================================================================
# 3. 離場與動態止盈模組 (Exits & Trailing Stops)
# =========================================================================

def calculate_chandelier_exit(df: pd.DataFrame, atr: pd.Series, period: int = 22, multiplier: float = 3.0) -> Tuple[pd.Series, pd.Series]:
    """
    計算吊燈式止損 (Chandelier Exit)
    公式:
    Chandelier Exit (Long)  = Rolling Max(High, n) - (ATR_n * Multiplier)
    Chandelier Exit (Short) = Rolling Min(Low, n) + (ATR_n * Multiplier)
    """
    rolling_max = df['high'].rolling(window=period).max()
    rolling_min = df['low'].rolling(window=period).min()
    
    chandelier_long = rolling_max - (atr * multiplier)
    chandelier_short = rolling_min + (atr * multiplier)
    
    # 進行移位以防看前偏誤，確保當前 K 線決策所採用的止損價為上一根 K 線之計算結果
    return chandelier_long.shift(1), chandelier_short.shift(1)

# =========================================================================
# 3.5. 擴充技術指標模組 (EMA & KAMA)
# =========================================================================

def calculate_ema(series: pd.Series, span: int = 20) -> pd.Series:
    """
    計算指數移動平均線 (EMA)
    """
    return series.ewm(span=span, adjust=False).mean()

@jit(nopython=True, cache=True)
def _kama_loop(close, sc, kama_out):
    for i in range(1, len(close)):
        kama_out[i] = kama_out[i-1] + sc[i] * (close[i] - kama_out[i-1])

def calculate_kama(series: pd.Series, period: int = 10, fast_span: int = 2, slow_span: int = 30) -> pd.Series:
    """
    計算卡夫曼適應性移動平均線 (KAMA)
    """
    close = series.astype(float).values
    
    # Direction: |Price_t - Price_t-n|
    shift_vals = series.shift(period).astype(float).values
    direction = np.abs(close - shift_vals)
    
    # Volatility: Sum of absolute diffs over the period
    diffs = np.abs(np.diff(close, prepend=close[0]))
    volatility = pd.Series(diffs).rolling(period).sum().values
    
    # Efficiency Ratio (ER)
    er = np.zeros(len(close))
    for i in range(len(close)):
        if volatility[i] > 0.0:
            er[i] = direction[i] / volatility[i]
            
    # Smoothing Constant (SC)
    fast_sc = 2.0 / (fast_span + 1.0)
    slow_sc = 2.0 / (slow_span + 1.0)
    sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
    
    # KAMA 計算循環
    kama_out = np.copy(close)
    sma = series.rolling(period).mean().fillna(series).values
    kama_out[0] = sma[0]
    
    _kama_loop(close, sc, kama_out)
    
    return pd.Series(kama_out, index=series.index)

def calculate_bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    計算布林通道 (Bollinger Bands)
    回傳: (上軌, 中軌, 下軌)
    """
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return upper.fillna(series), middle.fillna(series), lower.fillna(series)

# =========================================================================
# 4. 進場確認與部位管理器 (Entry & Position Manager)
# =========================================================================

class PositionManager:
    """
    交易部位管理與複式分批止盈核心邏輯
    實現階段 1 獲利減半、階段 2 保本、階段 3 吊燈止損動態跟蹤。
    """
    def __init__(self):
        self.is_active = False
        self.entry_price = 0.0
        self.position_size = 0.0
        self.stop_loss = 0.0
        self.stage = 0 # 0: 未進場, 1: 初始持倉, 2: 已完成階段 1 減半並保本
        self.direction = 0 # 1: 多頭, -1: 空頭

    def check_entry_signal(self, 
                           close: float, 
                           open_val: float, 
                           daily_open: float, 
                           vwap: float, 
                           atr: float, 
                           candle_high: float, 
                           candle_low: float, 
                           structure_sig: int, 
                           global_filter_ok: bool,
                           is_daily: bool = False) -> bool:
        """
        多重確認進場邏輯 (4 維度確認)
        """
        # 1. 結構端: MSS 或 BOS 方向確認 (1 代表看漲，-1 代表看跌，0 代表無訊號)
        structure_ok = (structure_sig == 1)
        
        # 2. 動能端: 收紅 K (陽線)
        momentum_ok = (close > open_val)
        
        # 3. 趨勢端: 價格同時處於當日開盤價與 VWAP 之上 (若為日線則 vwap 等於 close，此時僅看高於 daily_open)
        if is_daily:
            trend_ok = (close > daily_open)
        else:
            trend_ok = (close > daily_open) and (close > vwap)
        
        # 4. 波動端: 振幅位移大於 1.2 倍 ATR
        amplitude = candle_high - candle_low
        volatility_ok = (amplitude > 1.2 * atr)
        
        # 綜合全域濾網 (例如三關價判定)
        return structure_ok and momentum_ok and trend_ok and volatility_ok and global_filter_ok

    def manage_position(self, 
                        current_close: float, 
                        current_atr: float, 
                        chandelier_long: float, 
                        bar_count: int, 
                        time_limit: int = 10) -> Tuple[float, str]:
        """
        部位動態跟蹤管理，回傳 (實現損益比率, 事件說明)
        """
        if not self.is_active:
            return 0.0, "無持倉"
        
        # 買入多頭部位管理邏輯
        if self.direction == 1:
            # 保呆機制：檢查是否觸發初始止損或移動止損
            if current_close <= self.stop_loss:
                realized_pnl = (self.stop_loss - self.entry_price) / self.entry_price
                self.is_active = False
                self.stage = 0
                return realized_pnl, "觸發止損離場"
                
            # 時間止盈 (Time-Based Exit)
            if bar_count >= time_limit and self.stage == 1:
                realized_pnl = (current_close - self.entry_price) / self.entry_price
                self.is_active = False
                self.stage = 0
                return realized_pnl, "達到時間限制強制平倉"
            
            # 階段 1: 當獲利達到 1.5 * ATR，平倉 50% 並將止損移至進場保本位
            if self.stage == 1:
                target_p1 = self.entry_price + 1.5 * current_atr
                if current_close >= target_p1:
                    self.stage = 2
                    self.stop_loss = self.entry_price # 移至保本位 (Breakeven)
                    self.position_size *= 0.5
                    p1_pnl = (target_p1 - self.entry_price) / self.entry_price * 0.5
                    return p1_pnl, "階段 1 止盈 50% 成功，止損移至保本位"
                    
            # 階段 2: 已完成減半，剩餘部位以吊燈式止損進行移動跟蹤
            elif self.stage == 2:
                # 吊燈止損隨價格上移而動態調升，不可調降
                if chandelier_long > self.stop_loss:
                    self.stop_loss = chandelier_long
                
                # 檢查價格是否跌破動態吊燈止損線
                if current_close < self.stop_loss:
                    realized_pnl = (self.stop_loss - self.entry_price) / self.entry_price * 0.5
                    self.is_active = False
                    self.stage = 0
                    return realized_pnl, "剩餘部位觸發吊燈止損，波段結束"
                    
        return 0.0, "持倉中"
