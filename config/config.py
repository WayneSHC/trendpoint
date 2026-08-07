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
from pydantic import BaseModel, Field, ValidationError, model_validator

from instruments import Instrument  # spec 008a：資產類別抽象

class FuturesDataSourceConfig(BaseModel):
    """
    期貨真實資料源參數（spec 010）：回填/節流/重試/驗證容差。
    FinMind token 走環境變數 FINMIND_TOKEN（安全鐵律：憑證不入 config 檔）。
    """
    backfill_start: str = Field(
        default="1998-07-21",
        description="歷史回填起始日（TX 上市日；clarify 定案全歷史，可調）"
    )
    backfill_start_overrides: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "逐 instrument 的回填起始日覆寫（instrument id → ISO 日期）。"
            "台指類三商品**上市日不同**：TX 1998-07、MTX 2001-04、TMF 2024-07。"
            "共用 1998 起始會對 TAIFEX 發出數百個必然落空的月請求"
            "（TMF 約 312 個 × 節流 2 秒 ≈ 10 分鐘），故逐商品指定。"
            "取上市**當月 1 日**而非確切掛牌日：`fetch_raw` 本就以月為請求單位"
            "（`start.replace(day=1)`），故日精度不省請求，只徒增「日期記錯一天"
            "就永久截掉首日資料」的風險。"
        )
    )
    throttle_seconds: float = Field(
        default=2.0, ge=0.0,
        description="TAIFEX 回填每請求間隔秒數（未公告限流之保守節流）"
    )
    max_retries: int = Field(
        default=3, ge=0,
        description="單一請求失敗重試次數；用罄後 fail-fast"
    )
    verify_tolerance: float = Field(
        default=0.0, ge=0.0,
        description="交叉驗證容差（TAIFEX vs FinMind 同源鏡像，預設全等）"
    )
    latest_cache_seconds: float = Field(
        default=15.0, ge=0.0,
        description=(
            "當日端點（OpenAPI）回應的快取秒數。該端點一次回傳**全市場**所有商品，"
            "監控輪詢卻是逐 instrument 呼叫——三個台指類商品會對同一份資料打三次。"
            "同一輪內共用回應即可維持「至多 1 請求/輪詢」。"
            "預設 15 秒：足以涵蓋一輪內連續處理數個期貨標的，又遠短於預設 60 秒的"
            "輪詢間隔，故不會把上一輪的資料帶進下一輪。設 0 = 停用快取。"
        )
    )

    @model_validator(mode="after")
    def _check_dates_parseable(self) -> "FuturesDataSourceConfig":
        """回填起始日必須是可解析的 ISO 日期——壞值要在載入組態時炸，
        而不是在回填跑了幾百個請求之後才於 `date.fromisoformat` 崩掉。"""
        from datetime import date as _date
        for label, value in [("backfill_start", self.backfill_start),
                             *((f"backfill_start_overrides['{k}']", v)
                               for k, v in self.backfill_start_overrides.items())]:
            try:
                _date.fromisoformat(value)
            except ValueError as e:
                raise ValueError(
                    f"data.futures_source.{label} 不是合法的 ISO 日期（YYYY-MM-DD）：{value!r}"
                ) from e
        return self

    def backfill_start_for(self, instrument_id: str) -> str:
        """該 instrument 的回填起始日：有覆寫用覆寫，否則用全域預設。"""
        return self.backfill_start_overrides.get(instrument_id, self.backfill_start)


