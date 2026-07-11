# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

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
import numpy as np
import pandas as pd
from typing import Tuple, Dict, Any

from config import load_config
from backtester import BacktestEngine
from db_security import safe_load_db_data

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

        # 憲法安全條款：SQLite 存取一律走 db_security 白名單，禁止逕行拼接 SQL
        return safe_load_db_data(db_path, table_name)

    @staticmethod
    def _calmar(summary: Dict[str, Any]) -> float:
        """Calmar Ratio = 總報酬率 / |MDD|（防禦 MDD 為 0）"""
        mdd_floor = max(abs(summary["max_drawdown"]), 0.0001)
        return summary["total_return"] / mdd_floor

    def optimize_ticker(self, ticker: str,
                        holdout_ratio: float = 0.25
                        ) -> Tuple[Dict[str, Any], float, Dict[str, Any]]:
        """
        針對特定標的進行網格尋優。

        防過擬合（憲法 I）：網格搜尋只允許看前 (1 - holdout_ratio) 的
        訓練段；最佳參數再於最後 holdout_ratio 的 hold-out 段驗證。
        全樣本尋優等於「看過全部答案」，其績效是樣本內成績，
        不得作為寫回 config 的依據。

        搜尋空間：
        - atr_period: [10, 12, 14, 16, 18, 20]
        - ladder_k: [1.5, 2.0, 2.5, 3.0, 3.5]

        回傳：(best_params, 訓練段 Calmar, hold-out 段 summary)
        """
        df = self._load_data(ticker)
        if df.empty:
            raise ValueError(f"標的 {ticker} 的歷史數據為空，無法尋優。")

        split = int(len(df) * (1.0 - holdout_ratio))
        df_train = df.iloc[:split]
        df_holdout = df.iloc[split:]

        if len(df_holdout) < 60:
            raise ValueError(
                f"標的 {ticker} 的 hold-out 段僅 {len(df_holdout)} 根 K 線（<60），"
                f"不足以進行誠實的樣本外驗證。請先累積更長的歷史數據。"
            )

        print(f"\n[開始對 {ticker} 進行參數尋優]")
        print(f"時序長度: {len(df)} 根 K 線（訓練 {len(df_train)} / hold-out {len(df_holdout)}）")

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
                # 網格搜尋只在訓練段執行
                res = engine.run_backtest(
                    df=df_train,
                    atr_period=period,
                    k=k,
                    ch_period=self.cfg.strategy.default.chandelier_period,
                    ch_multiplier=self.cfg.strategy.default.chandelier_mult,
                    time_limit=self.cfg.strategy.default.time_limit
                )

                summary = res["summary"]
                if not summary:
                    continue

                calmar = self._calmar(summary)

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

        # 最佳參數於 hold-out 段驗證（此段從未參與尋優）
        holdout_res = engine.run_backtest(
            df=df_holdout,
            atr_period=best_params["atr_period"],
            k=best_params["ladder_k"],
            ch_period=best_params["chandelier_period"],
            ch_multiplier=best_params["chandelier_mult"],
            time_limit=best_params["time_limit"]
        )
        holdout_summary = holdout_res["summary"]

        print(f"最佳參數組合 -> ATR 週期: {best_params['atr_period']}, 階梯乘數 k: {best_params['ladder_k']}")
        print(f"訓練段（樣本內）  -> 報酬率: {best_summary['total_return']*100:.2f}%, "
              f"MDD: {best_summary['max_drawdown']*100:.2f}%, Calmar: {best_calmar:.2f}")
        print(f"hold-out（樣本外）-> 報酬率: {holdout_summary['total_return']*100:.2f}%, "
              f"MDD: {holdout_summary['max_drawdown']*100:.2f}%, "
              f"Calmar: {self._calmar(holdout_summary):.2f}")

        return best_params, best_calmar, holdout_summary

    @staticmethod
    def holdout_passes(holdout_summary: Dict[str, Any]) -> bool:
        """
        hold-out 驗證閘門：樣本外總報酬必須為正才允許把參數寫回 config。
        （樣本外虧損代表參數大概率是對訓練段雜訊的過擬合。）
        """
        return bool(holdout_summary) and holdout_summary.get("total_return", 0.0) > 0.0

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
