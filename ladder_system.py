# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 多空階梯核心演算法模組 (Ladder System Core)

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
from enum import Enum
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

    暖機期（前 period-1 根）ATR 尚未成熟，回傳 NaN——不得回傳 0，
    否則波動濾網 `amplitude > 1.2 * ATR` 恆為 True（形同停用）、
    初始止損 `entry - 2 * ATR` 貼在進場價（任何回檔立即止損）。
    """
    # 轉換為 NumPy 陣列以利底層加速
    tr_arr = tr.values.astype(np.float64)
    atr_arr = np.zeros_like(tr_arr)

    if len(tr_arr) < period:
        return pd.Series(np.full(len(tr_arr), np.nan), index=tr.index)

    # 初始 ATR 值以簡單移動平均 (SMA) 代替
    atr_arr[period - 1] = np.mean(tr_arr[:period])

    # 遞迴計算平滑 ATR
    _calculate_atr_jit(tr_arr, atr_arr, period)

    # 暖機期標記為未成熟
    atr_arr[:period - 1] = np.nan

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

def _detect_fvg(df: pd.DataFrame, direction: str) -> pd.Series:
    """
    偵測 bar t 是否形成 FVG（公平價值缺口，Fair Value Gap），三根 K 線結構。

    direction ∈ {"up","down"}：
      - "up"（向上/看漲缺口）：low(t) > high(t-2)
      - "down"（向下/看跌缺口）：high(t) < low(t-2)

    回傳 bool 序列，與 df.index 對齊；前 2 根為 False（shift(2) 為 NaN，
    與 NaN 比較恆為 False）。純向量化，因果——bar t 只用 t 與 t-2（皆已收盤）。
    """
    if direction == "up":
        result = df['low'] > df['high'].shift(2)
    elif direction == "down":
        result = df['high'] < df['low'].shift(2)
    else:
        raise ValueError(f"direction 必須為 'up' 或 'down'，收到 {direction!r}")
    return result.astype(bool)


def detect_swing_points(df: pd.DataFrame, n: int = 2) -> pd.DataFrame:
    """
    對稱碎形 swing 高/低點偵測（spec 007）。

    bar i 為 swing high 若 high[i] == max(high[i-n : i+n+1])（swing low 對稱）。
    回傳於「樞紐當根 i」對齊的四欄：is_swing_high / is_swing_low /
    swing_high_val / swing_low_val（非樞紐處值為 NaN）。

    單一職責：本函式只做偵測；確認延遲（樞紐 i 要到 i+n 才可用）由呼叫端以
    shift(n) 處理（見 classify_structure）。前 n 與後 n 根因窗口不足恆為 False
    （rolling center 邊界為 NaN）。純向量化。
    """
    if n < 1:
        raise ValueError(f"swing 碎形強度 n 必須 >= 1，收到 {n}")
    window = 2 * n + 1
    roll_high = df['high'].rolling(window=window, center=True).max()
    roll_low = df['low'].rolling(window=window, center=True).min()
    is_high = (df['high'] == roll_high) & roll_high.notna()
    is_low = (df['low'] == roll_low) & roll_low.notna()
    return pd.DataFrame(
        {
            'is_swing_high': is_high.astype(bool),
            'is_swing_low': is_low.astype(bool),
            'swing_high_val': df['high'].where(is_high),
            'swing_low_val': df['low'].where(is_low),
        },
        index=df.index,
    )


def _confirmed_and_prev(val_series: pd.Series, n: int) -> Tuple[pd.Series, pd.Series]:
    """
    由樞紐值序列（NaN 除樞紐當根）算出：截至各 bar「已確認」的最近樞紐值(now)
    與其前一個已確認樞紐值(prev)。確認延遲 = n 根（樞紐 i 於 i+n 方可用）。全向量化。
    """
    confirmed = val_series.shift(n)                 # 樞紐值於 i+n 現身（已確認時點）
    # 以位置（非標籤）指派 prev，對重複索引亦穩健：
    mask = confirmed.notna().to_numpy()
    ev_vals = confirmed.to_numpy()[mask]            # 依序排列的已確認樞紐值
    prev_vals = np.full(ev_vals.shape, np.nan)
    prev_vals[1:] = ev_vals[:-1]                    # 前一個已確認樞紐值（首個為 NaN）
    prev = pd.Series(np.nan, index=val_series.index)
    prev.iloc[mask] = prev_vals
    return confirmed.ffill(), prev.ffill()


def classify_structure(df: pd.DataFrame, n: int = 2) -> pd.DataFrame:
    """
    由「已確認」樞紐序列判定 HH/HL/LH/LL 與趨勢偏向（spec 007；看前偏誤安全）。

    回傳 conf_swing_high / conf_swing_low（截至各 bar 已確認的最近 swing 值）與
    trend_bias（+1 上升 / -1 下降 / 0 不明）：更高的 swing 高且更高的 swing 低為
    上升；更低的 swing 高且更低的 swing 低為下降；其餘不明。
    """
    sp = detect_swing_points(df, n=n)
    sh_now, sh_prev = _confirmed_and_prev(sp['swing_high_val'], n)
    sl_now, sl_prev = _confirmed_and_prev(sp['swing_low_val'], n)
    higher_high = sh_now > sh_prev
    higher_low = sl_now > sl_prev
    lower_high = sh_now < sh_prev
    lower_low = sl_now < sl_prev
    trend_bias = pd.Series(0, index=df.index, dtype=int)
    trend_bias[higher_high & higher_low] = 1
    trend_bias[lower_high & lower_low] = -1
    return pd.DataFrame(
        {
            'conf_swing_high': sh_now,
            'conf_swing_low': sl_now,
            'trend_bias': trend_bias,
        },
        index=df.index,
    )


def detect_market_structure(df: pd.DataFrame, period: int = 20, *,
                            use_fvg: bool = False,
                            fvg_lookback: int = 3,
                            swing_n: int = 2,
                            volume_mult: float = 1.5) -> Tuple[pd.Series, pd.Series]:
    """
    偵測市場結構連續 (BOS) 與結構破壞/反轉 (MSS)。
    嚴格執行 .shift(1) / .shift(swing_n) 以防禦「看前偏誤 (Look-Ahead Bias)」。

    spec 007：MSS 校正為理論的反轉訊號——上升結構中收盤跌破最近『已確認』HL
    為看跌反轉、下降結構中突破最近『已確認』LH 為看漲反轉，並需位移確認。
    BOS 續勢語意（突破同向 rolling 波段點）維持不變；MSS 與 BOS 語意分離、不再是
    子集（同 bar 同向以 ~BOS 保證互斥）。
    spec 002：use_fvg=True 時，MSS 須近 fvg_lookback 根內有同向 FVG 才成立
    （假訊號歸零）；BOS 不受影響。
    """
    # 滾動計算波段最高/最低點，移位一根 K 線以代表決策當下可取得的歷史數據
    rolling_high = df['high'].rolling(window=period).max().shift(1)
    rolling_low = df['low'].rolling(window=period).min().shift(1)
    
    # BOS (結構連續)：突破同向波段點
    # 看漲 BOS: 當前收盤價突破前 N 週期最高價
    bull_bos = (df['close'] > rolling_high)
    # 看跌 BOS: 當前收盤價跌破前 N 週期最低價
    bear_bos = (df['close'] < rolling_low)
    
    # MSS 反轉（spec 007）：反向「已確認」波段點被突破 + 位移確認（Displacement）。
    # 趨勢偏向由已確認碎形結構（HH/HL/LH/LL）判定；看前偏誤由 classify_structure 內
    # shift(swing_n) 確認延遲保證。位移沿用量能 proxy（門檻乘數集中至 volume_mult）。
    vol_ma = df['volume'].rolling(window=period).mean().shift(1)
    displacement = df['volume'] > (vol_ma * volume_mult)

    structure = classify_structure(df, n=swing_n)
    trend_up = structure['trend_bias'] == 1
    trend_down = structure['trend_bias'] == -1

    # 看跌反轉：上升結構中收盤跌破最近已確認 HL(=最近已確認 swing low)
    bear_mss = (trend_up & (df['close'] < structure['conf_swing_low'])
                & displacement & (~bear_bos)).fillna(False)
    # 看漲反轉：下降結構中收盤突破最近已確認 LH(=最近已確認 swing high)
    bull_mss = (trend_down & (df['close'] > structure['conf_swing_high'])
                & displacement & (~bull_bos)).fillna(False)

    # FVG 確認（spec 002；現套於校正後的 MSS）：MSS 只在近 fvg_lookback 根內有
    # 同向 FVG 時保留，否則歸零。use_fvg=False 時不進入此分支（spec 007 後 MSS 語意
    # 已變，不再與 spec 001 位元一致；進場層以 mss_reversal_entry=False 提供回歸錨點）。
    if use_fvg:
        fvg_up_present = (
            _detect_fvg(df, "up").rolling(fvg_lookback).max().fillna(False).astype(bool)
        )
        fvg_down_present = (
            _detect_fvg(df, "down").rolling(fvg_lookback).max().fillna(False).astype(bool)
        )
        bull_mss = bull_mss & fvg_up_present
        bear_mss = bear_mss & fvg_down_present

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

    # 不在此處移位——時基統一由呼叫端管理（引擎取「判定根的前一根」）。
    # 舊版在此 shift(1) 加上引擎又取前一根，造成雙重移位（實際用到 i-2 的
    # 吊燈線），止損跟蹤比設計慢一根（健檢 1.8）
    return chandelier_long, chandelier_short

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

def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    計算平均趨向指標 (Average Directional Index, ADX)，採用懷爾德平滑法。
    ADX 衡量趨勢強度（不分方向）：低於 20 一般視為盤整市況。
    趨勢跟蹤系統最大的敵人是盤整掃損，故以 ADX 作為進場的趨勢強度濾網。
    """
    high = df['high']
    low = df['low']
    close = df['close']

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    tr = calculate_tr(high, low, close)

    # 懷爾德平滑 (等效於 alpha = 1/period 的 EMA)
    atr_s = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s.replace(0, np.nan)

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()

    return adx.fillna(0.0)

