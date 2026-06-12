"""
Range Navigator - 視覺化互動儀表板 (Web UI Dashboard)

本程式使用 Streamlit 與 Plotly 實現：
1. 即時策略參數調整 (ATR, 階梯 k 乘數, 吊燈止損, 時間限制)。
2. 多標的選擇切換 (2330.TW, 0050.TW) 與資料庫整合讀取。
3. 互動式 K 線圖表：疊加多空階梯線、三關價濾網、以及實時回測進出場點記號。
4. 淨值增長曲線與回撤水下圖。
5. 玻璃擬物化 (Glassmorphism) 卡片呈現量化績效 KPI。
6. 精美的交易日誌與操作提示。
"""

import os
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from backtester import BacktestEngine
from data_ingestion import fetch_stock_data
from config import load_config
from db_security import safe_load_db_data, safe_save_to_sqlite
from security_utils import is_locked, register_failed_attempt, reset_lockout

# 載入強型別規格配置
cfg = load_config()

# =========================================================================
# 1.1. 密碼驗證防護閘 (Security Password Gate)
# =========================================================================
def check_password() -> bool:
    """
    簡易密碼驗證防護閘。若 st.secrets 中未設定 password，則預設跳過，便於本地開發。
    加入登入鎖定機制，防止暴力破解。連續失敗超過上限將暫時鎖定。
    """
    # 若已被鎖定，提示使用者稍後再試
    if is_locked(st.session_state):
        st.warning("已達嘗試上限，請稍後再試。")
        return False

    try:
        if "password" not in st.secrets:
            return True
    except Exception:
        # 當 Streamlit secrets 未初始化（本地開發常用）時，放行
        return True

    def password_entered():
        if st.session_state.get("password") == st.secrets["password"]:
            st.session_state["password_correct"] = True
            # 成功登入，重置鎖定狀態
            reset_lockout(st.session_state)
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False
            # 登入失敗，註冊失敗嘗試並可能觸發鎖定
            register_failed_attempt(st.session_state)

    if "password_correct" not in st.session_state:
        st.text_input(
            "請輸入密碼以存取儀表板",
            type="password",
            on_change=password_entered,
            key="password"
        )
        return False
    elif not st.session_state["password_correct"]:
        st.text_input(
            "請輸入密碼以存取儀表板",
            type="password",
            on_change=password_entered,
            key="password"
        )
        st.error("密碼錯誤，請重新輸入。")
        return False
    else:
        return True

if not check_password():
    st.stop()