class DataConfig(BaseModel):
    """
    資料來源與存儲設定
    """
    database_path: str = Field(
        default="trendpoint.db",
        description="SQLite 資料庫檔案路徑"
    )
    futures_source: FuturesDataSourceConfig = Field(
        default_factory=FuturesDataSourceConfig,
        description="期貨真實資料源參數（spec 010）；預設齊全，既有 config 零改可載"
    )
    equity_history_period: str = Field(
        default="max",
        description=(
            "現貨日線的 yfinance 取數期間（'max' / '10y' / '5y' …）。"
            "預設 max：B 段實測顯示 10y 下各標的僅 6~14 筆交易，遠低於統計推論所需的 30 筆。"
            "注意 max 會納入 1990 年代台股（漲跌幅限制、成本結構、參與者結構皆不同），"
            "樣本量與同質性是取捨——縮短期間請改此參數，勿在 adapter 內硬編碼。"
        )
    )
    tickers: List[str] = Field(
        default_factory=lambda: ["2330.TW", "0050.TW"],
        description="系統支援追蹤的證券/標代號清單（純字串→equity/yfinance instrument）"
    )
    instruments: List[Instrument] = Field(
        default_factory=list,
        description="結構化 instrument 宣告（期貨/明確資產類別，spec 008a）；與 tickers 合併為 registry"
    )

    @model_validator(mode="after")
    def _backfill_overrides_reference_declared_instruments(self) -> "DataConfig":
        """回填覆寫的鍵必須對應到已宣告的 instrument id。

        打錯 id 的後果是**靜默**的：`backfill_start_for` 找不到鍵就回全域預設，
        於是 TMF 悄悄從 1998 年開始回填，多花十分鐘卻仍然「成功」。
        這種只會變慢、不會變紅的錯誤最難察覺，故在載入時就擋掉。
        """
        declared = {inst.id for inst in self.instruments}
        unknown = sorted(set(self.futures_source.backfill_start_overrides) - declared)
        if unknown:
            raise ValueError(
                f"data.futures_source.backfill_start_overrides 含未宣告的 instrument id "
                f"{unknown}；已宣告者為 {sorted(declared)}"
            )
        return self