def calculate_efficiency_ratio(series: pd.Series, period: int = 10) -> pd.Series:
    """
    計算 Kaufman 效率比率 (Efficiency Ratio, ER)
    ER = |淨位移| / 路徑總長度，介於 0~1。
    接近 1 代表單邊趨勢行情；接近 0 代表來回震盪的高噪音盤整。
    """
    direction = (series - series.shift(period)).abs()
    volatility = series.diff().abs().rolling(window=period).sum()
    er = direction / volatility.replace(0, np.nan)
    return er.fillna(0.0).clip(0.0, 1.0)

def calculate_regime_filter(df: pd.DataFrame,
                            use_adx: bool = True, adx_period: int = 14, adx_threshold: float = 20.0,
                            use_ma: bool = True, ma_period: int = 200,
                            use_er: bool = False, er_period: int = 10, er_threshold: float = 0.3) -> pd.Series:
    """
    綜合市況濾網 (Regime Filter)：回傳布林序列，True 代表允許做多進場。
    - ADX 濾網：趨勢強度不足（盤整）時禁止進場。
    - 長均線濾網：價格低於長期均線（如 200MA）時禁止做多——最便宜的災難保險。
    - ER 濾網：路徑噪音過高時禁止進場。
    所有指標均移位一根 K 線，確保僅使用已收盤的歷史數據（防看前偏誤）。
    """
    ok = pd.Series(True, index=df.index)

    if use_adx:
        adx = calculate_adx(df, period=adx_period).shift(1)
        ok &= (adx >= adx_threshold)

    if use_ma:
        # min_periods 防止前段資料全為 NaN 而封死整段回測（資料不足時以現有均值替代）
        long_ma = df['close'].rolling(window=ma_period, min_periods=1).mean().shift(1)
        ok &= (df['close'] > long_ma)

    if use_er:
        er = calculate_efficiency_ratio(df['close'], period=er_period).shift(1)
        ok &= (er >= er_threshold)

    return ok.fillna(False)

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

