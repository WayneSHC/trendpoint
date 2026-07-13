# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 系統設定驗證規格模組 (Configuration Spec)

本模組使用 Pydantic (v2) 定義系統所有配置參數的資料合約，並在載入時執行靜態驗證，
落實軟體工程的 Fail-Fast 防錯機制，確保傳入策略與回測引擎的參數皆符合合理的數學邊界。
本版本支援策略參數的「標的專屬覆蓋 (Overrides)」。
"""

import os
import yaml
from typing import List, Dict, Optional
from pydantic import BaseModel, Field, ValidationError

from instruments import Instrument  # spec 008a：資產類別抽象

class DataConfig(BaseModel):
    """
    資料來源與存儲設定
    """
    database_path: str = Field(
        default="trendpoint.db",
        description="SQLite 資料庫檔案路徑"
    )
    tickers: List[str] = Field(
        default_factory=lambda: ["2330.TW", "0050.TW"],
        description="系統支援追蹤的證券/標代號清單（純字串→equity/yfinance instrument）"
    )
    instruments: List[Instrument] = Field(
        default_factory=list,
        description="結構化 instrument 宣告（期貨/明確資產類別，spec 008a）；與 tickers 合併為 registry"
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
    use_adx_filter: bool = Field(
        default=True,
        description="是否啟用 ADX 趨勢強度濾網（盤整市況不進場）"
    )
    adx_period: int = Field(
        default=14,
        ge=2,
        description="ADX 計算週期"
    )
    adx_threshold: float = Field(
        default=20.0,
        ge=0.0,
        description="ADX 低於此值視為盤整，不觸發進場"
    )
    use_ma_filter: bool = Field(
        default=True,
        description="是否啟用長期均線大週期濾網（價格低於長均線不做多）"
    )
    ma_period: int = Field(
        default=200,
        ge=2,
        description="大週期均線回看期數（日線預設 200）"
    )
    use_er_filter: bool = Field(
        default=False,
        description="是否啟用 Kaufman 效率比率濾網（噪音過高不進場）"
    )
    er_period: int = Field(
        default=10,
        ge=2,
        description="Kaufman Efficiency Ratio 計算週期"
    )
    er_threshold: float = Field(
        default=0.3,
        ge=0.0, le=1.0,
        description="ER 低於此值視為高噪音盤整，不觸發進場"
    )
    use_fvg: bool = Field(
        default=True,
        description="MSS 訊號是否需近 M 根內同向 FVG（公平價值缺口）確認，過濾假訊號"
    )
    fvg_lookback: int = Field(
        default=3,
        ge=1,
        description="FVG 確認的回看根數 M；MSS 成立需近 M 根內出現同向 FVG"
    )
    swing_fractal_n: int = Field(
        default=2,
        ge=1,
        description="對稱碎形強度 N：swing 高/低點需為 [i-N, i+N] 之極值；同時決定樞紐確認延遲 N 根（spec 007）"
    )
    mss_reversal_entry: bool = Field(
        default=True,
        description="是否啟用 MSS 反轉進場；False 復現 007 前的 BOS-only 進場（回歸/消融錨點，spec 007）"
    )
    mss_ladder_k: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="MSS 反轉進場的 ATR 追價乘數；None 時繼承 ladder_k（spec 007）"
    )
    mss_volume_mult: float = Field(
        default=1.5,
        gt=0.0,
        description="MSS 位移（Displacement）確認的量能乘數：volume > 均量 × 此值（spec 007）"
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
    lot_size: int = Field(
        default=1000,
        ge=1,
        description="整股交易單位（台股一張為 1000 股）；買進股數會向下取整至此單位之倍數"
    )

class PortfolioConfig(BaseModel):
    """
    多標的投資組合資金配置設定
    """
    allocation: str = Field(
        default="inverse_vol",
        pattern="^(equal|inverse_vol)$",
        description="資金配置法：equal（等權重）或 inverse_vol（波動率倒數加權，風險均衡）"
    )
    vol_lookback: int = Field(
        default=60,
        ge=5,
        description="計算已實現波動率之滾動回看期數"
    )
    max_weight: float = Field(
        default=0.5,
        gt=0.0, le=1.0,
        description="單一標的之最大資金權重上限，防止低波動標的吃掉整個組合"
    )

class DataQualityConfig(BaseModel):
    """
    資料品質防呆設定（憲法 VI：離群值須過濾並發出警告）。
    """
    max_close_jump_ratio: float = Field(
        default=3.0,
        gt=0.0,
        description="相鄰收盤跳動比率上限（|pct_change|）；超過即判資料離群並拒絕整批。"
                    "台股現貨有 10% 漲跌幅限制，預設 3.0（±300%）僅攔截歸零／千倍級的資料錯誤。"
    )
    max_close_jump_ratio_by_asset: Dict[str, float] = Field(
        default_factory=dict,
        description="per-asset-class 離群跳動門檻覆寫（如 {'futures': 5.0}）；未列則用 max_close_jump_ratio（spec 008a）"
    )

    def jump_ratio_for(self, asset_class) -> float:
        """依資產類別取離群門檻；AssetClass(str,Enum) 成員 == 其字串值，dict.get 兩者皆可。"""
        return self.max_close_jump_ratio_by_asset.get(asset_class, self.max_close_jump_ratio)

class SystemConfig(BaseModel):
    """
    全域系統配置規格模型
    """
    data: DataConfig = Field(default_factory=DataConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    trading_cost: TradingCostConfig = Field(default_factory=TradingCostConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    data_quality: DataQualityConfig = Field(default_factory=DataQualityConfig)

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

