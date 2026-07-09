# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 多標的投資組合回測引擎 (Portfolio Backtest Engine)

本模組實現了：
1. 時間軸對齊 (Timeline Alignment)：對齊多檔標的之交易時間戳，解決除權息與暫停交易之資料缺失。
2. 多部位管理器對照表：每個標的配有獨立的 PositionManager 與參數規格。
3. 等權重資金分配演算法 (Equal-Weight Capital Allocation)：
   - 限制任一標的的最大持倉市值不得超過「帳戶總資產 / 標的總數 N」。
   - 當多個標的同時觸發進場時，均分剩餘現金。
   - 動態處理分批止盈 (SELL_HALF) 與全額平倉的現金回收。
4. 綜合投資組合淨值 (Portfolio Equity) 與量化指標計算。
"""

import os
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Tuple

from config import load_config
from db_security import safe_load_db_data
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

class PortfolioBacktester:
    """
    多資產投資組合回測引擎
    """
    def __init__(self, config_path: str = None):
        self.cfg = load_config(config_path)
        self.initial_capital = self.cfg.backtest.init_capital
        self.commission_rate = self.cfg.trading_cost.commission_rate
        self.tax_rate = self.cfg.trading_cost.tax_rate
        self.slippage_rate = self.cfg.trading_cost.slip_rate
        self.lot_size = self.cfg.trading_cost.lot_size
        self.tickers = self.cfg.data.tickers
        # 資金配置法：equal（等權重）或 inverse_vol（波動率倒數加權）
        self.allocation = self.cfg.portfolio.allocation
        self.vol_lookback = self.cfg.portfolio.vol_lookback
        self.max_weight = self.cfg.portfolio.max_weight

    def round_to_lot(self, shares: float) -> float:
        """
        將股數向下取整至整股單位之倍數（台股一張 1000 股）。
        """
        if self.lot_size <= 1:
            return float(shares)
        return float(int(shares // self.lot_size) * self.lot_size)

    def _load_and_calculate_indicators(self) -> Dict[str, pd.DataFrame]:
        """
        載入所有標的數據，並使用各標的之最佳化參數計算指標 (具備 SQL 注入安全防範)
        """
        db_path = self.cfg.data.database_path
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"找不到資料庫: {db_path}")
            
        ticker_dfs = {}
        
        for ticker in self.tickers:
            clean_ticker = ticker.replace(".", "_")
            table_name = f"stock_{clean_ticker}_daily"
            
            df = safe_load_db_data(db_path, table_name)
            
            if df.empty:
                continue
            
            # 讀取該標的之專屬參數規格
            params = self.cfg.strategy.get_params_for_ticker(ticker)
            
            # 計算指標
            temp_df = df.copy()
            tr = calculate_tr(temp_df['high'], temp_df['low'], temp_df['close'])
            temp_df['atr'] = calculate_atr(tr, period=params.atr_period)
            temp_df['vwap'] = calculate_vwap(temp_df)
            
            # 結構與階梯
            mss, bos = detect_market_structure(temp_df, period=10)
            temp_df['mss_signal'] = mss
            temp_df['bos_signal'] = bos
            temp_df['ladder'] = calculate_ladder_levels(temp_df, temp_df['atr'], k=params.ladder_k)
            
            # 吊燈止損
            ch_long, ch_short = calculate_chandelier_exit(
                temp_df, temp_df['atr'], 
                period=params.chandelier_period, 
                multiplier=params.chandelier_mult
            )
            temp_df['chandelier_long'] = ch_long
            temp_df['chandelier_short'] = ch_short
            
            # 日線的 daily_open 等於 open 
            temp_df['daily_open'] = temp_df['open']
            
            # 三關價 (昨日最高/最低計算)
            temp_df['date'] = temp_df.index.date
            daily_ohlcv = temp_df.groupby('date').agg({'high': 'max', 'low': 'min'})
            yesterday_ohlcv = daily_ohlcv.shift(1)
            
            temp_df['yesterday_high'] = temp_df['date'].map(yesterday_ohlcv['high']).fillna(temp_df['high'].iloc[0])
            temp_df['yesterday_low'] = temp_df['date'].map(yesterday_ohlcv['low']).fillna(temp_df['low'].iloc[0])
            
            temp_df['mid_price'] = (temp_df['yesterday_high'] + temp_df['yesterday_low']) / 2.0
            temp_df['diff'] = temp_df['yesterday_high'] - temp_df['yesterday_low']
            temp_df['upper_price'] = temp_df['yesterday_low'] + temp_df['diff'] * 1.382
            temp_df['lower_price'] = temp_df['yesterday_high'] - temp_df['diff'] * 1.382
            
            # 市況濾網 (ADX 趨勢強度 + 長均線方向 + ER 噪音)
            temp_df['regime_ok'] = calculate_regime_filter(
                temp_df,
                use_adx=params.use_adx_filter, adx_period=params.adx_period,
                adx_threshold=params.adx_threshold,
                use_ma=params.use_ma_filter, ma_period=params.ma_period,
                use_er=params.use_er_filter, er_period=params.er_period,
                er_threshold=params.er_threshold
            )

            # 滾動已實現波動率（年化前的日波動），移位一根防看前偏誤。
            # 供波動率倒數加權使用：讓每個標的貢獻接近的風險，而非接近的市值。
            temp_df['realized_vol'] = (
                temp_df['close'].pct_change()
                .rolling(window=self.vol_lookback, min_periods=max(5, self.vol_lookback // 3))
                .std(ddof=1)
                .shift(1)
            )

            # 儲存策略參數供回測迴圈讀取
            temp_df['param_time_limit'] = params.time_limit

            ticker_dfs[ticker] = temp_df

        return ticker_dfs

    def _compute_weights(self, aligned_dfs: Dict[str, pd.DataFrame], i: int) -> Dict[str, float]:
        """
        計算第 i 根 K 線當下各標的之資金權重上限。
        - equal：等權重 1/N。
        - inverse_vol：波動率倒數加權（風險均衡）。波動率越高的標的（如槓桿 ETF
          00631L）分得越少資金，避免組合風險被單一高波動標的主導。
          權重受 max_weight 上限約束後重新正規化；波動率資料不足的標的退回等權重。
        """
        n = len(self.tickers)
        equal_w = {t: 1.0 / n for t in self.tickers}

        if self.allocation != "inverse_vol":
            return equal_w

        inv_vols = {}
        for t in self.tickers:
            vol = aligned_dfs[t]['realized_vol'].iloc[i]
            if pd.notna(vol) and vol > 0.0:
                inv_vols[t] = 1.0 / vol

        # 若任一標的缺波動率數據，整體退回等權重（避免權重失真）
        if len(inv_vols) < n:
            return equal_w

        total = sum(inv_vols.values())
        weights = {t: v / total for t, v in inv_vols.items()}

        # 套用單一標的權重上限後重新正規化（迭代一次已足夠收斂於本用途）
        capped = {t: min(w, self.max_weight) for t, w in weights.items()}
        cap_total = sum(capped.values())
        if cap_total > 0:
            # 正規化後再夾一次上限，確保不違反 max_weight
            weights = {t: min(w / cap_total, self.max_weight) for t, w in capped.items()}

        return weights

    def run_portfolio_backtest(self) -> Dict[str, Any]:
        """
        執行多標的投資組合對齊回測
        """
        print("開始執行多標的投資組合聯合理財回測...")
        ticker_dfs = self._load_and_calculate_indicators()
        
        if not ticker_dfs:
            raise ValueError("所有標的數據皆為空，無法執行回測。")
            
        # 1. 建立對齊之全域時間軸
        all_indices = [df.index for df in ticker_dfs.values()]
        global_idx = all_indices[0]
        for idx in all_indices[1:]:
            global_idx = global_idx.union(idx)
            
        global_idx = global_idx.sort_values()
        
        # 對齊各標的 DataFrame，並以前值補齊缺失 (例如停牌)
        aligned_dfs = {}
        for ticker, df in ticker_dfs.items():
            aligned_dfs[ticker] = df.reindex(global_idx).ffill().bfill()
            
        # 2. 初始化帳戶狀態
        cash = self.initial_capital
        shares: Dict[str, float] = {t: 0.0 for t in self.tickers}
        pms: Dict[str, PositionManager] = {t: PositionManager() for t in self.tickers}
        entry_bars: Dict[str, int] = {t: 0 for t in self.tickers}
        
        portfolio_equity: List[Dict[str, Any]] = []
        trade_logs: List[Dict[str, Any]] = []
        
        # 3. 逐筆交易模擬迴圈
        for i in range(1, len(global_idx)):
            current_time = global_idx[i]
            
            # 計算當前總資產價值 (Total Equity = Cash + Sum of Stocks Value)
            current_stock_value = 0.0
            for t in self.tickers:
                if shares[t] > 0.0:
                    current_stock_value += shares[t] * aligned_dfs[t]['close'].iloc[i]
            total_equity = cash + current_stock_value

            # 各標的資金配額上限：等權重或波動率倒數加權（風險均衡）
            weights = self._compute_weights(aligned_dfs, i)
            max_capital_by_ticker = {t: total_equity * weights[t] for t in self.tickers}
            
            # --- 步驟 A：持倉部位管理與平倉處理 ---
            for t in self.tickers:
                pm = pms[t]
                if pm.is_active and shares[t] > 0.0:
                    df_t = aligned_dfs[t]
                    row = df_t.iloc[i]
                    prev_row = df_t.iloc[i - 1]
                    
                    bar_count = i - entry_bars[t]
                    prev_ch_long = prev_row['chandelier_long']
                    time_limit = int(row['param_time_limit'])
                    
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
                        # 整股單位約束：僅持有一張無法分割時，跳過實際賣出，
                        # 止損已移至保本位，等同零風險持倉。
                        shares_to_sell = self.round_to_lot(shares[t] * 0.5)

                        if shares_to_sell > 0.0:
                            revenue = shares_to_sell * execution_price

                            commission = revenue * self.commission_rate
                            tax = revenue * self.tax_rate

                            cash += revenue - (commission + tax)
                            shares[t] -= shares_to_sell

                            trade_logs.append({
                                "datetime": current_time,
                                "ticker": t,
                                "action": "SELL_HALF",
                                "shares": shares_to_sell,
                                "price": execution_price,
                                "commission": commission,
                                "tax": tax,
                                "cash": cash,
                                "event": event
                            })
                        
                    # 處理全數平倉 (止損、時間止盈或吊燈止損)
                    elif event in ["觸發止損離場", "達到時間限制強制平倉", "剩餘部位觸發吊燈止損，波段結束"]:
                        execution_price = row['close'] * (1 - self.slippage_rate)
                        shares_sold = shares[t]
                        revenue = shares_sold * execution_price

                        commission = revenue * self.commission_rate
                        tax = revenue * self.tax_rate

                        cash += revenue - (commission + tax)
                        shares[t] = 0.0

                        trade_logs.append({
                            "datetime": current_time,
                            "ticker": t,
                            "action": "SELL_ALL",
                            "shares": shares_sold,
                            "price": execution_price,
                            "commission": commission,
                            "tax": tax,
                            "cash": cash,
                            "event": event
                        })
            
            # --- 步驟 B：進場訊號偵測與資金配額買入 ---
            signals_to_buy = []
            for t in self.tickers:
                pm = pms[t]
                # 僅在未持倉時檢查進場
                if not pm.is_active and shares[t] == 0.0:
                    df_t = aligned_dfs[t]
                    row = df_t.iloc[i]
                    prev_row = df_t.iloc[i - 1]
                    
                    # 全域濾網：三關價 + 市況濾網 (ADX/長均線/ER)
                    global_ok = (row['close'] > row['mid_price']) and bool(row['regime_ok'])
                    
                    # 結構突破確認
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
                        is_daily=True # 組合回測目前為日線級別
                    )
                    
                    if is_entry:
                        signals_to_buy.append(t)
                        
            # 若有買入訊號，依配置權重分配資金（等權重或波動率倒數加權）
            if signals_to_buy:
                num_buys = len(signals_to_buy)
                # 多標的同時觸發時，現金依各標的權重比例分配
                signal_weight_total = sum(weights[t] for t in signals_to_buy)

                for t in signals_to_buy:
                    # 該標的可用配額：權重上限與現金比例分配兩者取小
                    cash_share = cash * (weights[t] / signal_weight_total) if signal_weight_total > 0 else cash / num_buys
                    allocated = min(max_capital_by_ticker[t], cash_share)

                    if allocated > 1000.0:
                        df_t = aligned_dfs[t]
                        row = df_t.iloc[i]

                        raw_price = row['close']
                        execution_price = raw_price * (1 + self.slippage_rate)

                        # 整股單位買入：股數向下取整至 lot_size 倍數
                        max_affordable = allocated / (execution_price * (1.0 + self.commission_rate))
                        shares_bought = self.round_to_lot(max_affordable)

                        if shares_bought <= 0.0:
                            # 配額不足以買進一張，放棄此訊號
                            continue

                        cost = shares_bought * execution_price
                        fee = cost * self.commission_rate
                        cash -= (cost + fee)
                        shares[t] = shares_bought

                        # 初始化部位管理器
                        pm = pms[t]
                        pm.is_active = True
                        pm.entry_price = execution_price
                        pm.position_size = 1.0
                        pm.stop_loss = execution_price - 2.0 * row['atr']
                        pm.stage = 1
                        pm.direction = 1
                        entry_bars[t] = i

                        trade_logs.append({
                            "datetime": current_time,
                            "ticker": t,
                            "action": "BUY",
                            "shares": shares_bought,
                            "price": execution_price,
                            "commission": fee,
                            "tax": 0.0,
                            "cash": cash,
                            "event": f"投資組合多重確認進場做多 (配置法: {self.allocation})"
                        })
            
            # --- 步驟 C：更新組合淨值記錄 ---
            stock_val = 0.0
            for t in self.tickers:
                if shares[t] > 0.0:
                    stock_val += shares[t] * aligned_dfs[t]['close'].iloc[i]
            
            portfolio_equity.append({
                "datetime": current_time,
                "cash": cash,
                "position_value": stock_val,
                "equity": cash + stock_val
            })
            
        # 4. 統計指標與回測結果整理
        df_equity = pd.DataFrame(portfolio_equity).set_index("datetime")
        df_trades = pd.DataFrame(trade_logs)
        
        summary = self._calculate_metrics(df_equity, df_trades)
        
        return {
            "summary": summary,
            "equity_curve": df_equity,
            "trades": df_trades
        }

    def _calculate_metrics(self, df_equity: pd.DataFrame, df_trades: pd.DataFrame) -> Dict[str, Any]:
        """
        計算投資組合之量化交易指標
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
            # 依 ticker 分群配對交易
            paired_trades = []
            
            for ticker in self.tickers:
                df_ticker_trades = df_trades[df_trades['ticker'] == ticker]
                if df_ticker_trades.empty:
                    continue
                    
                buy_trades = df_ticker_trades[df_ticker_trades['action'] == 'BUY']
                sell_all_trades = df_ticker_trades[df_ticker_trades['action'] == 'SELL_ALL']
                
                for idx, buy_row in buy_trades.iterrows():
                    buy_time = buy_row['datetime']
                    later_sells = sell_all_trades[sell_all_trades['datetime'] > buy_time]
                    if not later_sells.empty:
                        sell_row = later_sells.iloc[0]
                        sell_time = sell_row['datetime']
                        
                        half_sells = df_ticker_trades[(df_ticker_trades['action'] == 'SELL_HALF') & 
                                               (df_ticker_trades['datetime'] > buy_time) & 
                                               (df_ticker_trades['datetime'] < sell_time)]
                                               
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
                        paired_trades.append((profit, profit / initial_cost if initial_cost > 0 else 0.0))
                        
            total_trades = len(paired_trades)
            if total_trades > 0:
                trade_returns = [r for _, r in paired_trades]
                profits = [p for p, _ in paired_trades if p > 0]
                losses = [p for p, _ in paired_trades if p <= 0]

                wins = len(profits)
                win_rate = wins / total_trades

                sum_profits = sum(profits)
                sum_losses = abs(sum(losses))

                profit_factor = sum_profits / sum_losses if sum_losses > 0 else (np.inf if sum_profits > 0 else 1.0)

        summary = {
            "initial_capital": self.initial_capital,
            "final_equity": final_equity,
            "total_return": total_return,
            "max_drawdown": mdd,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "trade_returns": trade_returns,
        }
        summary.update({k: v for k, v in perf.items() if k not in summary})
        return summary