def build_indicator_frame(df: pd.DataFrame,
                          *,
                          structure_period: int,
                          atr_period: int = 14,
                          ladder_k: float = 2.0,
                          chandelier_period: int = 22,
                          chandelier_multiplier: float = 3.0,
                          include_regime: bool = True,
                          regime_kwargs: dict = None,
                          use_fvg: bool = False,
                          fvg_lookback: int = 3,
                          swing_n: int = 2,
                          volume_mult: float = 1.5) -> pd.DataFrame:
    """
    正典指標組裝入口（spec 004，契約見 specs/004-acceptance-tests/contracts/）。
    回測引擎與即時監控共用此函式，消除兩端各自內聯的重複邏輯。

    時序契約：第 i 列僅依賴 df.iloc[:i+1]（結構訊號的 rolling 已 shift(1)、
    三關價只用昨日完成值）。chandelier 欄位不做 shift——呼叫端負責
    timebase（回測引擎取判定根的前一根），此處加 shift 會造成雙重延遲。

    參數一律由呼叫端自 config 傳入；本函式不讀組態、不硬編碼可調參數。
    include_regime=False 時省略 regime_ok 欄位（監控端不需市況濾網）。
    use_fvg 預設 False（baseline-preserving）：不帶此參數的既有呼叫（含 004 parity）
    行為不變；呼叫端須顯式 use_fvg=True 才啟用 spec 002 的 FVG 確認。
    回傳新 DataFrame，不就地修改輸入。
    """
    out = df.copy()

    # 真實波幅與 ATR
    tr = calculate_tr(out['high'], out['low'], out['close'])
    out['atr'] = calculate_atr(tr, period=atr_period)
    out['vwap'] = calculate_vwap(out)

    # 市況濾網 (Regime Filter)：ADX 趨勢強度 + 長均線方向 + ER 噪音
    if include_regime:
        out['regime_ok'] = calculate_regime_filter(out, **(regime_kwargs or {}))

    # 結構與階梯計算
    mss, bos = detect_market_structure(out, period=structure_period,
                                       use_fvg=use_fvg, fvg_lookback=fvg_lookback,
                                       swing_n=swing_n, volume_mult=volume_mult)
    out['mss_signal'] = mss
    out['bos_signal'] = bos
    out['ladder'] = calculate_ladder_levels(out, out['atr'], k=ladder_k)

    # 吊燈止損線
    ch_long, ch_short = calculate_chandelier_exit(
        out, out['atr'], period=chandelier_period, multiplier=chandelier_multiplier)
    out['chandelier_long'] = ch_long
    out['chandelier_short'] = ch_short

    # 日內開盤價：以當日第一筆交易之開盤價為基準
    out['date'] = out.index.date
    out['daily_open'] = out.groupby('date')['open'].transform('first')

    # 三關價 (以日為單位，昨日最高/最低計算，當日使用)
    # 此處使用分群求得每日的昨日最高與昨日最低
    daily_ohlcv = out.groupby('date').agg({'high': 'max', 'low': 'min'})
    yesterday_ohlcv = daily_ohlcv.shift(1) # 昨日日線數據

    # 將昨日數據對接回分鐘線/日線 DataFrame 中
    out['yesterday_high'] = out['date'].map(yesterday_ohlcv['high']).fillna(out['high'].iloc[0])
    out['yesterday_low'] = out['date'].map(yesterday_ohlcv['low']).fillna(out['low'].iloc[0])

    # 計算三關價
    out['mid_price'] = (out['yesterday_high'] + out['yesterday_low']) / 2.0
    out['diff'] = out['yesterday_high'] - out['yesterday_low']
    out['upper_price'] = out['yesterday_low'] + out['diff'] * 1.382
    out['lower_price'] = out['yesterday_high'] - out['diff'] * 1.382

    return out

