# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 歷史回測模擬核心 (Backtest Engine)

本模組實現了：
1. 逐筆 K 線 (Bar-by-Bar) 回測循環，嚴格防禦看前偏誤。
2. 自定義交易摩擦成本計算 (手續費、滑點、證券交易稅)。
3. 量化指標評估 (總報酬率、年化報酬率、勝率、盈虧比、最大資金回撤 MDD)。
4. 匯出回測歷程淨值曲線與交易日誌。
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Tuple
from ladder_system import (
    build_indicator_frame,
    PositionManager,
    ExitEvent,
    FULL_EXIT_EVENTS
)
from performance import compute_performance_metrics
from risk_gates import DrawdownGate, settlement_days
from trading_costs import EquityCostModel, EquitySizer


class FuturesBacktestNotSupportedError(ValueError):
    """期貨回測不支援之路徑護欄（008a 引入；008b 後僅組合路徑仍使用）。"""


def assert_backtestable(asset_class="equity") -> None:
    """範圍護欄：拒絕對期貨 instrument 回測。

    spec 008b 後，單標的路徑（BacktestEngine.run_backtest / run_backtest.py）
    已支援期貨、不再呼叫本函式；**組合路徑**（portfolio）的期貨元件接入
    不在 008b 範圍，仍以本函式擋下（008b analyze H1，憲章 II 邊界防護）。
    """
    ac = getattr(asset_class, "value", asset_class)
    if ac == "futures":
        raise FuturesBacktestNotSupportedError(
            "組合回測之期貨接入尚未支援（008b 僅單標的）；期貨組合待後續 spec。"
        )


