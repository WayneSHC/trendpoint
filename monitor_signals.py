# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 實時訊號監控與去重推播程式 (Signal Monitor)

本程式支援：
1. 讀取與計算最新 K 線指標（階梯軌跡、MSS/BOS 突破、三關價邊界）。
2. 在本地 SQLite 資料庫中維持去重記錄表 `sent_alerts`。
3. 提供測試推播參數 `--test-alert` 與單次查詢參數 `--once`。
4. 提供自動化定時循環輪詢。
"""

import os
import sqlite3
import argparse
import time
import datetime
import pandas as pd
from typing import Optional

from alerts import AlertManager
from data_ingestion import fetch_stock_data, clean_kline_dataframe
import ladder_system
from config import load_config

# 載入設定檔以確保與全域一致
cfg = load_config()
DB_PATH = cfg.data.database_path
TICKERS = cfg.data.tickers

def init_sent_alerts_db(db_path: str):
    """
    初始化訊號發送去重資料表。
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_alerts (
            ticker TEXT,
            bar_time TEXT,
            alert_type TEXT,
            sent_time TEXT,
            PRIMARY KEY (ticker, bar_time, alert_type)
        )
        """)
        conn.commit()
    except Exception as e:
        print(f"錯誤：初始化去重資料表失敗: {e}")
    finally:
        conn.close()

def is_alert_already_sent(ticker: str, bar_time: str, alert_type: str) -> bool:
    """
    查詢該 K 線時間之特定訊號是否已發送過。
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM sent_alerts WHERE ticker=? AND bar_time=? AND alert_type=?",
            (ticker, str(bar_time), alert_type)
        )
        result = cursor.fetchone()
        return result is not None
    except Exception as e:
        print(f"錯誤：查詢去重紀錄失敗: {e}")
        return False
    finally:
        conn.close()

def mark_alert_as_sent(ticker: str, bar_time: str, alert_type: str):
    """
    將已成功發送的警示訊息時間戳記與型態寫入資料庫。
    """
    conn = sqlite3.connect(DB_PATH)
    sent_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sent_alerts (ticker, bar_time, alert_type, sent_time) VALUES (?, ?, ?, ?)",
            (ticker, str(bar_time), alert_type, sent_time)
        )
        conn.commit()
    except Exception as e:
        print(f"錯誤：寫入去重紀錄失敗: {e}")
    finally:
        conn.close()

def select_closed_bar_indices(bar_times: pd.DatetimeIndex,
                              now: pd.Timestamp,
                              bar_interval: pd.Timedelta) -> tuple:
    """
    回傳 (latest_idx, prev_idx)，latest 為「最新已收盤」K 線的位置索引。

    盤中執行時，yfinance 回傳的最後一根 K 線是進行中的 bar——其
    close/high/low/volume 都會持續變動，以它判定訊號會產生「推播後
    訊號又消失」的 repaint。若 now 尚未到達末根時間 + K 線間隔，
    表示末根尚未收盤，一律改用倒數第二根作為最新已收盤 K 線。
    """
    if now < bar_times[-1] + bar_interval:
        return -2, -3
    return -1, -2

