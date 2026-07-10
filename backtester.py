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
    calculate_tr,
    calculate_atr,
    calculate_vwap,
    detect_market_structure,
    calculate_ladder_levels,
    calculate_chandelier_exit,
    calculate_regime_filter,
    PositionManager
)
from performance import compute_performance_metrics

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
            disabled_filters (frozenset): 消融測試用，可停用 'structure'/'momentum'/'trend'/'volatility'/'global'/'regime'
            verbose (bool): 是否輸出進度訊息

        回傳:
            Dict: 包含績效指標摘要 (summary)、淨值曲線 (equity_curve) 與交易日誌 (trades)
        """
        if verbose:
            print("開始進行策略回測...")

        # 1. 預先計算所有技術指標，並進行時序移位以防看前偏誤
        temp_df = df.copy()
        tr = calculate_tr(temp_df['high'], temp_df['low'], temp_df['close'])
        temp_df['atr'] = calculate_atr(tr, period=atr_period)
        temp_df['vwap'] = calculate_vwap(temp_df)

        # 市況濾網 (Regime Filter)：ADX 趨勢強度 + 長均線方向 + ER 噪音
        if 'regime' in disabled_filters:
            temp_df['regime_ok'] = True
        else:
            temp_df['regime_ok'] = calculate_regime_filter(
                temp_df,
                use_adx=use_adx_filter, adx_period=adx_period, adx_threshold=adx_threshold,
                use_ma=use_ma_filter, ma_period=ma_period,
                use_er=use_er_filter, er_period=er_period, er_threshold=er_threshold
            )
        
        # 結構與階梯計算
        mss, bos = detect_market_structure(temp_df, period=10)
        temp_df['mss_signal'] = mss
        temp_df['bos_signal'] = bos
        temp_df['ladder'] = calculate_ladder_levels(temp_df, temp_df['atr'], k=k)
        
        # 吊燈止損線
        ch_long, ch_short = calculate_chandelier_exit(temp_df, temp_df['atr'], period=ch_period, multiplier=ch_multiplier)
        temp_df['chandelier_long'] = ch_long
        temp_df['chandelier_short'] = ch_short
        
        # 日內開盤價：以當日第一筆交易之開盤價為基準
        temp_df['date'] = temp_df.index.date
        temp_df['daily_open'] = temp_df.groupby('date')['open'].transform('first')
        
        # 三關價 (以日為單位，昨日最高/最低計算，當日使用)
        # 此處使用分群求得每日的昨日最高與昨日最低
        daily_ohlcv = temp_df.groupby('date').agg({'high': 'max', 'low': 'min'})
        yesterday_ohlcv = daily_ohlcv.shift(1) # 昨日日線數據
        
        # 將昨日數據對接回分鐘線/日線 DataFrame 中
        temp_df['yesterday_high'] = temp_df['date'].map(yesterday_ohlcv['high']).fillna(temp_df['high'].iloc[0])
        temp_df['yesterday_low'] = temp_df['date'].map(yesterday_ohlcv['low']).fillna(temp_df['low'].iloc[0])
        
        # 計算三關價
        temp_df['mid_price'] = (temp_df['yesterday_high'] + temp_df['yesterday_low']) / 2.0
        temp_df['diff'] = temp_df['yesterday_high'] - temp_df['yesterday_low']
        temp_df['upper_price'] = temp_df['yesterday_low'] + temp_df['diff'] * 1.382
        temp_df['lower_price'] = temp_df['yesterday_high'] - temp_df['diff'] * 1.382
        
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
            
            # 防禦看前偏誤：進場決策採用前一根 K 線之訊號
            prev_row = temp_df.iloc[i - 1]
            
            # 若目前無持倉，檢查進場訊號
            if not pm.is_active and position_shares == 0.0:
                # 全域濾網：三關價（價格在中關價之上做多）+ 市況濾網 (ADX/長均線/ER)
                global_ok = (row['close'] > row['mid_price']) and bool(row['regime_ok'])
                
                # 結構訊號合併 (BOS 或 MSS 均可做為結構確認)
                struct_sig = 0
                if prev_row['mss_signal'] == 1 or prev_row['bos_signal'] == 1:
                    struct_sig = 1
                elif prev_row['mss_signal'] == -1 or prev_row['bos_signal'] == -1:
                    struct_sig = -1
                
                is_entry = pm.check_entry_signal(
                    close=row['close'],
                    open_val=row['open'],
                    daily_open=row['daily_open'],
                    vwap=row['vwap'],
                    atr=row['atr'],
                    candle_high=row['high'],
                    candle_low=row['low'],
                    structure_sig=struct_sig,
                    global_filter_ok=global_ok,
                    is_daily=is_daily,
                    disabled_filters=disabled_filters
                )

                if is_entry:
                    # 計算買入價格 (含滑點成本)
                    raw_price = row['close']
                    execution_price = raw_price * (1 + self.slippage_rate)

                    # 整股單位買入：股數向下取整至 lot_size 倍數（台股一張 1000 股）
                    max_affordable = capital / (execution_price * (1.0 + self.commission_rate))
                    position_shares = self.round_to_lot(max_affordable)

                    if position_shares <= 0.0:
                        # 資金不足以買進一張，放棄此次訊號
                        current_equity = capital
                        equity_curve.append({
                            "datetime": current_time,
                            "capital": capital,
                            "position_value": 0.0,
                            "equity": current_equity
                        })
                        continue

                    cost = position_shares * execution_price
                    fee = cost * self.commission_rate
                    capital -= (cost + fee)

                    # 設定部位管理器參數
                    pm.is_active = True
                    pm.entry_price = execution_price
                    pm.position_size = 1.0 # 初始持倉比例為 100%
                    pm.stop_loss = execution_price - 2.0 * row['atr']
                    pm.stage = 1
                    pm.direction = 1
                    entry_bar_idx = i
                    
                    trade_logs.append({
                        "datetime": current_time,
                        "action": "BUY",
                        "shares": position_shares,
                        "price": execution_price,
                        "commission": fee,
                        "tax": 0.0,
                        "cash": capital,
                        "event": "滿足多重確認進場做多"
                    })
            
            # 若目前有持倉，動態更新與管理部位
            elif pm.is_active and position_shares > 0.0:
                bar_count = i - entry_bar_idx
                # 使用前一根 K 線的吊燈止損，防看前偏誤
                prev_ch_long = prev_row['chandelier_long']
                
                # 計算當下部位價值
                position_value = position_shares * row['close']
                
                # 執行部位管理
                pnl_ratio, event = pm.manage_position(
                    current_close=row['close'],
                    current_atr=row['atr'],
                    chandelier_long=prev_ch_long,
                    bar_count=bar_count,
                    time_limit=time_limit
                )
                
                # 處理減半平倉 (階段 1 止盈)
                if event == "階段 1 止盈 50% 成功，止損移至保本位":
                    execution_price = row['close'] * (1 - self.slippage_rate)
                    # 賣出股數同樣受整股單位約束；若僅持有一張無法分割，
                    # 則跳過實際賣出，但 PositionManager 已將止損移至保本位，
                    # 經濟意義等同「部位太小不拆分、直接轉為零風險持倉」。
                    shares_to_sell = self.round_to_lot(position_shares * 0.5)

                    if shares_to_sell > 0.0:
                        revenue = shares_to_sell * execution_price

                        # 扣除手續費與證券交易稅
                        commission = revenue * self.commission_rate
                        tax = revenue * self.tax_rate

                        capital += revenue - (commission + tax)
                        position_shares -= shares_to_sell

                        trade_logs.append({
                            "datetime": current_time,
                            "action": "SELL_HALF",
                            "shares": shares_to_sell,
                            "price": execution_price,
                            "commission": commission,
                            "tax": tax,
                            "cash": capital,
                            "event": event
                        })
                
                # 處理全數平倉 (止損、時間止盈或剩餘部位吊燈止損)
                elif event in ["觸發止損離場", "達到時間限制強制平倉", "剩餘部位觸發吊燈止損，波段結束"]:
                    execution_price = row['close'] * (1 - self.slippage_rate)
                    shares_sold = position_shares
                    revenue = shares_sold * execution_price

                    commission = revenue * self.commission_rate
                    tax = revenue * self.tax_rate

                    capital += revenue - (commission + tax)
                    position_shares = 0.0

                    trade_logs.append({
                        "datetime": current_time,
                        "action": "SELL_ALL",
                        "shares": shares_sold,
                        "price": execution_price,
                        "commission": commission,
                        "tax": tax,
                        "cash": capital,
                        "event": event
                    })
            
            # 更新淨值曲線
            current_equity = capital + (position_shares * row['close'])
            equity_curve.append({
                "datetime": current_time,
                "capital": capital,
                "position_value": position_shares * row['close'],
                "equity": current_equity
            })
            
        # 3. 整理回測結果與統計指標
        df_equity = pd.DataFrame(equity_curve).set_index("datetime")
        df_trades = pd.DataFrame(trade_logs)
        
        summary = self._calculate_metrics(df_equity, df_trades)
        
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
                    initial_cost = buy_row['shares'] * buy_row['price'] + buy_row['commission']

                    total_revenue = 0.0
                    total_friction = sell_row['commission'] + sell_row['tax']

                    if not half_sells.empty:
                        for _, half_row in half_sells.iterrows():
                            total_revenue += half_row['shares'] * half_row['price']
                            total_friction += half_row['commission'] + half_row['tax']

                    # 使用 SELL_ALL 實際記錄之賣出股數（整股取整後不必然等於買入股數之半）
                    total_revenue += sell_row['shares'] * sell_row['price']

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
