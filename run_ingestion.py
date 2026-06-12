"""
Range Navigator - 數據下載與持久化測試腳本 (Run Ingestion)

本腳本執行以下任務：
1. 建立儲存資料夾。
2. 下載 0050.TW 與 2330.TW 的日線 (1年) 及 5分鐘線 (5天) 數據。
3. 將結果分別儲存至 CSV 檔案與 SQLite 資料庫 (range_navigator.db)。
4. 進行數據完整性驗證 (防呆欄位與空值檢查)。
"""

import os
from data_ingestion import fetch_stock_data, save_to_csv, save_to_sqlite
from config import load_config

def run():
    print("=" * 60)
    print("開始執行 Range Navigator 數據抓取任務...")
    print("=" * 60)
    
    # 載入強型別設定檔
    sys_cfg = load_config()
    db_path = sys_cfg.data.database_path
    tickers = sys_cfg.data.tickers
    
    # 下載日線與 5分鐘線配置
    # 日線抓取 10 年：交易樣本不足 30~50 筆時，任何勝率與盈虧比都只是雜訊。
    # 歷史必須涵蓋完整多空循環（如 2020/03 與 2022 空頭），統計才有意義。
    configs = [
        {"name": "daily", "period": "10y", "interval": "1d", "csv_suffix": "daily", "table_prefix": "stock_"},
        {"name": "5m", "period": "5d", "interval": "5m", "csv_suffix": "5m", "table_prefix": "stock_"}
    ]
    
    for ticker in tickers:
        print(f"\n[處理標的：{ticker}]")
        # 將 ticker 名稱中的點與斜線轉換為底線，便於作為檔名與表名
        clean_name = ticker.replace(".", "_").replace("/", "_")
        
        for cfg in configs:
            print(f"- 進行 [{cfg['name']}] 級別數據處理:")
            
            # 1. 抓取數據
            df = fetch_stock_data(ticker=ticker, period=cfg["period"], interval=cfg["interval"])
            
            if df is not None:
                # 2. 驗證資料完整性
                print(f"  * 數據防呆檢驗:")
                print(f"    - 資料筆數: {len(df)}")
                print(f"    - 日期範圍: {df.index.min()} ~ {df.index.max()}")
                
                # 檢查是否有任何 NaN 空值
                nan_count = df.isnull().sum().sum()
                if nan_count > 0:
                    print(f"    - [警告]：發現 {nan_count} 個空值。正在執行自動插補清洗...")
                    df = df.ffill().bfill()
                else:
                    print(f"    - 空值檢驗: 通過 (無任何空值)")
                
                # 3. 儲存至 CSV
                csv_path = f"data/{clean_name}_{cfg['csv_suffix']}.csv"
                save_to_csv(df, csv_path)
                
                # 4. 儲存至 SQLite 資料庫
                table_name = f"{cfg['table_prefix']}{clean_name}_{cfg['csv_suffix']}"
                save_to_sqlite(df, table_name, db_path)
                
            else:
                print(f"  * [錯誤]：未能取得 {ticker} 的 [{cfg['name']}] 級別數據，跳過此項儲存。")
                
    print("\n" + "=" * 60)
    print("數據抓取與持久化任務執行完畢！")
    print("=" * 60)

if __name__ == "__main__":
    run()
