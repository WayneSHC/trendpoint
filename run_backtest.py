# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 歷史回測執行與驗證腳本 (Run Backtest)

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
from db_security import safe_load_db_data, table_name_for
from instruments import AssetClass, equity_instrument
from performance import format_performance_report
from monte_carlo import bootstrap_trades, format_monte_carlo_report
from trading_costs import for_asset_class

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

    # 完整風險調整後績效報表 (Sharpe / Sortino / Calmar / 曝險時間)
    print(format_performance_report(summary, title=f"{ticker} 風險調整後績效"))

    # 蒙地卡羅交易序列重抽：回撤「分布」才是真正的風險預算，而非歷史單一路徑
    trade_returns = summary.get("trade_returns", [])
    if trade_returns:
        mc = bootstrap_trades(trade_returns, n_sims=5000, seed=42)
        print(format_monte_carlo_report(mc, title=f"{ticker} 蒙地卡羅重抽"))

def run():
    # 載入強型別設定檔
    cfg = load_config()
    db_path = cfg.data.database_path
    
    # 確保資料庫存在
    if not os.path.exists(db_path):
        print(f"錯誤：找不到資料庫 {db_path}，請先執行 run_ingestion.py 下載數據。")
        return
        
    # 動態設定回測標的與對應表名（spec 008b：現貨 tickers + 結構化期貨 instruments）
    test_cases = []
    for ticker in cfg.data.tickers:
        inst = equity_instrument(ticker)
        test_cases.append({
            "ticker": ticker,
            "table": table_name_for(inst, "daily"),
            "instrument": inst,
        })
    for inst in cfg.data.instruments:
        test_cases.append({
            "ticker": inst.id,
            "table": table_name_for(inst, "daily"),
            "instrument": inst,
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

            # spec 008b：依資產類別分派成本/sizing 元件（現貨=現行語意、期貨=槓桿模型）；
            # 008a 的單標的入口護欄已退役（組合路徑護欄保留，見 backtester.assert_backtestable）
            instrument = case["instrument"]
            cost_model, sizer = for_asset_class(instrument, cfg)
            pv = instrument.contract.point_value if instrument.contract else 1.0

            # 執行回測，使用該標的專屬（或預設）的策略參數規格
            params = cfg.strategy.get_params_for_ticker(ticker)
            results = engine.run_backtest(
                df=df,
                asset_class=instrument.asset_class,
                cost_model=cost_model,
                sizer=sizer,
                point_value=pv,
                atr_period=params.atr_period,
                k=params.ladder_k,
                ch_period=params.chandelier_period,
                ch_multiplier=params.chandelier_mult,
                time_limit=params.time_limit,
                use_adx_filter=params.use_adx_filter,
                adx_period=params.adx_period,
                adx_threshold=params.adx_threshold,
                use_ma_filter=params.use_ma_filter,
                ma_period=params.ma_period,
                use_er_filter=params.use_er_filter,
                er_period=params.er_period,
                er_threshold=params.er_threshold,
                use_fvg=params.use_fvg,
                fvg_lookback=params.fvg_lookback,
                swing_n=params.swing_fractal_n,
                volume_mult=params.mss_volume_mult,
                mss_reversal_entry=params.mss_reversal_entry
            )
            
            summary = results["summary"]
            df_equity = results["equity_curve"]
            df_trades = results["trades"]

            # 顯示績效
            display_summary(ticker, summary)

            # spec 008b：期貨附加資訊（口數/保證金/爆倉）
            if instrument.asset_class == AssetClass.FUTURES:
                buys = df_trades[df_trades["action"] == "BUY"] if not df_trades.empty else df_trades
                if not buys.empty:
                    print(f"  [期貨] 進場次數 {len(buys)}｜單次口數 max {buys['shares'].max():.0f}"
                          f"｜佔用保證金 max {buys['margin_used'].max():,.0f} 元")
                if summary.get("blown_up"):
                    print("  ⚠ [期貨] 本回測觸發爆倉（權益 ≤ 0 強制結清並終止）——槓桿/使用率過高")
            
            # 持久化回測結果以供日後 UI 面板讀取
            clean_name = ticker.replace(".", "_")
            df_equity.to_csv(f"data/{clean_name}_backtest_equity.csv", index=True)
            df_trades.to_csv(f"data/{clean_name}_backtest_trades.csv", index=False)
            print(f"回測歷程與日誌已匯出至 data/{clean_name}_backtest_*.csv")
            
        except Exception as e:
            print(f"錯誤：回測標的 {ticker} 時發生異常。原因: {e}")

if __name__ == "__main__":
    run()