# =========================================================================
# 4. 進場確認與部位管理器 (Entry & Position Manager)
# =========================================================================

class ExitEvent(Enum):
    """
    部位管理事件。引擎與所有呼叫端一律以 enum 身分比對來驅動資金流；
    .value 的中文字串僅供顯示與交易日誌（修改文案不影響任何邏輯分支）。
    舊版以中文字串精確比對橫跨三個檔案，任何文案修改都會讓平倉分支
    靜默失效（健檢 1.10）。
    """
    NOT_ACTIVE = "無持倉"
    HOLDING = "持倉中"
    STOP_LOSS = "觸發止損離場"
    TIME_LIMIT = "達到時間限制強制平倉"
    STAGE1_HALF = "階段 1 止盈 50% 成功，止損移至保本位"
    CHANDELIER = "剩餘部位觸發吊燈止損，波段結束"

# 會使引擎執行「全數平倉」的事件集合
FULL_EXIT_EVENTS = frozenset({ExitEvent.STOP_LOSS, ExitEvent.TIME_LIMIT, ExitEvent.CHANDELIER})

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
                           is_daily: bool = False,
                           disabled_filters: frozenset = frozenset()) -> bool:
        """
        多重確認進場邏輯 (4 維度確認)

        disabled_filters 可包含 'structure' / 'momentum' / 'trend' / 'volatility' / 'global'，
        被列入的維度將直接視為通過。此參數供消融測試 (Ablation Test) 逐一評估
        每道濾網對期望值的真實貢獻，避免堆疊「看起來嚴謹」但只會扼殺交易次數的濾網。
        """
        # 1. 結構端: MSS 或 BOS 方向確認 (1 代表看漲，-1 代表看跌，0 代表無訊號)
        structure_ok = (structure_sig == 1) or ('structure' in disabled_filters)

        # 2. 動能端: 收紅 K (陽線)
        momentum_ok = (close > open_val) or ('momentum' in disabled_filters)

        # 3. 趨勢端: 價格同時處於當日開盤價與 VWAP 之上 (若為日線則 vwap 等於 close，此時僅看高於 daily_open)
        if is_daily:
            trend_ok = (close > daily_open)
        else:
            trend_ok = (close > daily_open) and (close > vwap)
        trend_ok = trend_ok or ('trend' in disabled_filters)

        # 4. 波動端: 振幅位移大於 1.2 倍 ATR。
        # ATR 未成熟（NaN 或 <=0，見 calculate_atr 暖機期）一律不進場——
        # 不得依賴「與 NaN 比較恰好為 False」的隱性行為
        amplitude = candle_high - candle_low
        atr_ready = (atr is not None) and pd.notna(atr) and atr > 0.0
        volatility_ok = (atr_ready and amplitude > 1.2 * atr) or ('volatility' in disabled_filters)

        # 綜合全域濾網 (例如三關價判定與市況濾網)
        global_ok = global_filter_ok or ('global' in disabled_filters)

        return structure_ok and momentum_ok and trend_ok and volatility_ok and global_ok

    def manage_position(self,
                        current_close: float,
                        current_atr: float,
                        chandelier_long: float,
                        bar_count: int,
                        time_limit: int = 10) -> ExitEvent:
        """
        部位動態跟蹤管理，回傳 ExitEvent（呼叫端以 enum 身分比對）。

        設計假設（健檢 1.9 註記）：
        - 止損與吊燈皆以「收盤價跌破」判定——盤中一度跌破但收盤收回
          不觸發，屬樂觀假設；實際成交價由回測引擎決定（次根開盤±滑價）。
        - 本方法不回報損益數字：舊版回傳的 pnl_ratio 以止損價計算、
          與引擎實際成交價不一致，且從未被任何引擎使用，已移除。
        """
        if not self.is_active:
            return ExitEvent.NOT_ACTIVE

        # 買入多頭部位管理邏輯
        if self.direction == 1:
            # 保呆機制：檢查是否觸發初始止損或移動止損
            if current_close <= self.stop_loss:
                self.is_active = False
                self.stage = 0
                return ExitEvent.STOP_LOSS

            # 時間止盈 (Time-Based Exit)——刻意僅約束階段 1（遲未達首個獲利
            # 目標的呆滯部位）；完成減半後 (stage 2) 部位已保本，轉由吊燈止損
            # 跟蹤讓利潤奔跑，不再受時間上限約束（健檢 1.7 確認為預期行為，
            # 行為規格見 tests/test_position_manager.py）
            if bar_count >= time_limit and self.stage == 1:
                self.is_active = False
                self.stage = 0
                return ExitEvent.TIME_LIMIT

            # 階段 1: 當獲利達到 1.5 * ATR，平倉 50% 並將止損移至進場保本位
            if self.stage == 1:
                target_p1 = self.entry_price + 1.5 * current_atr
                if current_close >= target_p1:
                    self.stage = 2
                    self.stop_loss = self.entry_price # 移至保本位 (Breakeven)
                    self.position_size *= 0.5
                    return ExitEvent.STAGE1_HALF

            # 階段 2: 已完成減半，剩餘部位以吊燈式止損進行移動跟蹤
            elif self.stage == 2:
                # 吊燈止損隨價格上移而動態調升，不可調降
                if chandelier_long > self.stop_loss:
                    self.stop_loss = chandelier_long

                # 檢查價格是否跌破動態吊燈止損線
                if current_close < self.stop_loss:
                    self.is_active = False
                    self.stage = 0
                    return ExitEvent.CHANDELIER

        return ExitEvent.HOLDING