def check_new_signals(ticker: str, alert_mgr: AlertManager):
    """
    獲取最新數據，計算指標並發送滿足條件的警訊。
    """
    # 1. 下載最新 5 天的 5 分鐘線，以獲取最新即時資料
    print(f"\n正在下載 {ticker} 最新即時數據進行訊號分析...")
    df = fetch_stock_data(ticker=ticker, period="5d", interval="5m")
    
    if df is None or df.empty:
        print(f"警告：未能獲取 {ticker} 數據，略過此標的。")
        return

    # 2. 計算技術指標與多空訊號
    # 正典組裝入口：與回測引擎共用 ladder_system.build_indicator_frame，
    # 消除兩端重複內聯。監控端不需市況濾網，故 include_regime=False。
    df = ladder_system.build_indicator_frame(
        df, structure_period=10, include_regime=False)

    # 3. 取得最新「已收盤」的 K 線與其前一根進行突破判斷。
    # 盤中末根是進行中的 bar，不得用於訊號判定（repaint 防禦）
    if len(df) < 3:
        return

    bar_interval = pd.Timedelta(minutes=5)  # 對應上方 fetch 的 interval="5m"
    now = pd.Timestamp.now(tz=df.index.tz)
    latest_idx, prev_idx = select_closed_bar_indices(df.index, now, bar_interval)

    latest_time = df.index[latest_idx]
    latest_bar = df.iloc[latest_idx]
    prev_bar = df.iloc[prev_idx]
    
    # 4. 判定與推播訊號
    # 訊號 A：MSS 結構破壞 (1為多頭突破，-1為空頭突破)
    # 訊號決策採用上一根已關閉 K 線的訊號，防範看前偏誤與訊號飄移
    if latest_bar['mss_signal'] == 1:
        alert_type = "BULLISH_MSS"
        if not is_alert_already_sent(ticker, latest_time, alert_type):
            msg = f"<b>【多頭反轉訊號】</b>\n標的: {ticker}\n時間: {latest_time}\n價格: {latest_bar['close']}\n說明: 偵測到最新 K 線看漲 MSS 結構破壞，大成交量突破前高，趨勢可能反轉向上！\n當前階梯參考價: {latest_bar['ladder']:.2f}"
            if alert_mgr.send_alert(msg):
                mark_alert_as_sent(ticker, latest_time, alert_type)
                
    elif latest_bar['mss_signal'] == -1:
        alert_type = "BEARISH_MSS"
        if not is_alert_already_sent(ticker, latest_time, alert_type):
            msg = f"<b>【空頭反轉訊號】</b>\n標的: {ticker}\n時間: {latest_time}\n價格: {latest_bar['close']}\n說明: 偵測到最新 K 線看跌 MSS 結構破壞，大成交量跌破前低，趨勢可能反向做空！\n當前階梯參考價: {latest_bar['ladder']:.2f}"
            if alert_mgr.send_alert(msg):
                mark_alert_as_sent(ticker, latest_time, alert_type)

    # 訊號 B：BOS 趨勢延續 (1為多頭強勢突破，-1為空頭強勢突破)
    if latest_bar['bos_signal'] == 1:
        alert_type = "BULLISH_BOS"
        if not is_alert_already_sent(ticker, latest_time, alert_type):
            msg = f"<b>【多頭趨勢延續】</b>\n標的: {ticker}\n時間: {latest_time}\n價格: {latest_bar['close']}\n說明: 偵測到 BOS 結構連續突破，多頭力道持續加強！\n當前階梯參考價: {latest_bar['ladder']:.2f}"
            if alert_mgr.send_alert(msg):
                mark_alert_as_sent(ticker, latest_time, alert_type)
                
    elif latest_bar['bos_signal'] == -1:
        alert_type = "BEARISH_BOS"
        if not is_alert_already_sent(ticker, latest_time, alert_type):
            msg = f"<b>【空頭趨勢延續】</b>\n標的: {ticker}\n時間: {latest_time}\n價格: {latest_bar['close']}\n說明: 偵測到 BOS 結構連續跌破，空頭趨勢強烈加壓！\n當前階梯參考價: {latest_bar['ladder']:.2f}"
            if alert_mgr.send_alert(msg):
                mark_alert_as_sent(ticker, latest_time, alert_type)

    # 訊號 C：三關價邊界突破
    # 最新 K 線收盤價突破上關價
    if latest_bar['close'] > latest_bar['upper_price'] and prev_bar['close'] <= prev_bar['upper_price']:
        alert_type = "BREAK_UPPER_BAND"
        if not is_alert_already_sent(ticker, latest_time, alert_type):
            msg = f"<b>【突破上關價】</b>\n標的: {ticker}\n時間: {latest_time}\n價格: {latest_bar['close']}\n說明: 價格收盤強勢站上昨日三關價之上關位 ({latest_bar['upper_price']:.2f})！多頭波段進入強勢區域。"
            if alert_mgr.send_alert(msg):
                mark_alert_as_sent(ticker, latest_time, alert_type)
                
    # 最新 K 線收盤價跌破下關價
    elif latest_bar['close'] < latest_bar['lower_price'] and prev_bar['close'] >= prev_bar['lower_price']:
        alert_type = "BREAK_LOWER_BAND"
        if not is_alert_already_sent(ticker, latest_time, alert_type):
            msg = f"<b>【跌破下關價】</b>\n標的: {ticker}\n時間: {latest_time}\n價格: {latest_bar['close']}\n說明: 價格收盤跌破昨日三關價之下關位 ({latest_bar['lower_price']:.2f})！空頭波段進入弱勢區域。"
            if alert_mgr.send_alert(msg):
                mark_alert_as_sent(ticker, latest_time, alert_type)

def main():
    parser = argparse.ArgumentParser(description="TrendPoint - Live Signal Monitor")
    parser.add_argument("--test-alert", action="store_true", help="發送一筆測試訊息驗證通知管道配置")
    parser.add_argument("--once", action="store_true", help="僅執行單次訊號檢測，不進行循環輪詢")
    parser.add_argument("--interval", type=int, default=60, help="即時輪詢檢查間隔秒數 (預設 60 秒)")
    args = parser.parse_args()

    # 初始化去重資料表
    init_sent_alerts_db(DB_PATH)
    
    # 建立通知管理器
    alert_mgr = AlertManager()

    # 1. 執行測試發送
    if args.test_alert:
        print("開始執行推播測試...")
        test_msg = "<b>【TrendPoint 系統測試】</b>\n這是一筆來自系統的測試警報！\n您的通知發送管道已成功配置。"
        alert_mgr.send_alert(test_msg)
        return

    # 2. 執行單次檢測
    if args.once:
        print("開始單次實時訊號檢測...")
        for ticker in TICKERS:
            check_new_signals(ticker, alert_mgr)
        print("單次檢測執行完畢。")
        return

    # 3. 執行輪詢監控循環
    print(f"開啟實時監控輪詢中... 檢查間隔: {args.interval} 秒。按 Ctrl+C 結束。")
    try:
        while True:
            for ticker in TICKERS:
                try:
                    check_new_signals(ticker, alert_mgr)
                except Exception as e:
                    print(f"錯誤：監控標的 {ticker} 時發生未預期錯誤: {e}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n監控循環已終止。")

if __name__ == "__main__":
    main()