class BacktestEngine:
    """
    歷史回測引擎類別，模擬策略執行並計算績效指標。
    """
    def __init__(self,
                 initial_capital: float = 1000000.0,
                 commission_rate: float = 0.001425,
                 tax_rate: float = 0.003,
                 slippage_rate: float = 0.0005,
                 lot_size: int = 1000,
                 config = None):
        """
        參數:
            initial_capital (float): 初始資金 (預設 1,000,000 元)
            commission_rate (float): 單邊手續費率 (預設台股現股 0.1425%)
            tax_rate (float): 證券交易稅率 (預設台股現股賣出 0.3%)
            slippage_rate (float): 單邊滑點比例 (預設 0.05%)
            lot_size (int): 整股交易單位 (台股一張 1000 股)，買進股數向下取整至此倍數
            config (SystemConfig, optional): 全域配置規格物件，若傳入將覆蓋上述個別設定值
        """
        if config is not None:
            self.initial_capital = config.backtest.init_capital
            self.commission_rate = config.trading_cost.commission_rate
            self.tax_rate = config.trading_cost.tax_rate
            self.slippage_rate = config.trading_cost.slip_rate
            self.lot_size = config.trading_cost.lot_size
        else:
            self.initial_capital = initial_capital
            self.commission_rate = commission_rate
            self.tax_rate = tax_rate
            self.slippage_rate = slippage_rate
            self.lot_size = lot_size

    def round_to_lot(self, shares: float) -> float:
        """
        將股數向下取整至整股單位之倍數（台股整股市場一張 1000 股）。
        回測若允許無限分割股數，會嚴重高估小資金策略的可執行性。
        """
        if self.lot_size <= 1:
            return float(shares)
        return float(int(shares // self.lot_size) * self.lot_size)

    def run_backtest(self,
                     df: pd.DataFrame,
                     atr_period: int = 14,
                     k: float = 2.0,
                     ch_period: int = 22,
                     ch_multiplier: float = 3.0,
                     time_limit: int = 15,
                     use_adx_filter: bool = True,
                     adx_period: int = 14,
                     adx_threshold: float = 20.0,
                     use_ma_filter: bool = True,
                     ma_period: int = 200,
                     use_er_filter: bool = False,
                     er_period: int = 10,
                     er_threshold: float = 0.3,
                     use_fvg: bool = False,
                     fvg_lookback: int = 3,
                     swing_n: int = 2,
                     volume_mult: float = 1.5,
                     mss_reversal_entry: bool = False,
                     asset_class: str = "equity",
                     cost_model = None,
                     sizer = None,
                     point_value: float = 1.0,
                     enable_short: bool = False,
                     use_bos_volume: bool = False,
                     bos_volume_mult: float = 1.5,
                     bos_volume_period: int = 20,
                     use_dd_gate: bool = False,
                     dd_limit_pct: float = 0.20,
                     dd_resume_pct: float = 0.10,
                     use_settlement_gate: bool = False,
                     disabled_filters: frozenset = frozenset(),
                     verbose: bool = True) -> Dict[str, Any]:
        """
        執行歷史回測。

        參數:
            df (pd.DataFrame): 包含 datetime 索引與標準 OHLCV 欄位之 DataFrame
            atr_period (int): ATR 週期 (預設 14)
            k (float): 階梯觸發的 ATR 乘數 (預設 2.0)
            ch_period (int): 吊燈止損滾動週期 (預設 22)
            ch_multiplier (int): 吊燈止損 ATR 乘數 (預設 3.0)
            time_limit (int): 時間限制止盈根數 (預設 15)
            use_adx_filter (bool): 啟用 ADX 趨勢強度濾網 (盤整不進場)
            adx_period (int): ADX 週期
            adx_threshold (float): ADX 低於此值視為盤整
            use_ma_filter (bool): 啟用長均線大週期濾網 (價格低於長均線不做多)
            ma_period (int): 長均線回看期數 (日線預設 200)
            use_er_filter (bool): 啟用 Kaufman ER 噪音濾網
            er_period (int): ER 週期
            er_threshold (float): ER 低於此值視為高噪音
            use_fvg (bool): 啟用 FVG 確認（MSS 須近 fvg_lookback 根內有同向缺口，spec 002）
            fvg_lookback (int): FVG 回看根數 M (預設 3)
            asset_class (str): "equity"（預設）或 "futures"（spec 008b：期貨會計語意）
            cost_model (CostModel, optional): 摩擦成本元件；None → 現股元件（現行語意，位元不變）
            sizer (PositionSizer, optional): 部位 sizing 元件；None → 現股整張元件
            point_value (float): 每點價值（現股 1.0；期貨 = 契約乘數，P&L = units×Δ價×此值）
            enable_short (bool): 期貨做空開關（spec 003）；僅 asset_class="futures" 時生效
                ——現貨結構上不存在空方路徑（任何旗標組合下 equity 零空單）
            use_bos_volume (bool): 啟用 BOS 續勢進場的量能確認（spec 012）；預設關閉、逐筆不變
            bos_volume_mult (float): 量能放大倍數門檻（成交量 > 均量 × 此值）
            bos_volume_period (int): 均量回看根數
            use_dd_gate (bool): 啟用回撤閘門（spec 013）；預設關閉，關閉時逐筆逐根位元不變
            dd_limit_pct (float): 回撤達此幅度停止開新倉（正數表幅度）
            dd_resume_pct (float): 回撤回升至此幅度以內解除封鎖；須嚴格小於 dd_limit_pct
            use_settlement_gate (bool): 啟用結算日封鎖（spec 013）；僅期貨生效，現貨無效果不報錯
            disabled_filters (frozenset): 消融測試用，可停用 'structure'/'momentum'/'trend'/'volatility'/'global'/'regime'/'fvg'
            verbose (bool): 是否輸出進度訊息

        回傳:
            Dict: 包含績效指標摘要 (summary)、淨值曲線 (equity_curve) 與交易日誌 (trades)
        """
        # spec 008b：成本/sizing 元件注入——預設現股元件（既有呼叫零改動、現貨路徑位元不變）
        if cost_model is None:
            cost_model = EquityCostModel(self.commission_rate, self.tax_rate, self.slippage_rate)
        if sizer is None:
            sizer = EquitySizer(self.commission_rate, self.lot_size)
        is_futures = (getattr(asset_class, "value", asset_class) == "futures")
        blown_up = False

        # spec 011 FR-008：期貨 sizing／稅以未調整參考價為名目值基準，缺欄即硬失敗。
        # 失敗要早——擋在指標計算與逐根迴圈之前，不留半份結果。
        # **不得**加入 fallback 分支：所有期貨來源皆有產出義務（FR-009），故缺欄
        # 唯一代表「本功能實作前建立的舊資料」；若沉默退回調整後價，保證金低估
        # 的 bug 會無聲重現，而其症狀（爆倉）極易被誤判為策略問題。
        if is_futures:
            _needed = ("unadj_open", "unadj_high", "unadj_low", "unadj_close")
            _missing = [c for c in _needed if c not in df.columns]
            if _missing:
                raise ValueError(
                    f"期貨回測資料缺少未調整參考價欄位 {_missing}（需 {list(_needed)}）。"
                    "back-adjust 連續序列的價位水準不等於當年真實市價，不可用於"
                    "口數/保證金/期交稅的名目值計算。請執行 python run_ingestion.py "
                    "重建連續層以補齊欄位（spec 011 FR-008）。"
                )

        def cost_basis_price(bar_row, side, exec_price, *, field='open'):
            """摩擦成本（期交稅）的價格基準（spec 011 FR-005）。

            期貨：取同根**未調整**價再套同一滑價——稅基是成交契約金額，須反映
            當年真實市價；back-adjust 後的水位會使稅額失真（早年負價位甚至算出
            負稅額）。滑價為點數加減，故兩基準的偏移量一致、自洽。
            現貨：維持成交價（008b 位元不變承諾）。
            每口定額手續費不吃價格，不受本函式影響。
            """
            if not is_futures:
                return exec_price
            return cost_model.slip(float(bar_row[f'unadj_{field}']), side)

        if verbose:
            print("開始進行策略回測...")

        # 消融：'fvg' 在 disabled_filters 時關閉 FVG 確認，該次回測回到 spec 001 基準
        # （比照 include_regime 的建構期短路模式）
        effective_use_fvg = use_fvg and ('fvg' not in disabled_filters)

        # 1. 預先計算所有技術指標（正典組裝入口：ladder_system.build_indicator_frame）
        temp_df = build_indicator_frame(
            df,
            structure_period=10,
            atr_period=atr_period,
            ladder_k=k,
            chandelier_period=ch_period,
            chandelier_multiplier=ch_multiplier,
            include_regime=('regime' not in disabled_filters),
            regime_kwargs=dict(
                use_adx=use_adx_filter, adx_period=adx_period, adx_threshold=adx_threshold,
                use_ma=use_ma_filter, ma_period=ma_period,
                use_er=use_er_filter, er_period=er_period, er_threshold=er_threshold
            ),
            use_fvg=effective_use_fvg,
            fvg_lookback=fvg_lookback,
            swing_n=swing_n,
            volume_mult=volume_mult,
            use_bos_volume=use_bos_volume,
            bos_volume_mult=bos_volume_mult,
            bos_volume_period=bos_volume_period
        )
        # 消融測試停用 regime 時，保持原語意：濾網欄位存在且恆為 True
        if 'regime_ok' not in temp_df.columns:
            temp_df['regime_ok'] = True
        if 'regime_ok_short' not in temp_df.columns:
            temp_df['regime_ok_short'] = True
        # spec 012：濾網關閉時不輸出該欄，補為恆 True（同 regime_ok 的既有慣例）
        if 'bos_volume_ok' not in temp_df.columns:
            temp_df['bos_volume_ok'] = True

        # 1b. 進場閘門（spec 013）：路徑相依風控，**只擋開新倉，不碰任何出場路徑**。
        # 消融語意與既有濾網一致：鍵出現在 disabled_filters 時該閘門視為恆開。
        # 結算日閘門僅對期貨成立（FR-007）——現貨不建立集合，故對其零效果且不報錯。
        dd_gate_active = use_dd_gate and ('dd_gate' not in disabled_filters)
        settlement_gate_active = (
            use_settlement_gate and is_futures and ('settlement_gate' not in disabled_filters)
        )
        # 條件輸出欄的存在條件取「**實際生效**」而非「參數為真」：對現貨啟用結算日
        # 閘門時不輸出 block_reason，使 SC-008「與未啟用完全相同」在欄位集層級亦成立。
        # 一欄全空字串只會讓使用者誤以為有風控在保護他（research.md D7 的同一顧慮）。
        gates_effective = use_dd_gate or (use_settlement_gate and is_futures)
        dd_gate = (
            DrawdownGate(initial_equity=self.initial_capital,
                         limit_pct=dd_limit_pct, resume_pct=dd_resume_pct)
            if dd_gate_active else None
        )
        settlement_set = settlement_days(temp_df.index) if settlement_gate_active else frozenset()

        # 2. 模擬交易迴圈
        capital = self.initial_capital
        position_shares = 0.0 # 持有股數
        position_value = 0.0  # 部位市值

        equity_curve: List[Dict[str, Any]] = []
        trade_logs: List[Dict[str, Any]] = []

        def record_equity(ts, cap: float, pos_val: float, eq: float, reason: str) -> None:
            """寫入權益曲線一根，並在同一處推進回撤閘門狀態。

            **這是 FR-004（憲章原則 I）的落點**：閘門必須以「本根權益」在本根
            **結束時**更新，供**下一根**開頭讀取。搬到迴圈開頭即構成看前偏誤
            （進場判定會用到含當根收盤價的權益）。tests/test_lookahead_bias.py
            的 SC-004 專門守此點，勿為了「少一個函式」而把它拆回迴圈裡。

            集中在此還有第二個理由：權益曲線有四個 append 點（多方口數不足、
            空方口數不足、爆倉截止、正常尾端），其中兩個以 `continue` 跳過迴圈
            尾端。閘門若只掛在尾端，那兩根就會漏更新。
            """
            row = {"datetime": ts, "capital": cap, "position_value": pos_val, "equity": eq}
            if gates_effective:
                row["block_reason"] = reason
            equity_curve.append(row)
            if dd_gate is not None:
                dd_gate.update(eq)

        pm = PositionManager()
        entry_bar_idx = 0
        
        # 偵測是否是日線資料 (判斷時間中位數間隔)
        is_daily = False
        if len(temp_df) > 1:
            median_interval = pd.Series(temp_df.index).diff().median()
            is_daily = median_interval >= pd.Timedelta(days=1)
            
        for i in range(1, len(temp_df)):
            current_time = temp_df.index[i]
            row = temp_df.iloc[i]

            # 憲法 I 成交規則：訊號於第 i-1 根（已完整收盤）判定，
            # 於第 i 根開盤價成交。判定邏輯本身維持原策略定義：
            # 濾網用判定根 sig_row、結構訊號用判定根的前一根 struct_row
            sig_row = temp_df.iloc[i - 1]
            struct_row = temp_df.iloc[i - 2] if i >= 2 else None

            # 進場閘門狀態（spec 013）：於本根**開頭**取值——dd_gate.blocked 反映
            # 第 i-1 根為止的權益（見 record_equity 的說明）。逐根記錄而非只在
            # 「確實擋掉一筆進場」時記錄：block_reason 是狀態軌跡，不是事件日誌，
            # 這讓封鎖／解除的轉折可由輸出直接指出確切根索引。
            block_reasons: List[str] = []
            if dd_gate is not None and dd_gate.blocked:
                block_reasons.append("drawdown")
            if settlement_set and current_time.date() in settlement_set:
                block_reasons.append("settlement")
            block_reason = "+".join(block_reasons)
            gate_ok = not block_reasons

            # 若目前無持倉，檢查進場訊號
            if not pm.is_active and position_shares == 0.0:
                # 全域濾網：三關價（價格在中關價之上做多）+ 市況濾網 (ADX/長均線/ER)
                global_ok = (sig_row['close'] > sig_row['mid_price']) and bool(sig_row['regime_ok'])

                # 結構訊號分流（spec 007）：BOS 續勢 vs MSS 反轉，兩條獨立進場路徑。
                # mss_reversal_entry=False 時僅走 BOS 續勢——因 007 前 MSS 為 BOS 子集，
                # 此設定恰精確復現 007 前的進場行為（回歸/消融錨點，見 tasks T019）。
                is_entry = False
                entry_reason = "滿足多重確認進場做多"
                if struct_row is not None:
                    bos_sig = int(struct_row['bos_signal'])
                    mss_sig = int(struct_row['mss_signal'])
                    common = dict(
                        close=sig_row['close'], open_val=sig_row['open'],
                        daily_open=sig_row['daily_open'], vwap=sig_row['vwap'],
                        atr=sig_row['atr'], candle_high=sig_row['high'],
                        candle_low=sig_row['low'], global_filter_ok=global_ok,
                        is_daily=is_daily,
                    )
                    # (1) BOS 續勢進場：全維度濾網（語意同 007 前）
                    # spec 012：量能確認**只**接在續勢分支；取 sig_row（判定根）
                    # 而非 struct_row（iloc[i-2]，會比其餘四道確認多一根延遲）
                    if bos_sig == 1:
                        is_entry = pm.check_entry_signal(
                            structure_sig=1, disabled_filters=disabled_filters,
                            volume_ok=bool(sig_row['bos_volume_ok']), **common
                        )
                    # (2) MSS 反轉進場（長側）：放寬順勢確認(trend)與 200MA regime，
                    #     但**保留三關價**（close>mid_price，spec 003 強調的空頭防線）——
                    #     反轉的 global 濾網只留三關價、去掉 regime（research D6 修訂）。
                    if (not is_entry) and mss_reversal_entry and mss_sig == 1:
                        reversal_filters = disabled_filters | frozenset({'trend'})
                        rev_common = {**common, 'global_filter_ok': bool(sig_row['close'] > sig_row['mid_price'])}
                        if pm.check_entry_signal(
                            structure_sig=1, disabled_filters=reversal_filters, **rev_common
                        ):
                            is_entry = True
                            entry_reason = "MSS 反轉進場做多"
                    # 看跌訊號（bos/mss == -1）→ 空方進場評估（spec 003，原 BLOCKED-003 已解封）

                # 空方進場（spec 003）：僅期貨且 enable_short 時可達（現貨結構硬邊界）。
                # 三關價互斥裁決：多方 global 含 close>mid、空方 global 含 close<mid，
                # 同根多空訊號自然只有一側能過（消融 'global' 時多方優先）。
                short_entry = False
                short_reason = "滿足多重確認進場做空"
                if (not is_entry) and enable_short and is_futures and struct_row is not None:
                    below_mid = bool(sig_row['close'] < sig_row['mid_price'])
                    # (1) 空頭 BOS 續勢：全維度鏡像（global = 三關價之下 AND 空方市況濾網）
                    short_global_ok = below_mid and bool(sig_row['regime_ok_short'])
                    if bos_sig == -1:
                        short_entry = pm.check_entry_signal(
                            structure_sig=-1, direction=-1,
                            disabled_filters=disabled_filters,
                            volume_ok=bool(sig_row['bos_volume_ok']),   # spec 012：多空同式
                            **{**common, 'global_filter_ok': short_global_ok}
                        )
                    # (2) 看跌 MSS 反轉（spec 007 短腿解封）：鏡像多方反轉 profile——
                    #     放寬順勢確認(trend)、免市況 regime、global 僅留三關價（close<mid）
                    if (not short_entry) and mss_reversal_entry and mss_sig == -1:
                        reversal_filters = disabled_filters | frozenset({'trend'})
                        rev_common_s = {**common, 'global_filter_ok': below_mid}
                        if pm.check_entry_signal(
                            structure_sig=-1, direction=-1,
                            disabled_filters=reversal_filters, **rev_common_s
                        ):
                            short_entry = True
                            short_reason = "MSS 反轉進場做空"

                # ---- 進場閘門接線點（spec 013 FR-002，本案最高風險處）----
                # 位置是契約的一部分：**必須**在 `if not pm.is_active` 區塊內、
                # `if is_entry:` 之前，且**只**改寫兩個進場旗標。
                # 禁止改成迴圈開頭 `continue`——那會連出場判定與權益 append 一起
                # 跳過，封鎖期間的停損不會執行、權益曲線出現斷點（SC-003 守此點）。
                # 禁止折進 global_ok——消融將無法區分封鎖來源，封鎖原因也無從記錄。
                # 禁止塞進 check_entry_signal——該函式是無狀態純判定，混入路徑相依
                # 狀態會破壞其真值表可測性。
                # 閘門對多空無方向性：兩個旗標同時被 AND（SC-010 守此點）。
                if not gate_ok:
                    is_entry = False
                    short_entry = False

                if is_entry:
                    # 以次根開盤價成交 (滑價由成本元件計入成交價)
                    raw_price = row['open']
                    execution_price = cost_model.slip(raw_price, "buy")

                    # sizing 價格語意按資產類別（008b analyze M1）：
                    # equity = 成交價（現行語意：以成交價算最大可負擔股數）；
                    # futures = 訊號根**未調整**收盤價（spec 011 FR-004：名目值＝價位×乘數，
                    # 須用當年真實市價；back-adjust 後的水位會使保證金低估數十倍）
                    sizing_price = float(sig_row['unadj_close']) if is_futures else execution_price
                    position_shares = sizer.size(capital, sizing_price)

                    if position_shares <= 0.0:
                        # 資金不足以買進最小單位（一張/一口），放棄此次訊號
                        current_equity = capital
                        record_equity(current_time, capital, 0.0, current_equity, block_reason)
                        continue

                    cost = position_shares * execution_price
                    entry_costs = cost_model.entry_costs(
                        cost_basis_price(row, "buy", execution_price), position_shares)
                    fee = entry_costs.commission
                    if is_futures:
                        # 期貨：不付名目、僅扣摩擦成本（保證金為佔用而非支出）
                        capital -= entry_costs.total
                    else:
                        capital -= (cost + fee)

                    # 設定部位管理器參數
                    pm.is_active = True
                    pm.entry_price = execution_price
                    pm.position_size = 1.0 # 初始持倉比例為 100%
                    pm.stop_loss = execution_price - 2.0 * sig_row['atr']
                    pm.stage = 1
                    pm.direction = 1
                    entry_bar_idx = i
                    
                    entry_log = {
                        "datetime": current_time,
                        "action": "BUY",
                        "shares": position_shares,
                        "price": execution_price,
                        "commission": fee,
                        "tax": entry_costs.tax,
                        "cash": capital,
                        "event": entry_reason
                    }
                    if is_futures:
                        # 期貨紀錄擴充（FR-006/data-model）：point_value 供績效配對換算 NT$；
                        # margin_used 為佔用保證金（sizing 約束之稽核欄位）
                        entry_log["point_value"] = point_value
                        # spec 011：sizing_price 語意為**未調整**訊號根收盤；
                        # 併記調整後值供驗收比對兩基準之落差
                        entry_log["sizing_price"] = sizing_price
                        entry_log["sizing_price_adj"] = float(sig_row['close'])
                        margin_fn = getattr(sizer, "margin_per_lot", None)
                        entry_log["margin_used"] = (
                            margin_fn(sizing_price) * position_shares if margin_fn else 0.0
                        )
                    trade_logs.append(entry_log)

                elif short_entry:
                    # 空方進場（spec 003）：賣出開倉於次根開盤，滑價不利向下；
                    # 僅期貨可達（成本/口數 = 008b 元件，天然對稱、無借券概念）
                    raw_price = row['open']
                    execution_price = cost_model.slip(raw_price, "sell")
                    # FR-007 + spec 011 FR-004：sizing 用訊號根**未調整**收盤（多空對稱）
                    sizing_price = float(sig_row['unadj_close'])
                    position_shares = sizer.size(capital, sizing_price)

                    if position_shares <= 0.0:
                        current_equity = capital
                        record_equity(current_time, capital, 0.0, current_equity, block_reason)
                        continue

                    entry_costs = cost_model.entry_costs(
                        cost_basis_price(row, "sell", execution_price), position_shares)
                    capital -= entry_costs.total   # 期貨：僅扣摩擦成本、不付名目

                    pm.is_active = True
                    pm.entry_price = execution_price
                    pm.position_size = 1.0
                    pm.stop_loss = execution_price + 2.0 * sig_row['atr']  # 空方止損在上方
                    pm.stage = 1
                    pm.direction = -1
                    entry_bar_idx = i

                    short_log = {
                        "datetime": current_time,
                        "action": "SELL_SHORT",
                        "shares": position_shares,
                        "price": execution_price,
                        "commission": entry_costs.commission,
                        "tax": entry_costs.tax,
                        "cash": capital,
                        "event": short_reason,
                        "point_value": point_value,
                        "sizing_price": sizing_price,          # spec 011：未調整基準
                        "sizing_price_adj": float(sig_row['close']),
                    }
                    margin_fn = getattr(sizer, "margin_per_lot", None)
                    short_log["margin_used"] = (
                        margin_fn(sizing_price) * position_shares if margin_fn else 0.0
                    )
                    trade_logs.append(short_log)
            
            # 若目前有持倉，動態更新與管理部位
            elif pm.is_active and position_shares > 0.0:
                bar_count = i - entry_bar_idx
                # 出場決策同樣以判定根（第 i-1 根收盤）判定、次根開盤成交；
                # 吊燈止損維持原策略定義：取判定根的前一根（持倉時 i>=3 必然存在）
                prev_ch_long = struct_row['chandelier_long']

                # 計算當下部位價值
                position_value = position_shares * row['close']

                # 執行部位管理（spec 003：空方持倉需空方吊燈——同為判定根前一根之值）
                event = pm.manage_position(
                    current_close=sig_row['close'],
                    current_atr=sig_row['atr'],
                    chandelier_long=prev_ch_long,
                    bar_count=bar_count,
                    time_limit=time_limit,
                    chandelier_short=struct_row['chandelier_short']
                )
                
                # 處理減半平倉 (階段 1 止盈)；空方 = 部分回補（spec 003）
                if event is ExitEvent.STAGE1_HALF:
                    is_short_pos = (pm.direction == -1)
                    execution_price = cost_model.slip(row['open'], "buy" if is_short_pos else "sell")
                    # 賣出單位受最小單位約束（現股整張 / 期貨整數口，FR-012）；
                    # 若僅持有一張（口）無法分割，則跳過實際賣出，但 PositionManager
                    # 已將止損移至保本位——經濟意義等同「部位太小不拆分、轉零風險持倉」。
                    shares_to_sell = sizer.partial_units(position_shares, 0.5)

                    if shares_to_sell > 0.0:
                        revenue = shares_to_sell * execution_price

                        exit_costs = cost_model.exit_costs(
                            cost_basis_price(row, "buy" if is_short_pos else "sell",
                                             execution_price), shares_to_sell)
                        commission = exit_costs.commission
                        tax = exit_costs.tax

                        if is_futures:
                            # 期貨：入帳 = 已實現點數損益 × 乘數 − 摩擦成本（方向因子）
                            if is_short_pos:
                                realized = shares_to_sell * (pm.entry_price - execution_price) * point_value
                            else:
                                realized = shares_to_sell * (execution_price - pm.entry_price) * point_value
                            capital += realized - exit_costs.total
                        else:
                            capital += revenue - (commission + tax)
                        position_shares -= shares_to_sell

                        half_log = {
                            "datetime": current_time,
                            "action": "COVER_HALF" if is_short_pos else "SELL_HALF",
                            "shares": shares_to_sell,
                            "price": execution_price,
                            "commission": commission,
                            "tax": tax,
                            "cash": capital,
                            "event": event.value
                        }
                        if is_futures:
                            half_log["point_value"] = point_value
                        trade_logs.append(half_log)
                
                # 處理全數平倉 (止損、時間止盈或剩餘部位吊燈止損)；空方 = 全回補
                elif event in FULL_EXIT_EVENTS:
                    is_short_pos = (pm.direction == -1)
                    execution_price = cost_model.slip(row['open'], "buy" if is_short_pos else "sell")
                    shares_sold = position_shares
                    revenue = shares_sold * execution_price

                    exit_costs = cost_model.exit_costs(
                        cost_basis_price(row, "buy" if is_short_pos else "sell",
                                         execution_price), shares_sold)
                    commission = exit_costs.commission
                    tax = exit_costs.tax

                    if is_futures:
                        if is_short_pos:
                            realized = shares_sold * (pm.entry_price - execution_price) * point_value
                        else:
                            realized = shares_sold * (execution_price - pm.entry_price) * point_value
                        capital += realized - exit_costs.total
                    else:
                        capital += revenue - (commission + tax)
                    position_shares = 0.0

                    full_log = {
                        "datetime": current_time,
                        "action": "COVER_ALL" if is_short_pos else "SELL_ALL",
                        "shares": shares_sold,
                        "price": execution_price,
                        "commission": commission,
                        "tax": tax,
                        "cash": capital,
                        "event": event.value
                    }
                    if is_futures:
                        full_log["point_value"] = point_value
                    trade_logs.append(full_log)
            
            # 更新淨值曲線（期貨：權益 = 現金 + 未實現點數損益×乘數；現貨：現金 + 市值）
            if is_futures:
                if position_shares > 0.0:
                    if pm.direction == -1:
                        # 空方未實現：價格下跌獲利（spec 003 方向因子）
                        unrealized = position_shares * (pm.entry_price - row['close']) * point_value
                    else:
                        unrealized = position_shares * (row['close'] - pm.entry_price) * point_value
                    current_equity = capital + unrealized
                    position_value_now = position_shares * row['close'] * point_value  # 名目（曝險）
                else:
                    current_equity = capital
                    position_value_now = 0.0

                # spec 008b FR-011 爆倉防護：權益 ≤ 0 當根以當根收盤強制結清並終止
                # （spec 003：空方爆倉由上漲觸發，強制回補 COVER_ALL，機制不變）
                if current_equity <= 0.0:
                    if position_shares > 0.0:
                        forced_price = row['close']
                        forced_costs = cost_model.exit_costs(
                            cost_basis_price(row, "buy" if pm.direction == -1 else "sell",
                                             forced_price, field='close'),
                            position_shares)
                        if pm.direction == -1:
                            realized = position_shares * (pm.entry_price - forced_price) * point_value
                            forced_action = "COVER_ALL"
                        else:
                            realized = position_shares * (forced_price - pm.entry_price) * point_value
                            forced_action = "SELL_ALL"
                        capital += realized - forced_costs.total
                        forced_log = {
                            "datetime": current_time,
                            "action": forced_action,
                            "shares": position_shares,
                            "price": forced_price,
                            "commission": forced_costs.commission,
                            "tax": forced_costs.tax,
                            "cash": capital,
                            "event": "爆倉強制結清 (FORCED_LIQUIDATION)",
                            "point_value": point_value,
                        }
                        trade_logs.append(forced_log)
                        position_shares = 0.0
                        pm.is_active = False
                    blown_up = True
                    current_equity = capital
                    record_equity(current_time, capital, 0.0, current_equity, block_reason)
                    break  # 權益曲線截止於爆倉當根（FR-011）
            else:
                current_equity = capital + (position_shares * row['close'])
                position_value_now = position_shares * row['close']

            record_equity(current_time, capital, position_value_now, current_equity, block_reason)

        # 3. 整理回測結果與統計指標
        df_equity = pd.DataFrame(equity_curve).set_index("datetime")
        df_trades = pd.DataFrame(trade_logs)
        
        summary = self._calculate_metrics(df_equity, df_trades)
        if is_futures:
            summary["blown_up"] = blown_up

        return {
            "summary": summary,
            "equity_curve": df_equity,
            "trades": df_trades
        }

    def _calculate_metrics(self, df_equity: pd.DataFrame, df_trades: pd.DataFrame) -> Dict[str, Any]:
        """
        計算量化交易績效指標。
        """
        if df_equity.empty:
            return {}

        final_equity = df_equity['equity'].iloc[-1]

        # 完整績效指標 (Sharpe / Sortino / Calmar / CAGR / 年化波動 / 曝險時間)
        perf = compute_performance_metrics(
            equity=df_equity['equity'],
            initial_capital=self.initial_capital,
            position_value=df_equity.get('position_value')
        )

        total_return = perf.get('total_return', (final_equity - self.initial_capital) / self.initial_capital)
        mdd = perf.get('max_drawdown', 0.0)

        # 交易統計
        total_trades = 0
        win_rate = 0.0
        profit_factor = 0.0
        trade_returns: List[float] = []

        if not df_trades.empty:
            # 以進場 (BUY) 與全平倉 (SELL_ALL) 作為完整交易配對進行統計
            buy_trades = df_trades[df_trades['action'] == 'BUY']
            sell_all_trades = df_trades[df_trades['action'] == 'SELL_ALL']
            
            paired_trades: List[Tuple[float, float]] = []
            
            # 配對買入與賣出
            for idx, buy_row in buy_trades.iterrows():
                buy_time = buy_row['datetime']
                # 尋找在此進場時間後的第一個 SELL_ALL
                later_sells = sell_all_trades[sell_all_trades['datetime'] > buy_time]
                if not later_sells.empty:
                    sell_row = later_sells.iloc[0]
                    sell_time = sell_row['datetime']
                    
                    # 取得該筆交易的中途 SELL_HALF (若有)
                    half_sells = df_trades[(df_trades['action'] == 'SELL_HALF') & 
                                           (df_trades['datetime'] > buy_time) & 
                                           (df_trades['datetime'] < sell_time)]
                                           
                    # 計算總投入成本與總回收金額
                    # spec 008b：期貨紀錄帶 point_value（點→NT$ 換算）；現貨無此欄 → 1.0
                    #（×1.0 對正浮點為位元恆等，現貨配對數字不變）
                    pv_buy = buy_row.get('point_value', 1.0)
                    initial_cost = buy_row['shares'] * buy_row['price'] * pv_buy + buy_row['commission']

                    total_revenue = 0.0
                    # 期貨進場邊亦有期交稅（現貨進場 tax=0.0，+0.0 位元恆等）
                    total_friction = sell_row['commission'] + sell_row['tax'] + buy_row['tax']

                    if not half_sells.empty:
                        for _, half_row in half_sells.iterrows():
                            total_revenue += half_row['shares'] * half_row['price'] * half_row.get('point_value', 1.0)
                            total_friction += half_row['commission'] + half_row['tax']

                    # 使用 SELL_ALL 實際記錄之賣出股數（整股取整後不必然等於買入股數之半）
                    total_revenue += sell_row['shares'] * sell_row['price'] * sell_row.get('point_value', 1.0)

                    profit = total_revenue - initial_cost - total_friction
                    paired_trades.append((profit, profit / initial_cost))

            # spec 003：空方配對（SELL_SHORT → COVER_ALL，含中途 COVER_HALF）。
            # 多方配對段（上方）逐字不動；空方 profit = 進場名目 − 回補名目 − 摩擦。
            short_entry_trades = df_trades[df_trades['action'] == 'SELL_SHORT']
            cover_all_trades = df_trades[df_trades['action'] == 'COVER_ALL']
            for idx, s_row in short_entry_trades.iterrows():
                s_time = s_row['datetime']
                later_covers = cover_all_trades[cover_all_trades['datetime'] > s_time]
                if not later_covers.empty:
                    c_row = later_covers.iloc[0]
                    c_time = c_row['datetime']
                    half_covers = df_trades[(df_trades['action'] == 'COVER_HALF') &
                                            (df_trades['datetime'] > s_time) &
                                            (df_trades['datetime'] < c_time)]

                    pv_s = s_row.get('point_value', 1.0)
                    entry_value = s_row['shares'] * s_row['price'] * pv_s
                    denom = entry_value + s_row['commission']

                    exit_value = c_row['shares'] * c_row['price'] * c_row.get('point_value', 1.0)
                    total_friction = (c_row['commission'] + c_row['tax']
                                      + s_row['commission'] + s_row['tax'])
                    if not half_covers.empty:
                        for _, h_row in half_covers.iterrows():
                            exit_value += h_row['shares'] * h_row['price'] * h_row.get('point_value', 1.0)
                            total_friction += h_row['commission'] + h_row['tax']

                    profit = entry_value - exit_value - total_friction
                    paired_trades.append((profit, profit / denom))

            total_trades = len(paired_trades)
            if total_trades > 0:
                trade_returns = [r for _, r in paired_trades]
                profits = [p for p, _ in paired_trades if p > 0]
                losses = [p for p, _ in paired_trades if p <= 0]

                wins = len(profits)
                win_rate = wins / total_trades

                sum_profits = sum(profits)
                sum_losses = abs(sum(losses))

                # 計算盈虧比 (Profit Factor)
                profit_factor = sum_profits / sum_losses if sum_losses > 0 else (np.inf if sum_profits > 0 else 1.0)

        summary = {
            "initial_capital": self.initial_capital,
            "final_equity": final_equity,
            "total_return": total_return,
            "max_drawdown": mdd,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            # 蒙地卡羅交易重抽所需之逐筆交易報酬率序列
            "trade_returns": trade_returns,
        }
        # 併入完整風險調整後績效指標
        summary.update({k: v for k, v in perf.items() if k not in summary})
        return summary