# =========================================================================
# 1.2. 網頁頁面配置與 CSS 注入 (Aesthetics & Layout)
# =========================================================================
st.set_page_config(
    page_title="Range Navigator - 階梯策略互動儀表板",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 注入 CSS：引入 Outfit 字型，設定玻璃擬物化 (Glassmorphism) 卡片樣式與現代深色背景
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap');
    
    /* 全域字體與背景 */
    * {
        font-family: 'Outfit', 'Segoe UI', -apple-system, sans-serif;
    }
    
    /* 玻璃擬物化卡片樣式 */
    .kpi-container {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 20px;
        margin-bottom: 25px;
    }
    
    .kpi-card {
        background: rgba(25, 30, 40, 0.65);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 16px;
        padding: 20px;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        transition: transform 0.2s ease, border-color 0.2s ease;
    }
    
    .kpi-card:hover {
        transform: translateY(-2px);
        border-color: rgba(0, 210, 255, 0.3);
    }
    
    .kpi-title {
        font-size: 14px;
        color: #8b9bb4;
        margin-bottom: 8px;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    
    .kpi-value {
        font-size: 28px;
        font-weight: 700;
        color: #ffffff;
    }
    
    .kpi-value.positive {
        color: #00ffaa;
        text-shadow: 0 0 10px rgba(0, 255, 170, 0.2);
    }
    
    .kpi-value.negative {
        color: #ff4a5a;
        text-shadow: 0 0 10px rgba(255, 74, 90, 0.2);
    }
    
    /* 表格與側邊欄美化 */
    .stTable {
        background: rgba(25, 30, 40, 0.4);
        border-radius: 12px;
        overflow: hidden;
    }
</style>
""", unsafe_allow_html=True)

# =========================================================================
# 2. 數據載入輔助函式 (Data Loading Utilities)
# =========================================================================
# 已重構為引用 db_security 模組之 safe_load_db_data 以防杜 SQL 注入。

# =========================================================================
# 3. 網頁標題與側邊欄控制面板 (UI Sidebar Panel)
# =========================================================================
st.title("Range Navigator - 多空階梯策略互動儀表板")
st.markdown("基於市場結構偏見移位 (MSS)、趨勢連續 (BOS) 與台指期三關價過濾的量化策略實時展示。")

# 側邊欄配置
st.sidebar.header("策略與回測參數")

# Ticker 選擇 (對接配置檔 tickers 清單，並新增 PORTFOLIO 選項)
ticker_options = ["PORTFOLIO"] + cfg.data.tickers
ticker_option = st.sidebar.selectbox(
    "1. 選擇交易標的 (Ticker)",
    ticker_options,
    index=1  # 預設選中第一個個股 (2330.TW)
)

is_portfolio = (ticker_option == "PORTFOLIO")

# 投資設定 (對接配置檔 init_capital)
st.sidebar.subheader("投資設定")
init_capital = st.sidebar.number_input(
    "初始投資資金 (元)",
    min_value=10000.0,
    max_value=100000000.0,
    value=cfg.backtest.init_capital,
    step=50000.0
)

# 依選定標的動態載入最佳化預設參數
if not is_portfolio:
    p_opt = cfg.strategy.get_params_for_ticker(ticker_option)
    
    st.sidebar.subheader("輔助技術指標")
    show_ema = st.sidebar.checkbox("顯示 EMA (20)", value=False)
    show_ema_200 = st.sidebar.checkbox("顯示 EMA (200)", value=False)
    show_kama = st.sidebar.checkbox("顯示 KAMA (10)", value=False)
    show_bb = st.sidebar.checkbox("顯示布林通道 (Bollinger Bands)", value=False)

    st.sidebar.markdown("---")

    # 指標與止損參數調整
    st.sidebar.subheader("指標與進場參數")
    atr_period = st.sidebar.slider("ATR 計算週期", min_value=5, max_value=40, value=int(p_opt.atr_period), step=1)
    ladder_k = st.sidebar.slider("階梯觸發乘數 (k)", min_value=0.5, max_value=5.0, value=float(p_opt.ladder_k), step=0.1)

    st.sidebar.subheader("動態部位與止損參數")
    ch_period = st.sidebar.slider("吊燈滾動週期", min_value=10, max_value=50, value=int(p_opt.chandelier_period), step=1)
    ch_mult = st.sidebar.slider("吊燈止損乘數", min_value=1.5, max_value=6.0, value=float(p_opt.chandelier_mult), step=0.1)
    time_limit = st.sidebar.slider("持有時間限制 (根數)", min_value=5, max_value=50, value=int(p_opt.time_limit), step=1)

    st.sidebar.markdown("---")

    # 交易摩擦成本設定
    st.sidebar.subheader("交易手續費與滑點")
    comm_rate = st.sidebar.number_input("手續費率 (單邊 %)", min_value=0.0, max_value=1.0, value=cfg.trading_cost.commission_rate * 100.0, step=0.01) / 100.0
    tax_rate = st.sidebar.number_input("證交稅率 (%)", min_value=0.0, max_value=1.0, value=cfg.trading_cost.tax_rate * 100.0, step=0.05) / 100.0
    slip_rate = st.sidebar.number_input("滑點率 (單邊 %)", min_value=0.0, max_value=1.0, value=cfg.trading_cost.slip_rate * 100.0, step=0.01) / 100.0
else:
    st.sidebar.info("投資組合模式下，系統會自動使用每檔股票在 config.yaml 中的最佳化參數進行聯合同步回測，無法在此手動微調參數。")
    # 給予預設值以防後面變數未定義錯誤
    show_ema = False
    show_ema_200 = False
    show_kama = False
    show_bb = False
    atr_period = int(cfg.strategy.default.atr_period)
    ladder_k = float(cfg.strategy.default.ladder_k)
    ch_period = int(cfg.strategy.default.chandelier_period)
    ch_mult = float(cfg.strategy.default.chandelier_mult)
    time_limit = int(cfg.strategy.default.time_limit)
    comm_rate = cfg.trading_cost.commission_rate
    tax_rate = cfg.trading_cost.tax_rate
    slip_rate = cfg.trading_cost.slip_rate

# =========================================================================
# 3.1. 觀察標的動態管理面板 (Tickers Management Panel)
# =========================================================================
st.sidebar.markdown("---")
with st.sidebar.expander("管理觀察標的"):
    # 1. 新增標的
    st.markdown("**新增觀察標的**")
    new_ticker = st.text_input("輸入股票代號 (如 2454.TW)", key="new_ticker_input").strip()
    if st.button("確認新增"):
        if not new_ticker:
            st.error("請輸入有效的股票代號。")
        elif new_ticker in cfg.data.tickers:
            st.error("該標的已在觀察清單中。")
        else:
            import re
            # 正則驗證 Ticker 格式，僅允許英數字與點和減號
            if not re.match(r"^[a-zA-Z0-9.-]+$", new_ticker):
                st.error("股票代號格式錯誤，僅允許英文、數字、點與減號。")
            else:
                db_path_tmp = cfg.data.database_path
                with st.spinner(f"正在連線下載 {new_ticker} 最新數據……"):
                    # 嘗試抓取 1y 的日線數據作為驗證
                    df_verify = fetch_stock_data(ticker=new_ticker, period="1y", interval="1d")
                    # 同時下載 5 分鐘數據，確保監控警示正常運作
                    df_verify_5m = fetch_stock_data(ticker=new_ticker, period="5d", interval="5m")
                    
                if df_verify is not None and not df_verify.empty and df_verify_5m is not None and not df_verify_5m.empty:
                    # 寫入 SQLite 本地資料庫
                    clean_new = new_ticker.replace(".", "_")
                    table_daily = f"stock_{clean_new}_daily"
                    table_5m = f"stock_{clean_new}_5m"
                    
                    # 具備 SQL 注入校驗的安全寫入
                    daily_ok = safe_save_to_sqlite(df_verify, table_daily, db_path_tmp)
                    m5_ok = safe_save_to_sqlite(df_verify_5m, table_5m, db_path_tmp)
                    
                    if daily_ok and m5_ok:
                        # 更新設定檔
                        from config.config import save_config, SingleStrategyParams
                        cfg.data.tickers.append(new_ticker)
                        # 為新標的建立預設參數覆蓋
                        cfg.strategy.ticker_overrides[new_ticker] = SingleStrategyParams()
                        
                        if save_config(cfg):
                            st.success(f"成功新增標的 {new_ticker} ！")
                            st.rerun()
                        else:
                            st.error("寫入設定檔失敗。")
                    else:
                        st.error("寫入資料庫失敗。")
                else:
                    st.error(f"無法自 Yahoo Finance 下載 {new_ticker} 的完整數據，請檢查代號是否正確。")

    st.markdown("---")
    
    # 2. 刪除標的
    st.markdown("**刪除觀察標的**")
    ticker_to_delete = st.selectbox(
        "選擇要刪除的標的",
        options=cfg.data.tickers,
        key="delete_ticker_select"
    )
    
    # 防呆限制：至少保留一個標的
    can_delete = len(cfg.data.tickers) > 1
    
    if st.button("確認刪除", disabled=not can_delete):
        db_path_tmp = cfg.data.database_path
        with st.spinner(f"正在移除 {ticker_to_delete} 並清理資料庫……"):
            clean_del = ticker_to_delete.replace(".", "_")
            table_daily_del = f"stock_{clean_del}_daily"
            table_5m_del = f"stock_{clean_del}_5m"
            
            # 安全地 DROP TABLE，防止 SQL 注入
            from db_security import validate_table_name
            import sqlite3
            
            # 實施 Fail-Fast 正則驗證
            validate_table_name(table_daily_del)
            validate_table_name(table_5m_del)
            
            # 連線並刪除對應的資料表
            conn = sqlite3.connect(db_path_tmp)
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table_daily_del}")
                conn.execute(f"DROP TABLE IF EXISTS {table_5m_del}")
                conn.commit()
                db_cleaned = True
            except Exception as e:
                st.error(f"清理資料表失敗： {e} ")
                db_cleaned = False
            finally:
                conn.close()
                
            if db_cleaned:
                # 從設定檔移除
                from config.config import save_config
                if ticker_to_delete in cfg.data.tickers:
                    cfg.data.tickers.remove(ticker_to_delete)
                if ticker_to_delete in cfg.strategy.ticker_overrides:
                    del cfg.strategy.ticker_overrides[ticker_to_delete]
                    
                if save_config(cfg):
                    st.success(f"成功移除標的 {ticker_to_delete} ！")
                    st.rerun()
                else:
                    st.error("寫入設定檔失敗。")
    if not can_delete:
        st.caption("提示：系統必須保留至少 1 個觀察標的，故目前無法執行刪除。")

# =========================================================================
# 4. 數據載入與回測執行 (Core Backtest Run)
# =========================================================================
db_path = cfg.data.database_path

if is_portfolio:
    with st.spinner("正在執行多標的投資組合聯合回測……"):
        from portfolio_backtester import PortfolioBacktester
        pb = PortfolioBacktester()
        results = pb.run_portfolio_backtest()
        
        summary = results["summary"]
        df_equity = results["equity_curve"]
        df_trades = results["trades"]
else:
    clean_ticker = ticker_option.replace(".", "_")
    table_name = f"stock_{clean_ticker}_daily"
    
    # 優先嘗試自 Yahoo Finance 下載最新數據，失敗時自動回退至本地 SQLite 資料庫 (防呆與高可用性)
    with st.spinner(f"正在更新 {ticker_option} 最新即時數據……"):
        df_kline = fetch_stock_data(ticker=ticker_option, period="1y", interval="1d")
    
    if df_kline is not None and not df_kline.empty:
        # 成功獲取，寫入本地資料庫快取
        success = safe_save_to_sqlite(df_kline, table_name, db_path)
        if not success:
            st.sidebar.warning("本地快取寫入失敗。")
        st.toast(f"成功自 Yahoo Finance 獲取 {ticker_option} 最新數據！")
    else:
        # 獲取失敗，讀取本地舊資料
        df_kline = safe_load_db_data(db_path, table_name)
        if not df_kline.empty:
            st.sidebar.warning("無法連線至 Yahoo Finance，已載入本地歷史快取數據。")
        else:
            st.error("獲取數據失敗：網路不可用且本地資料庫中無快取數據！請檢查網路連線。")
            st.stop()
            
    # 實例化單標的回測引擎並執行
    engine = BacktestEngine(
        initial_capital=init_capital,
        commission_rate=comm_rate,
        tax_rate=tax_rate,
        slippage_rate=slip_rate
    )
    
    # 執行回測
    results = engine.run_backtest(
        df=df_kline,
        atr_period=atr_period,
        k=ladder_k,
        ch_period=ch_period,
        ch_multiplier=ch_mult,
        time_limit=time_limit
    )
    
    summary = results["summary"]
    df_equity = results["equity_curve"]
    df_trades = results["trades"]
# 5. KPI 玻璃卡片呈現 (Glassmorphism KPI Cards)
return_val = summary["total_return"] * 100
mdd_val = summary["max_drawdown"] * 100
win_rate_val = summary["win_rate"] * 100
pf_val = summary["profit_factor"]

return_class = "positive" if return_val >= 0 else "negative"
mdd_class = "negative" # MDD 為負值

st.markdown(f"""
<div class="kpi-container">
    <div class="kpi-card">
        <div class="kpi-title">總投資報酬率</div>
        <div class="kpi-value {return_class}">{return_val:+.2f}%</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-title">最大資金回撤 (MDD)</div>
        <div class="kpi-value negative">{mdd_val:.2f}%</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-title">總交易次數 / 勝率</div>
        <div class="kpi-value">{summary['total_trades']} 次 / {win_rate_val:.1f}%</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-title">獲利因子 (Profit Factor)</div>
        <div class="kpi-value">{"無虧損 (inf)" if pf_val == np.inf or pf_val == 0 and return_val > 0 else f"{pf_val:.2f}"}</div>
    </div>
</div>
""", unsafe_allow_html=True)

# =========================================================================
# 6. 互動式技術圖表繪製 (Interactive Charts)
# =========================================================================
if is_portfolio:
    st.subheader("投資組合標的收盤價相對走勢對照 (Normalized to 100%)")
    
    # 載入標的資料庫數據並進行基準歸一化 (以首日價格為 100%)
    try:
        normalized_dfs = []
        for t in cfg.data.tickers:
            clean_t = t.replace(".", "_")
            table_name_t = f"stock_{clean_t}_daily"
            df_t = safe_load_db_data(db_path, table_name_t)
            if not df_t.empty:
                # 歸一化：(當前收盤價 / 首日收盤價) * 100
                df_t[t] = (df_t['close'] / df_t['close'].iloc[0]) * 100.0
                normalized_dfs.append(df_t[[t]])
        
        df_normalized = pd.concat(normalized_dfs, axis=1).ffill().bfill()
        
        fig_norm = go.Figure()
        colors = ["#ff4a5a", "#00d2ff", "#ffeb3b", "#e040fb", "#00e676"]
        for idx, t in enumerate(df_normalized.columns):
            fig_norm.add_trace(go.Scatter(
                x=df_normalized.index,
                y=df_normalized[t],
                name=t,
                line=dict(color=colors[idx % len(colors)], width=2.0)
            ))
            
        fig_norm.update_layout(
            template="plotly_dark",
            height=400,
            margin=dict(l=10, r=10, t=30, b=10),
            xaxis=dict(gridcolor="rgba(255, 255, 255, 0.05)"),
            yaxis=dict(gridcolor="rgba(255, 255, 255, 0.05)", title="歸一化基準價 (%)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig_norm, use_container_width=True)
    except Exception as e:
        st.warning(f"無法載入標的對照圖： {e} ")

else:
    # --- 單一標的價格與技術指標圖表 ---
    st.subheader("價格走勢與多空階梯確認圖表")
    
    # 預先計算繪圖所需之技術指標數據 (保持原本邏輯)
    plot_df = df_kline.copy()
    import ladder_system
    
    plot_df['atr'] = ladder_system.calculate_atr(
        ladder_system.calculate_tr(df_kline['high'], df_kline['low'], df_kline['close']), 
        period=atr_period
    )
    plot_df['vwap'] = ladder_system.calculate_vwap(df_kline)
    
    from backtester import detect_market_structure, calculate_ladder_levels, calculate_chandelier_exit
    mss_plot, bos_plot = detect_market_structure(plot_df, period=10)
    plot_df['mss'] = mss_plot
    plot_df['bos'] = bos_plot
    plot_df['ladder'] = calculate_ladder_levels(plot_df, plot_df['atr'], k=ladder_k)
    ch_long, ch_short = calculate_chandelier_exit(plot_df, plot_df['atr'], period=ch_period, multiplier=ch_mult)
    plot_df['ch_long'] = ch_long
    
    # 輔助技術指標
    plot_df['ema'] = ladder_system.calculate_ema(plot_df['close'], span=20)
    plot_df['ema_200'] = ladder_system.calculate_ema(plot_df['close'], span=200)
    plot_df['kama'] = ladder_system.calculate_kama(plot_df['close'], period=10)
    bb_upper, bb_mid, bb_lower = ladder_system.calculate_bollinger_bands(plot_df['close'], period=20, num_std=2.0)
    plot_df['bb_upper'] = bb_upper
    plot_df['bb_mid'] = bb_mid
    plot_df['bb_lower'] = bb_lower
    
    # 三關價
    plot_df['date'] = plot_df.index.date
    daily_ohlcv = plot_df.groupby('date').agg({'high': 'max', 'low': 'min'})
    yesterday_ohlcv = daily_ohlcv.shift(1)
    plot_df['yesterday_high'] = plot_df['date'].map(yesterday_ohlcv['high']).fillna(plot_df['high'].iloc[0])
    plot_df['yesterday_low'] = plot_df['date'].map(yesterday_ohlcv['low']).fillna(plot_df['low'].iloc[0])
    plot_df['mid_price'] = (plot_df['yesterday_high'] + plot_df['yesterday_low']) / 2.0
    plot_df['diff'] = plot_df['yesterday_high'] - plot_df['yesterday_low']
    plot_df['upper_price'] = plot_df['yesterday_low'] + plot_df['diff'] * 1.382
    plot_df['lower_price'] = plot_df['yesterday_high'] - plot_df['diff'] * 1.382
    
    fig_price = go.Figure()
    
    # 1. 蠟燭 K 線圖
    fig_price.add_trace(go.Candlestick(
        x=plot_df.index,
        open=plot_df['open'],
        high=plot_df['high'],
        low=plot_df['low'],
        close=plot_df['close'],
        name="K 線",
        increasing_line_color="#ff4a5a", # 紅漲
        decreasing_line_color="#00e676", # 綠跌
        increasing_fillcolor="rgba(255, 74, 90, 0.2)",
        decreasing_fillcolor="rgba(0, 230, 118, 0.2)"
    ))
    
    # 2. 多空階梯軌跡線
    fig_price.add_trace(go.Scatter(
        x=plot_df.index,
        y=plot_df['ladder'],
        name="多空階梯價格線",
        line=dict(color="#00d2ff", width=2.5, shape="hv"),
        opacity=0.9
    ))
    
    # 3. 三關價線
    fig_price.add_trace(go.Scatter(
        x=plot_df.index,
        y=plot_df['upper_price'],
        name="上關價 (偏多目標)",
        line=dict(color="#ff9800", width=1.2, dash="dash"),
        opacity=0.6
    ))
    fig_price.add_trace(go.Scatter(
        x=plot_df.index,
        y=plot_df['mid_price'],
        name="中關價 (多空分水嶺)",
        line=dict(color="#ffffff", width=1.2, dash="dot"),
        opacity=0.5
    ))
    fig_price.add_trace(go.Scatter(
        x=plot_df.index,
        y=plot_df['lower_price'],
        name="下關價 (偏空目標)",
        line=dict(color="#2196f3", width=1.2, dash="dash"),
        opacity=0.6
    ))
    
    # 4. 吊燈止損線
    fig_price.add_trace(go.Scatter(
        x=plot_df.index,
        y=plot_df['ch_long'],
        name="吊燈式止損跟蹤線",
        line=dict(color="#e040fb", width=1.5, dash="dashdot"),
        opacity=0.7
    ))
    
    # 5. 輔助指標疊加
    if show_ema:
        fig_price.add_trace(go.Scatter(x=plot_df.index, y=plot_df['ema'], name="EMA (20)", line=dict(color="#ffa726", width=1.5), opacity=0.8))
    if show_ema_200:
        fig_price.add_trace(go.Scatter(x=plot_df.index, y=plot_df['ema_200'], name="EMA (200)", line=dict(color="#d32f2f", width=1.5), opacity=0.8))
    if show_kama:
        fig_price.add_trace(go.Scatter(x=plot_df.index, y=plot_df['kama'], name="KAMA (10)", line=dict(color="#26a69a", width=1.8), opacity=0.8))
    if show_bb:
        fig_price.add_trace(go.Scatter(x=plot_df.index, y=plot_df['bb_upper'], name="布林通道-上軌", line=dict(color="#4fc3f7", width=1.0, dash="dash"), opacity=0.6))
        fig_price.add_trace(go.Scatter(x=plot_df.index, y=plot_df['bb_mid'], name="布林通道-中軌", line=dict(color="#b0bec5", width=1.0, dash="dash"), opacity=0.5))
        fig_price.add_trace(go.Scatter(x=plot_df.index, y=plot_df['bb_lower'], name="布林通道-下軌", line=dict(color="#4fc3f7", width=1.0, dash="dash"), opacity=0.6))
        
    # 6. 進出場標記
    if not df_trades.empty:
        buy_trades = df_trades[df_trades['action'] == 'BUY']
        if not buy_trades.empty:
            fig_price.add_trace(go.Scatter(
                x=pd.to_datetime(buy_trades['datetime']), y=buy_trades['price'], mode="markers+text",
                marker=dict(symbol="triangle-up", size=14, color="#00ffaa", line=dict(width=1, color="white")),
                text=["B"] * len(buy_trades), textposition="bottom center", name="進場做多 (BUY)"
            ))
        half_trades = df_trades[df_trades['action'] == 'SELL_HALF']
        if not half_trades.empty:
            fig_price.add_trace(go.Scatter(
                x=pd.to_datetime(half_trades['datetime']), y=half_trades['price'], mode="markers+text",
                marker=dict(symbol="triangle-down", size=12, color="#ffeb3b", line=dict(width=1, color="white")),
                text=["S50%"] * len(half_trades), textposition="top center", name="減半止盈 (SELL 50%)"
            ))
        all_trades = df_trades[df_trades['action'] == 'SELL_ALL']
        if not all_trades.empty:
            fig_price.add_trace(go.Scatter(
                x=pd.to_datetime(all_trades['datetime']), y=all_trades['price'], mode="markers+text",
                marker=dict(symbol="x", size=12, color="#ff3d00", line=dict(width=1, color="white")),
                text=["Exit"] * len(all_trades), textposition="top center", name="全數平倉 (SELL 100%)"
            ))
            
    fig_price.update_layout(
        template="plotly_dark", height=600, margin=dict(l=10, r=10, t=30, b=10),
        xaxis=dict(rangeslider=dict(visible=False), gridcolor="rgba(255, 255, 255, 0.05)"),
        yaxis=dict(gridcolor="rgba(255, 255, 255, 0.05)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig_price, use_container_width=True)

# =========================================================================
# 7. 淨值與水下回撤圖 (Equity & Drawdown)
# =========================================================================
st.subheader("資金淨值與水下回撤曲線")

fig_metrics = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                            vertical_spacing=0.08, row_heights=[0.7, 0.3])

# 1. 淨值增長折線圖
fig_metrics.add_trace(
    go.Scatter(
        x=df_equity.index,
        y=df_equity['equity'],
        name="帳戶淨值 (Equity)",
        line=dict(color="#00d2ff", width=2.5),
        fill='tozeroy',
        fillcolor='rgba(0, 210, 255, 0.08)'
    ),
    row=1, col=1
)

# 2. 回撤水下圖 (Drawdown)
peaks = df_equity['equity'].cummax()
drawdowns = (df_equity['equity'] - peaks) / peaks * 100.0

fig_metrics.add_trace(
    go.Scatter(
        x=df_equity.index,
        y=drawdowns,
        name="回撤幅度 (%)",
        line=dict(color="#ff3d00", width=1.5),
        fill='tozeroy',
        fillcolor='rgba(255, 61, 0, 0.15)'
    ),
    row=2, col=1
)

fig_metrics.update_layout(
    template="plotly_dark",
    height=400,
    margin=dict(l=10, r=10, t=10, b=10),
    showlegend=False
)
fig_metrics.update_xaxes(gridcolor="rgba(255, 255, 255, 0.05)")
fig_metrics.update_yaxes(gridcolor="rgba(255, 255, 255, 0.05)")

st.plotly_chart(fig_metrics, use_container_width=True)

# =========================================================================
# 8. 交易日誌表格明細 (Trades Table Logs)
# =========================================================================
st.subheader("回測交易詳細日誌")

if not df_trades.empty:
    display_trades = df_trades.copy()
    display_trades['price'] = display_trades['price'].round(2)
    display_trades['commission'] = display_trades['commission'].round(2)
    display_trades['tax'] = display_trades['tax'].round(2)
    display_trades['cash'] = display_trades['cash'].round(2)
    display_trades['shares'] = display_trades['shares'].round(2)
    
    # 格式化呈現欄位 (適配投資組合包含 Ticker 欄位的情況)
    if is_portfolio:
        display_cols = ["datetime", "ticker", "action", "shares", "price", "commission", "tax", "cash", "event"]
        display_trades = display_trades[display_cols]
        display_trades.columns = ["時間", "交易標的", "交易動作", "交易股數", "成交價格", "手續費", "交易稅", "持有現金", "觸發事件說明"]
    else:
        # 若為個股，加入標的欄位以保證一致性
        display_trades.insert(1, 'ticker', ticker_option)
        display_cols = ["datetime", "ticker", "action", "shares", "price", "commission", "tax", "cash", "event"]
        display_trades = display_trades[display_cols]
        display_trades.columns = ["時間", "交易標的", "交易動作", "交易股數", "成交價格", "手續費", "交易稅", "持有現金", "觸發事件說明"]
        
    st.dataframe(
        display_trades.sort_values(by="時間", ascending=False),
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("此回測設定下未觸發任何交易進場。請試著調整參數或更換標的。")
