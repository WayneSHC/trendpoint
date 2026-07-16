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
            volume_mult=volume_mult
        )
        # 消融測試停用 regime 時，保持原語意：濾網欄位存在且恆為 True
        if 'regime_ok' not in temp_df.columns:
            temp_df['regime_ok'] = True

        # 2. 模擬交易迴圈
        capital = self.initial_capital
        position_shares = 0.0 # 持有股數
        position_value = 0.0  # 部位市值
        
        equity_curve: List[Dict[str, Any]] = []
        trade_logs: List[Dict[str, Any]] = []
        
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
                    if bos_sig == 1:
                        is_entry = pm.check_entry_signal(
                            structure_sig=1, disabled_filters=disabled_filters, **common
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
                    # 看跌反轉（mss_sig == -1）為短側做空 → BLOCKED-003（long-only 暫不支援）

                if is_entry:
                    # 以次根開盤價成交 (滑價由成本元件計入成交價)
                    raw_price = row['open']
                    execution_price = cost_model.slip(raw_price, "buy")

                    # sizing 價格語意按資產類別（008b analyze M1）：
                    # equity = 成交價（現行語意：以成交價算最大可負擔股數）；
                    # futures = 訊號根收盤價（FR-004 保證金以訊號根名目值計，憲章 I）
                    sizing_price = float(sig_row['close']) if is_futures else execution_price
                    position_shares = sizer.size(capital, sizing_price)

                    if position_shares <= 0.0:
                        # 資金不足以買進最小單位（一張/一口），放棄此次訊號
                        current_equity = capital
                        equity_curve.append({
                            "datetime": current_time,
                            "capital": capital,
                            "position_value": 0.0,
                            "equity": current_equity
                        })
                        continue

                    cost = position_shares * execution_price
                    entry_costs = cost_model.entry_costs(execution_price, position_shares)
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
                        entry_log["sizing_price"] = sizing_price
                        margin_fn = getattr(sizer, "margin_per_lot", None)
                        entry_log["margin_used"] = (
                            margin_fn(sizing_price) * position_shares if margin_fn else 0.0
                        )
                    trade_logs.append(entry_log)
            
            # 若目前有持倉，動態更新與管理部位
            elif pm.is_active and position_shares > 0.0:
                bar_count = i - entry_bar_idx
                # 出場決策同樣以判定根（第 i-1 根收盤）判定、次根開盤成交；
                # 吊燈止損維持原策略定義：取判定根的前一根（持倉時 i>=3 必然存在）
                prev_ch_long = struct_row['chandelier_long']

                # 計算當下部位價值
                position_value = position_shares * row['close']

                # 執行部位管理
                event = pm.manage_position(
                    current_close=sig_row['close'],
                    current_atr=sig_row['atr'],
                    chandelier_long=prev_ch_long,
                    bar_count=bar_count,
                    time_limit=time_limit
                )
                
                # 處理減半平倉 (階段 1 止盈)
                if event is ExitEvent.STAGE1_HALF:
                    execution_price = cost_model.slip(row['open'], "sell")
                    # 賣出單位受最小單位約束（現股整張 / 期貨整數口，FR-012）；
                    # 若僅持有一張（口）無法分割，則跳過實際賣出，但 PositionManager
                    # 已將止損移至保本位——經濟意義等同「部位太小不拆分、轉零風險持倉」。
                    shares_to_sell = sizer.partial_units(position_shares, 0.5)

                    if shares_to_sell > 0.0:
                        revenue = shares_to_sell * execution_price

                        exit_costs = cost_model.exit_costs(execution_price, shares_to_sell)
                        commission = exit_costs.commission
                        tax = exit_costs.tax

                        if is_futures:
                            # 期貨：入帳 = 已實現點數損益 × 乘數 − 摩擦成本
                            realized = shares_to_sell * (execution_price - pm.entry_price) * point_value
                            capital += realized - exit_costs.total
                        else:
                            capital += revenue - (commission + tax)
                        position_shares -= shares_to_sell

                        half_log = {
                            "datetime": current_time,
                            "action": "SELL_HALF",
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
                
                # 處理全數平倉 (止損、時間止盈或剩餘部位吊燈止損)
                elif event in FULL_EXIT_EVENTS:
                    execution_price = cost_model.slip(row['open'], "sell")
                    shares_sold = position_shares
                    revenue = shares_sold * execution_price

                    exit_costs = cost_model.exit_costs(execution_price, shares_sold)
                    commission = exit_costs.commission
                    tax = exit_costs.tax

                    if is_futures:
                        realized = shares_sold * (execution_price - pm.entry_price) * point_value
                        capital += realized - exit_costs.total
                    else:
                        capital += revenue - (commission + tax)
                    position_shares = 0.0

                    full_log = {
                        "datetime": current_time,
                        "action": "SELL_ALL",
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
                    unrealized = position_shares * (row['close'] - pm.entry_price) * point_value
                    current_equity = capital + unrealized
                    position_value_now = position_shares * row['close'] * point_value  # 名目（曝險）
                else:
                    current_equity = capital
                    position_value_now = 0.0

                # spec 008b FR-011 爆倉防護：權益 ≤ 0 當根以當根收盤強制結清並終止
                if current_equity <= 0.0:
                    if position_shares > 0.0:
                        forced_price = row['close']
                        forced_costs = cost_model.exit_costs(forced_price, position_shares)
                        realized = position_shares * (forced_price - pm.entry_price) * point_value
                        capital += realized - forced_costs.total
                        forced_log = {
                            "datetime": current_time,
                            "action": "SELL_ALL",
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
                    equity_curve.append({
                        "datetime": current_time,
                        "capital": capital,
                        "position_value": 0.0,
                        "equity": current_equity
                    })
                    break  # 權益曲線截止於爆倉當根（FR-011）
            else:
                current_equity = capital + (position_shares * row['close'])
                position_value_now = position_shares * row['close']

            equity_curve.append({
                "datetime": current_time,
                "capital": capital,
                "position_value": position_value_now,
                "equity": current_equity
            })

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
