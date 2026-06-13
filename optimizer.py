"""
TrendPoint - 策略參數尋優模組 (Parameter Optimizer)

本模組使用高效的網格搜尋 (Grid Search) 演算法：
1. 從本地 SQLite 資料庫載入時序數據。
2. 在定義的二維參數空間 (ATR 週期 與 階梯乘數 k) 中執行回測。
3. 以卡爾瑪比率 (Calmar Ratio = 總報酬率 / |最大回撤|) 作為尋優適應度指標，兼顧報酬與風險控制。
4. 尋優完成後，自動將最佳化參數更新並寫回系統設定檔 config.yaml。
"""

import os
import yaml
import sqlite3
import numpy as np
import pandas as pd
from typing import Tuple, Dict, Any

from config import load_config
from backtester import BacktestEngine

class ParameterOptimizer:
    """
    參數自動尋優器類別
    """
    def __init__(self, config_path: str = None):
        self.cfg = load_config(config_path)
        self.config_path = config_path
        if self.config_path is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.config_path = os.path.join(current_dir, "config", "config.yaml")

    def _load_data(self, ticker: str) -> pd.DataFrame:
        """
        從 SQLite 載入該標的的日線數據
        """
        db_path = self.cfg.data.database_path
        clean_ticker = ticker.replace(".", "_")
        table_name = f"stock_{clean_ticker}_daily"
        
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"資料庫檔案不存在: {db_path}")
            
        conn = sqlite3.connect(db_path)
        try:
            df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
            df['datetime'] = pd.to_datetime(df['datetime'])
            df = df.set_index('datetime')
            return df
        finally:
            conn.close()

    def optimize_ticker(self, ticker: str) -> Tuple[Dict[str, Any], float]:
        """
        針對特定標的進行網格尋優
        搜尋空間：
        - atr_period: [10, 12, 14, 16, 18, 20]
        - ladder_k: [1.5, 2.0, 2.5, 3.0, 3.5]
        """
        df = self._load_data(ticker)
        if df.empty:
            raise ValueError(f"標的 {ticker} 的歷史數據為空，無法尋優。")
            
        print(f"\n[開始對 {ticker} 進行參數尋優]")
        print(f"時序長度: {len(df)} 根 K 線")
        
        # 搜尋空間定義
        atr_periods = [10, 12, 14, 16, 18, 20]
        ladder_ks = [1.5, 2.0, 2.5, 3.0, 3.5]
        
        best_params = {}
        best_calmar = -np.inf
        best_summary = {}
        
        # 使用現有回測引擎
        engine = BacktestEngine(config=self.cfg)
        
        for period in atr_periods:
            for k in ladder_ks:
                # 執行單次歷史回測
                res = engine.run_backtest(
                    df=df,
                    atr_period=period,
                    k=k,
                    ch_period=self.cfg.strategy.default.chandelier_period,
                    ch_multiplier=self.cfg.strategy.default.chandelier_mult,
                    time_limit=self.cfg.strategy.default.time_limit
                )
                
                summary = res["summary"]
                if not summary:
                    continue
                    
                total_return = summary["total_return"]
                mdd = abs(summary["max_drawdown"])
                
                # 計算 Calmar Ratio (防禦 MDD 為 0 的情況)
                mdd_floor = max(mdd, 0.0001)
                calmar = total_return / mdd_floor
                
                # 若 Calmar 較佳，則記錄
                if calmar > best_calmar:
                    best_calmar = calmar
                    best_params = {
                        "atr_period": period,
                        "ladder_k": k,
                        "chandelier_period": self.cfg.strategy.default.chandelier_period,
                        "chandelier_mult": self.cfg.strategy.default.chandelier_mult,
                        "time_limit": self.cfg.strategy.default.time_limit
                    }
                    best_summary = summary
                    
        print(f"最佳參數組合 -> ATR 週期: {best_params['atr_period']}, 階梯乘數 k: {best_params['ladder_k']}")
        print(f"最佳績效績效 -> 報酬率: {best_summary['total_return']*100:.2f}%, MDD: {best_summary['max_drawdown']*100:.2f}%, 卡爾瑪比率: {best_calmar:.2f}")
        
        return best_params, best_calmar

    def save_override_to_yaml(self, ticker: str, params: Dict[str, Any]):
        """
        將尋優結果寫入 config.yaml 中的 ticker_overrides 字典中
        """
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"找不到設定檔：{self.config_path}")
            
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            
        if "strategy" not in data:
            data["strategy"] = {}
        if "ticker_overrides" not in data["strategy"]:
            data["strategy"]["ticker_overrides"] = {}
            
        # 寫入特定標的的覆蓋參數
        data["strategy"]["ticker_overrides"][ticker] = {
            "atr_period": int(params["atr_period"]),
            "ladder_k": float(params["ladder_k"]),
            "chandelier_period": int(params["chandelier_period"]),
            "chandelier_mult": float(params["chandelier_mult"]),
            "time_limit": int(params["time_limit"])
        }
        
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
            
        print(f"成功將 {ticker} 的最佳參數寫回設定檔: {self.config_path}")
