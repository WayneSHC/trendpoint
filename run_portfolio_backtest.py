"""
TrendPoint - 投資組合回測執行腳本 (Run Portfolio Backtest)

本腳本執行以下任務：
1. 實例化 PortfolioBacktester。
2. 對 config 設置的所有標的進行時間軸對齊與等權重資金分配的回測。
3. 列印投資組合整體的績效統計。
4. 將投資組合淨值曲線與交易日誌儲存至 data/ 資料夾中。
"""

import os
import sys
from portfolio_backtester import PortfolioBacktester

def display_summary(summary: dict):
    """
    格式化列印投資組合績效摘要。
    """
    print(f"\n==================================================")
    print(f"TrendPoint 投資組合回測績效摘要")
    print(f"==================================================")
    print(f"  初始資金        : {summary['initial_capital']:.2f} 元")
    print(f"  最終淨值        : {summary['final_equity']:.2f} 元")
    print(f"  總報酬率        : {summary['total_return'] * 100:.2f}%")
    print(f"  最大帳戶回撤(MDD) : {summary['max_drawdown'] * 100:.2f}%")
    print(f"  總交易次數      : {summary['total_trades']} 次")
    print(f"  整體勝率        : {summary['win_rate'] * 100:.2f}%")
    print(f"  整體盈虧比 (PF) : {summary['profit_factor']:.2f}")
    print(f"==================================================")

def run():
    print("=" * 60)
    print("開始執行 TrendPoint 投資組合聯合同步回測任務...")
    print("=" * 60)
    
    try:
        backtester = PortfolioBacktester()
        res = backtester.run_portfolio_backtest()
        
        summary = res["summary"]
        df_equity = res["equity_curve"]
        df_trades = res["trades"]
        
        display_summary(summary)
        
        # 建立 data 目錄 (若不存在)
        os.makedirs("data", exist_ok=True)
        
        # 匯出資料
        df_equity.to_csv("data/portfolio_backtest_equity.csv", index=True)
        df_trades.to_csv("data/portfolio_backtest_trades.csv", index=False)
        print("投資組合淨值歷程與交易明細已成功匯出至 data/portfolio_backtest_*.csv")
        
    except Exception as e:
        print(f"錯誤：執行投資組合回測失敗。原因: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run()
