# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 策略參數自動尋優執行腳本 (Run Optimization)

本腳本執行以下任務：
1. 載入全域設定檔，取得系統設定的交易標的清單。
2. 實例化 ParameterOptimizer 類別。
3. 針對清單中每檔標的，執行歷史數據的網格搜尋。
4. 尋找使卡爾瑪比率最高的 (ATR 週期, 階梯乘數 k) 最佳化組合。
5. 將各標的的專屬參數寫回 config.yaml 進行持久化。
"""

import sys
from optimizer import ParameterOptimizer

def run():
    print("=" * 60)
    print("開始執行 TrendPoint 參數最佳化尋優任務...")
    print("=" * 60)
    
    try:
        # 初始化尋優器
        opt = ParameterOptimizer()
        tickers = opt.cfg.data.tickers
        
        print(f"待優化標的清單: {tickers}")
        
        for ticker in tickers:
            try:
                # 執行網格尋優
                best_params, best_calmar = opt.optimize_ticker(ticker)
                
                # 將結果寫回設定檔
                opt.save_override_to_yaml(ticker, best_params)
                
            except Exception as e:
                print(f"警告：優化標的 {ticker} 時發生錯誤。原因: {e}")
                continue
                
        print("\n" + "=" * 60)
        print("參數最佳化尋優任務執行完畢！設定檔已完成更新。")
        print("=" * 60)
        
    except Exception as e:
        print(f"錯誤：初始化尋優任務失敗。原因: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run()