class BacktestConfig(BaseModel):
    """
    回測引擎專用設定
    """
    init_capital: float = Field(
        default=1000000.0,
        gt=0.0,
        description="初始投資資本，必須大於 0 元"
    )
    futures_init_capital: float = Field(
        default=200000000.0,
        gt=0.0,
        description=(
            "期貨回測的初始資本（與現貨分開）。"
            "大台一口名目值 = 指數 × 200——以現貨的 100 萬資本，低槓桿下"
            "連一口都下不起（0 筆交易），槓桿與資本因此被合約規格綁死，"
            "無法各自獨立設定。"
            "本值調的是**口數量化粒度**而非槓桿：1× 下名目值恆等於權益，"
            "資本大小不改變槓桿倍數或報酬率，只改變 floor() 的相對誤差。"
            "前值 1,000 萬依「指數 25,000」估算，實測序列末端一口已逾 795 萬，"
            "導致全情境保證金死亡；2 億使全段量化誤差低於 1%。"
            "完整依據見 config/config.yaml 的同名鍵註解。"
        )
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
    enable_short: bool = Field(
        default=False,
        description="期貨做空開關（spec 003，期貨限定）：僅對 futures instrument 生效——"
                    "現貨結構上不存在空方路徑（引擎閘門保證）；對現貨 ticker 的 override "
                    "明設 True 會於組態載入時 fail-fast"
    )
    # ---- BOS 量能確認（spec 012）：預設關閉，關閉時回測行為逐筆不變 ----
    use_bos_volume: bool = Field(
        default=False,
        description="啟用 BOS 續勢進場的量能確認（spec 012）：判定根成交量須放大至"
                    "前 bos_volume_period 根均量的 bos_volume_mult 倍以上。"
                    "**僅作用於續勢分支**，MSS 反轉分支已內建自己的位移量能確認"
    )
    bos_volume_mult: float = Field(
        default=1.5,
        gt=0.0,
        description="BOS 量能確認的放大倍數門檻（成交量 > 均量 × 此值，嚴格大於）。"
                    "與 MSS 位移確認的 mss_volume_mult 獨立——兩者作用於不同路徑"
    )
    bos_volume_period: int = Field(
        default=20,
        ge=2,
        description="BOS 量能確認的均量回看根數。刻意不綁定 structure_period："
                    "後者是結構樞紐的回看窗，與「均量該取多長」無先驗關聯"
                    "（spec 012 research.md D4）"
    )
    # ---- 進場閘門（spec 013）：兩道皆預設關閉，關閉時回測行為逐筆逐根不變 ----
    use_dd_gate: bool = Field(
        default=False,
        description="啟用回撤閘門（spec 013）：權益自峰值回落達 dd_limit_pct 時停止開新倉，"
                    "回升至 dd_resume_pct 以內解除。**只擋進場、不影響任何出場路徑**"
    )
    dd_limit_pct: float = Field(
        default=0.20,
        gt=0.0, lt=1.0,
        description="回撤封鎖門檻（正數表幅度，0.20 = 回撤 20%）。預設值僅為形式佔位——"
                    "閘門預設關閉故不生效；實際值須由 spec 013 SC-015 以 monte_carlo "
                    "p95 回撤分布校準後決定並記錄依據"
    )
    dd_resume_pct: float = Field(
        default=0.10,
        ge=0.0, lt=1.0,
        description="回撤恢復門檻：回撤回升至此值以內解除封鎖。必須嚴格小於 dd_limit_pct"
                    "（遲滯區間），0.0 代表需回撤完全回復"
    )
    use_settlement_gate: bool = Field(
        default=False,
        description="啟用結算日封鎖（spec 013）：期貨標的在台指期結算日（每月第三個週三，"
                    "遇假日順延）不開新倉。對現貨標的無效果且不報錯"
    )

    @model_validator(mode="after")
    def _resume_below_limit(self) -> "SingleStrategyParams":
        """spec 013 FR-005：恢復門檻必須嚴格小於封鎖門檻。

        相等亦拒絕——兩者相等時遲滯區間退化為單一門檻，權益在門檻附近震盪會使
        閘門逐根翻動（flapping），交易與否取決於小數點後幾位。這是必須被 schema
        擋下的設定，不是使用者的自由。
        """
        if self.dd_resume_pct >= self.dd_limit_pct:
            raise ValueError(
                f"dd_resume_pct（{self.dd_resume_pct}）必須嚴格小於 dd_limit_pct"
                f"（{self.dd_limit_pct}）。兩者之間的遲滯區間是回撤閘門避免逐根翻動的"
                "唯一機制；相等或反向會讓閘門在門檻附近失去意義。"
            )
        return self

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

class FuturesCostConfig(BaseModel):
    """
    期貨帳戶/政策層成本與 sizing 參數（spec 008b）。

    契約內生值（乘數/tick/交易所定額費）不在此——它們隨 instrument 的
    `contract`（ContractSpec）走。此處僅放使用者政策與跨契約費率。
    """
    broker_commission_per_lot: float = Field(
        default=0.0, ge=0.0,
        description="券商加收每口每邊定額 NT$（0 = 僅 TAIFEX 權威費率，可調為實際券商數字）"
    )
    tax_rate: float = Field(
        default=0.00002, ge=0.0,
        description="期貨交易稅率（契約金額 ×，兩邊各收；台指類權威值十萬分之二）"
    )
    slippage_ticks: float = Field(
        default=1.0, ge=0.0,
        description="每邊滑價 tick 數（台指 1 tick = 1 點）"
    )
    margin_rate: float = Field(
        default=0.055, gt=0.0, le=1.0,
        description="每口保證金 = 名目值（點數×乘數）× 此值（台指歷史約 4-6%）"
    )
    margin_utilization: float = Field(
        default=0.5, gt=0.0, le=1.0,
        description="保證金使用率上限：口數 = floor(權益 × 此值 ÷ 每口保證金)"
    )


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
    futures: FuturesCostConfig = Field(
        default_factory=FuturesCostConfig,
        description="期貨成本/口數政策（spec 008b）；預設齊全，既有 config 零改可載"
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

class MaLineConfig(BaseModel):
    """
    單一均線的通知設定（spec 014）。
    """
    enabled: bool = Field(
        default=True,
        description="是否對此條均線發送觸價通知（總開關關閉時本欄不生效）"
    )
    period: int = Field(
        default=20,
        ge=2,
        description="均線回看的交易日根數（台股慣例：月 20／季 60／半年 120／年 240）"
    )


class OutcomeTrackingConfig(BaseModel):
    """
    推播訊號的事後表現追蹤（spec 015 A 段）。

    **這是觀察層設定，不是策略參數**——與 `MaAlertConfig` 同一判準：不進回測、
    不影響任何訊號、不該被 optimizer 掃描。

    **本區塊產出的數字不是策略績效**：無成本模型、無出場規則、未經樣本外驗證
    （spec FR-017）。呈現端必須標示，且不得與回測 KPI 並列。
    """
    enabled: bool = Field(
        default=False,
        description="總開關。**預設關閉**——關閉時逐筆、逐則、逐欄與本案實作前相同"
    )
    log_dir: str = Field(
        default="alert_log",
        description="JSONL 紀錄目錄（月分片）。**不得置於 data/ 之下**——該目錄整體 gitignored"
    )
    horizons: List[int] = Field(
        default_factory=lambda: [1, 3, 5],
        description="前瞻視窗（交易日）。T+N 為日線表中日期嚴格大於告警日的第 N 根"
    )
    min_samples: int = Field(
        default=20,
        ge=1,
        description="分群樣本數低於此值時標示為樣本不足，不顯示統計量（FR-018）"
    )

    @model_validator(mode="after")
    def _validate(self) -> "OutcomeTrackingConfig":
        if not self.horizons:
            raise ValueError("outcome_tracking.horizons 不得為空")
        if any(h < 1 for h in self.horizons):
            raise ValueError(f"outcome_tracking.horizons 必須皆為正整數：{self.horizons}")
        if list(self.horizons) != sorted(set(self.horizons)):
            raise ValueError(
                f"outcome_tracking.horizons 必須嚴格遞增且不重複：{self.horizons}"
            )
        cleaned = self.log_dir.strip()
        if not cleaned:
            raise ValueError("outcome_tracking.log_dir 不得為空")
        # 落入 data/ 會被 .gitignore 吞掉（該目錄整體忽略，理由為市場資料再散布
        # 疑慮）——紀錄將靜默不進版本庫，FR-008 的持久性保證隨之失效。
        # 這是「看起來能跑、實際上樣本無聲消失」的失效模式，故 fail-fast。
        normalized = cleaned.replace("\\", "/").lstrip("./")
        if normalized == "data" or normalized.startswith("data/"):
            raise ValueError(
                f"outcome_tracking.log_dir 不得位於 data/ 之下（得到 '{self.log_dir}'）："
                "該目錄整體被 .gitignore，紀錄會靜默不進版本庫"
            )
        return self


class MaAlertConfig(BaseModel):
    """
    均線觸價通知設定（spec 014）。

    **刻意不放進 `SingleStrategyParams`**：那承載的是會進入回測、會被 optimizer
    掃描、會影響訊號的「策略參數」；本區塊是「通知偏好」——不進回測、
    不影響任何訊號、不該被尋優，也不該稀釋 `ticker_overrides`
    （「這檔用不同的策略參數」）的語意。
    """
    ma_alerts_enabled: bool = Field(
        default=False,
        description="均線觸價通知總開關。**預設關閉**——新增的通知類型不應在使用者未要求時自行啟用"
    )
    monthly: MaLineConfig = Field(
        default_factory=lambda: MaLineConfig(period=20), description="月線"
    )
    quarterly: MaLineConfig = Field(
        default_factory=lambda: MaLineConfig(period=60), description="季線"
    )
    half_yearly: MaLineConfig = Field(
        default_factory=lambda: MaLineConfig(period=120), description="半年線"
    )
    yearly: MaLineConfig = Field(
        default_factory=lambda: MaLineConfig(period=240), description="年線"
    )
    # spec 015：推播訊號的事後表現追蹤（觀察層，與通知偏好同層、與策略參數分離）
    outcome_tracking: OutcomeTrackingConfig = Field(
        default_factory=OutcomeTrackingConfig,
        description="推播訊號的事後表現追蹤（spec 015 A 段，預設關閉）"
    )

    def enabled_periods(self) -> Dict[str, int]:
        """回傳已啟用之線別 → 週期；總開關關閉時回傳空 dict（呼叫端據此短路）。"""
        if not self.ma_alerts_enabled:
            return {}
        return {
            name: line.period
            for name, line in (
                ("monthly", self.monthly),
                ("quarterly", self.quarterly),
                ("half_yearly", self.half_yearly),
                ("yearly", self.yearly),
            )
            if line.enabled
        }

    def all_periods(self) -> Dict[str, int]:
        """回傳四條線的週期（不受開關影響）——供儀表板現況表使用（US4）。"""
        return {
            "monthly": self.monthly.period,
            "quarterly": self.quarterly.period,
            "half_yearly": self.half_yearly.period,
            "yearly": self.yearly.period,
        }


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
    # spec 014：均線觸價通知（通知偏好，與策略參數分離——見 MaAlertConfig docstring）
    alerts: MaAlertConfig = Field(default_factory=MaAlertConfig)

    @model_validator(mode="after")
    def _no_short_on_equity_overrides(self) -> "SystemConfig":
        """spec 003 SC-004 硬邊界（組態層）：對現貨 ticker 的 override 明設
        enable_short=True → fail-fast。default.enable_short 不受限（語意=期貨可做空，
        對現貨無效——引擎閘門另行保證現貨零空單）。"""
        futures_ids = {
            inst.id for inst in self.data.instruments
            if getattr(inst.asset_class, "value", inst.asset_class) == "futures"
        }
        for tk, params in self.strategy.ticker_overrides.items():
            if params.enable_short and tk not in futures_ids:
                raise ValueError(
                    f"ticker_overrides['{tk}'].enable_short=True 不合法：'{tk}' 非期貨 "
                    f"instrument（現貨做空已於 spec 003 決策排除；僅期貨可做空）"
                )
        return self

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

