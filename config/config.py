"""
Range Navigator - 系統設定驗證規格模組 (Configuration Spec)

本模組使用 Pydantic (v2) 定義系統所有配置參數的資料合約，並在載入時執行靜態驗證，
落實軟體工程的 Fail-Fast 防錯機制，確保傳入策略與回測引擎的參數皆符合合理的數學邊界。
本版本支援策略參數的「標的專屬覆蓋 (Overrides)」。
"""

import os
import yaml
from typing import List, Dict
from pydantic import BaseModel, Field, ValidationError

class DataConfig(BaseModel):
    """
    資料來源與存儲設定
    """
    database_path: str = Field(
        default="range_navigator.db",
        description="SQLite 資料庫檔案路徑"
    )
    tickers: List[str] = Field(
        default_factory=lambda: ["2330.TW", "0050.TW"],
        description="系統支援追蹤的證券/標代號清單"
    )

class BacktestConfig(BaseModel):
    """
    回測引擎專用設定
    """
    init_capital: float = Field(
        default=1000000.0,
        gt=0.0,
        description="初始投資資本，必須大於 0 元"
    )

class SingleStrategyParams(BaseModel):
    """
    單一標的之多空階梯與技術指標策略參數
    """
    atr_period: int = Field(
        default=14,
        ge=1,
        description="ATR 計算週期，必須為正整數且大於等於 1"
    )
    ladder_k: float = Field(
        default=2.0,
        gt=0.0,
        description="多空階梯的 ATR 觸發乘數，必須為大於 0 的浮點數"
    )
    chandelier_period: int = Field(
        default=22,
        ge=1,
        description="吊燈式止損的滾動週期，必須為正整數且大於等於 1"
    )
    chandelier_mult: float = Field(
        default=3.0,
        gt=0.0,
        description="吊燈式止損的 ATR 乘數，必須為大於 0 的浮點數"
    )
    time_limit: int = Field(
        default=15,
        ge=1,
        description="持倉的最大 K 線根數限制，防禦時間維度的風險"
    )

class StrategyConfig(BaseModel):
    """
    策略多層次配置結構：提供預設參數與個別標的覆蓋 (overrides)
    """
    default: SingleStrategyParams = Field(
        default_factory=SingleStrategyParams,
        description="預設策略參數組"
    )
    ticker_overrides: Dict[str, SingleStrategyParams] = Field(
        default_factory=dict,
        description="個別標的專用策略參數覆蓋字典"
    )
    
    def get_params_for_ticker(self, ticker: str) -> SingleStrategyParams:
        """
        獲取指定標的專屬的策略參數。若無專屬參數，則返回 default 參數。
        """
        return self.ticker_overrides.get(ticker, self.default)

class TradingCostConfig(BaseModel):
    """
    交易摩擦摩擦成本設定
    """
    commission_rate: float = Field(
        default=0.001425,
        ge=0.0,
        description="券商手續費率（單邊），不得為負值"
    )
    tax_rate: float = Field(
        default=0.003,
        ge=0.0,
        description="證券交易稅率，不得為負值"
    )
    slip_rate: float = Field(
        default=0.0005,
        ge=0.0,
        description="預估滑點率（單邊），不得為负值"
    )

class SystemConfig(BaseModel):
    """
    全域系統配置規格模型
    """
    data: DataConfig = Field(default_factory=DataConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    trading_cost: TradingCostConfig = Field(default_factory=TradingCostConfig)

def load_config(config_path: str = None) -> SystemConfig:
    """
    載入並驗證設定檔。
    若未指定 path，則預設尋找 config.yaml 同目錄設定檔；若找不到檔案則直接返回預設配置。
    若格式不符驗證規範，將立刻拋出 ValidationError 以防錯。
    """
    if config_path is None:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(current_dir, "config.yaml")

    # 若檔案不存在，退回 Pydantic 生成的系統預設配置
    if not os.path.exists(config_path):
        return SystemConfig()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f)
            
        # 若 yaml 檔案為空，給予空 dict 以觸發預設值生成
        if yaml_data is None:
            yaml_data = {}
            
        return SystemConfig(**yaml_data)
        
    except ValidationError as e:
        # 強制 Fail-fast
        raise RuntimeError(f"設定檔驗證失敗！原因：\n{e}")
    except Exception as e:
        raise RuntimeError(f"載入設定檔時發生非預期錯誤：{e}")

def save_config(cfg: SystemConfig, config_path: str = None) -> bool:
    """
    將 SystemConfig 狀態回寫至 YAML 設定檔中。
    """
    if config_path is None:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(current_dir, "config.yaml")

    try:
        config_dict = cfg.model_dump()
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config_dict, f, default_flow_style=False, allow_unicode=True)
        return True
    except Exception as e:
        print(f"寫入設定檔失敗： {e} ")
        return False

