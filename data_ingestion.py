"""
TrendPoint - 數據收集與清洗模組 (Data Ingestion Module)

本模組負責：
1. 透過 yfinance 獲取標的之歷史與即時 K 線數據 (包含日線與日内分鐘線)。
2. 將數據欄位清洗並統一為 OpenSpec 規定之標準時序格式。
3. 提供 CSV 檔案持久化與 SQLite 資料庫寫入介面。
"""

import os
import sqlite3
import pandas as pd
import yfinance as yf
from security_utils import rate_limiter
from db_security import safe_save_to_sqlite
from typing import Optional

@rate_limiter(calls=5, period=60)
def fetch_stock_data(ticker: str, period: str = "1mo", interval: str = "1d", auto_adjust: bool = True) -> Optional[pd.DataFrame]:
    """
    透過 yfinance API 抓取指定標的之 K 線數據。

    參數:
        ticker (str): 標的代號 (例如 "0050.TW", "2330.TW")
        period (str): 抓取歷史長度 (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, max)
        interval (str): K 線週期 (1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo)
        auto_adjust (bool): 是否使用還原股價 (預設 True)。
            高股息 ETF (如 00878、00919) 若使用未還原價格回測，每次除息缺口
            都會被系統誤判為跌破支撐，訊號與績效全數失真，故預設強制還原。

    回傳:
        pd.DataFrame: 格式化後的 K 線數據。若抓取失敗或無數據，回傳 None。
    """
    print(f"開始下載 {ticker} 數據 (週期: {period}, K 線間距: {interval}, 還原股價: {auto_adjust})...")

    try:
        # 呼叫 yfinance 下載 (auto_adjust=True 會將 OHLC 全數還原除權息與分割)
        df = yf.download(tickers=ticker, period=period, interval=interval, progress=False, auto_adjust=auto_adjust)
        
        if df.empty:
            print(f"警告：{ticker} 未能獲取任何數據，請檢查標的代號或週期。")
            return None
            
        # 進行欄位與索引清洗 (防呆與格式化)
        df = clean_kline_dataframe(df)
        validate_data_contract(df)
        print(f"成功下載並清洗 {ticker} 數據，並通過資料合約驗證，共 {len(df)} 筆紀錄。")
        return df
        
    except Exception as e:
        print(f"錯誤：下載 {ticker} 數據或資料合約驗證時發生異常。原因: {e}")
        return None

def clean_kline_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗 yfinance 返回的 DataFrame，統一欄位與時間格式。
    規格要求：欄位包含 datetime, open, high, low, close, volume。
    """
    # 複製 DataFrame 避免 side effects
    cleaned_df = df.copy()
    
    # 處理 yfinance 有時返回多重索引 (MultiIndex) 欄位的情況
    if isinstance(cleaned_df.columns, pd.MultiIndex):
        cleaned_df.columns = cleaned_df.columns.get_level_values(0)
        
    # 統一將欄位名稱轉為小寫
    cleaned_df.columns = [col.lower() for col in cleaned_df.columns]
    
    # 篩選核心的 OHLCV 欄位，排除 Adj Close 等其他欄位
    required_cols = ["open", "high", "low", "close", "volume"]
    cleaned_df = cleaned_df[[col for col in required_cols if col in cleaned_df.columns]]
    
    # 處理索引 (Datetime Index)
    # yfinance 將時間設為 Index。我們將其轉為時區無關 (Timezone-naive) 的 Timestamp，便於後續計算
    if cleaned_df.index.name is not None and "date" in cleaned_df.index.name.lower():
        cleaned_df.index = pd.to_datetime(cleaned_df.index)
        if cleaned_df.index.tz is not None:
            cleaned_df.index = cleaned_df.index.tz_localize(None)
            
    # 重新命名索引為 datetime
    cleaned_df.index.name = "datetime"
    
    # 缺失值防呆：若有缺失的價格或成交量，使用前值填補，再用後值填補
    cleaned_df = cleaned_df.ffill().bfill()
    
    return cleaned_df

def validate_data_contract(df: pd.DataFrame) -> bool:
    """
    驗證資料是否符合時序資料合約規格 (Data Contract Spec)
    - 必須包含 datetime 索引 (DatetimeIndex)
    - 欄位必須完整包含 open, high, low, close, volume
    - 價格與成交量不可包含負數
    - 不能含有 NaN 缺失值
    """
    if df is None or df.empty:
        raise ValueError("資料合約驗證失敗：DataFrame 為空")
        
    # 1. 驗證索引是否為 DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("資料合約驗證失敗：索引必須為 DatetimeIndex")
        
    # 2. 驗證欄位是否完整
    required_cols = {"open", "high", "low", "close", "volume"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise ValueError(f"資料合約驗證失敗：缺少必要欄位 {missing_cols}")
        
    # 3. 驗證有無 NaN
    if df[list(required_cols)].isnull().any().any():
        raise ValueError("資料合約驗證失敗：資料中包含 NaN 缺失值")
        
    # 4. 驗證數值範圍 (價格與交易量必須大於等於 0)
    for col in required_cols:
        if (df[col] < 0.0).any():
            raise ValueError(f"資料合約驗證失敗：欄位 {col} 包含負數值")
            
    return True

def save_to_csv(df: pd.DataFrame, filepath: str) -> bool:
    """
    將 DataFrame 儲存為 CSV 檔案。會自動建立不存在的目錄。
    """
    try:
        # 自動建立目標資料夾 (容錯防呆)
        dir_name = os.path.dirname(filepath)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
            
        df.to_csv(filepath, index=True)
        print(f"資料成功儲存至 CSV: {filepath}")
        return True
    except Exception as e:
        print(f"錯誤：儲存至 CSV 時失敗。路徑: {filepath}，原因: {e}")
        return False

def save_to_sqlite(df: pd.DataFrame, table_name: str, db_path: str) -> bool:
    """
    將 DataFrame 寫入 SQLite 本地資料庫。若表已存在則覆蓋寫入 (具備 SQL 注入安全防範)。
    """
    # 自動建立目標資料夾
    dir_name = os.path.dirname(db_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
        
    return safe_save_to_sqlite(df, table_name, db_path)
