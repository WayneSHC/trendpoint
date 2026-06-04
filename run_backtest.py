"""
Range Navigator - 歷史回測執行與驗證腳本 (Run Backtest)

本腳本執行以下任務：
1. 從 SQLite 資料庫載入 0050.TW 與 2330.TW 的日線數據。
2. 配置回測參數，呼叫 BacktestEngine 進行回測。
3. 列印量化績效指標摘要。
4. 將回測結果（淨值曲線與交易日誌）儲存至 data/ 資料夾。
"""

import os
import pandas as pd
from backtester import BacktestEngine
from config import load_config
from db_security import safe_load_db_data

def load_data_from_db(db_path: str, table_name: str) -> pd.DataFrame:
    """
    從 SQLite 資料庫中安全讀取時序數據，防禦 SQL 注入。
    """
    return safe_load_db_data(db_path, table_name)

def display_summary(ticker: str, summary: dict):
    """
    格式化列印回測統計摘要。
    """
    print(f"\n==================================================")
    print(f"回測績效摘要：{ticker}")
    print(f"==================================================")
    print(f"  初始資金        : {summary['initial_capital']:.2f} 元")
    print(f"  最終淨值        : {summary['final_equity']:.2f} 元")
    print(f"  總報酬率        : {summary['total_return'] * 100:.2f}%")
    print(f"  最大資金回撤(MDD) : {summary['max_drawdown'] * 100:.2f}%")
    print(f"  總交易次數      : {summary['total_trades']} 次")
    print(f"  勝率            : {summary['win_rate'] * 100:.2f}%")
    print(f"  盈虧比 (PF)     : {summary['profit_factor']:.2f}")
    print(f"==================================================")

def run():
    # 載入強型別設定檔
    cfg = load_config()
    db_path = cfg.data.database_path
    
    # 確保資料庫存在
    if not os.path.exists(db_path):
        print(f"錯誤：找不到資料庫 {db_path}，請先執行 run_ingestion.py 下載數據。")
        return
        
    # 動態設定回測標的與對應表名
    test_cases = []
    for ticker in cfg.data.tickers:
        clean_ticker = ticker.replace(".", "_")
        test_cases.append({
            "ticker": ticker,
            "table": f"stock_{clean_ticker}_daily"
        })
    
    # 建立回測引擎，直接傳入設定檔規格物件
    engine = BacktestEngine(config=cfg)
    
    for case in test_cases:
        ticker = case["ticker"]
        table = case["table"]
        
        print(f"\n正在讀取 {ticker} 數據...")
        try:
            df = load_data_from_db(db_path, table)
            if df.empty:
                print(f"警告：資料表 {table} 為空，跳過此標的。")
                continue
                
            print(f"載入成功，共 {len(df)} 筆 K 線數據。")
            
            # 執行回測，使用 config 中的策略參數規格
            results = engine.run_backtest(
                df=df,
                atr_period=cfg.strategy.atr_period,
                k=cfg.strategy.ladder_k,
                ch_period=cfg.strategy.chandelier_period,
                ch_multiplier=cfg.strategy.chandelier_mult,
                time_limit=cfg.strategy.time_limit
            )
            
            summary = results["summary"]
            df_equity = results["equity_curve"]
            df_trades = results["trades"]
            
            # 顯示績效
            display_summary(ticker, summary)
            
            # 持久化回測結果以供日後 UI 面板讀取
            clean_name = ticker.replace(".", "_")
            df_equity.to_csv(f"data/{clean_name}_backtest_equity.csv", index=True)
            df_trades.to_csv(f"data/{clean_name}_backtest_trades.csv", index=False)
            print(f"回測歷程與日誌已匯出至 data/{clean_name}_backtest_*.csv")
            
        except Exception as e:
            print(f"錯誤：回測標的 {ticker} 時發生異常。原因: {e}")

if __name__ == "__main__":
    run()
