# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 專業交易工作站儀表板 (Trading Workstation Dashboard)

機構級交易終端介面，資訊架構以「交易決策優先」設計：
1. 訊號決策列：當前多空偏見、市況濾網狀態、今日關鍵價位、風險試算建議部位。
2. 風險調整後 KPI：CAGR / Sharpe / Sortino / Calmar / 曝險時間（非僅總報酬）。
3. 四分頁工作區：價格與訊號 / 績效分析 / 風險分析 / 交易日誌。
4. 風險分析整合蒙地卡羅交易重抽：以回撤「分布」而非歷史單一路徑設定風險預算。
5. 樣本數警告：交易筆數不足 30 筆時明確標示統計不可靠。
6. 台股慣例：紅漲綠跌、整股單位（張）試算。
"""

import os
import hmac
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from backtester import BacktestEngine
from data_ingestion import fetch_stock_data
from config import load_config
from db_security import safe_load_db_data, safe_save_to_sqlite, table_name_for
from instruments import equity_instrument
from security_utils import is_locked, register_failed_attempt, reset_lockout
from performance import rolling_sharpe
from monte_carlo import bootstrap_trades
import ladder_system
from ladder_system import (
    detect_market_structure, calculate_ladder_levels,
    calculate_chandelier_exit, calculate_adx, calculate_efficiency_ratio,
    calculate_regime_filter
)

# =========================================================================
# 0. 頁面配置（必須是第一個 Streamlit 指令，否則設密碼後會直接報錯）
# =========================================================================
st.set_page_config(
    page_title="TrendPoint | Trading Workstation",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 載入強型別規格配置
cfg = load_config()

# =========================================================================
# 1. 密碼驗證防護閘 (Security Password Gate)
# =========================================================================
def check_password() -> bool:
    """
    簡易密碼驗證防護閘。若 st.secrets 中未設定 password，則預設跳過，便於本地開發。
    加入登入鎖定機制，防止暴力破解。連續失敗超過上限將暫時鎖定。
    """
    if is_locked(st.session_state):
        st.warning("已達嘗試上限，請稍後再試。")
        return False

    try:
        if "password" not in st.secrets:
            return True
    except Exception:
        return True

    def password_entered():
        # 常數時間比較，避免時序側信道（健檢 2.4）
        if hmac.compare_digest(st.session_state.get("password", ""), st.secrets["password"]):
            st.session_state["password_correct"] = True
            reset_lockout(st.session_state)
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False
            register_failed_attempt(st.session_state)

    if "password_correct" not in st.session_state:
        st.text_input("請輸入密碼以存取工作站", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("請輸入密碼以存取工作站", type="password", on_change=password_entered, key="password")
        st.error("密碼錯誤，請重新輸入。")
        return False
    return True

if not check_password():
    st.stop()

# =========================================================================
# 2. 終端機設計系統 (Terminal Design System)
# =========================================================================
# 機構交易終端風格：高密度、髮絲線分隔、等寬數字、台股紅漲綠跌。
# 色彩語彙：琥珀 = 系統/警示、紅 = 多/漲、綠 = 空/跌、青 = 中性數據線。
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans+TC:wght@400;500;600;700&display=swap');

    :root {
        --bg: #f4f6f9;
        --panel: #ffffff;
        --panel-raised: #fbfcfe;
        --border: #d4dbe4;
        --hairline: #e3e8ef;
        --text: #1c2733;
        --muted: #5b6b7e;
        --faint: #9aa7b5;
        --up: #d6293d;        /* 台股慣例：紅漲 */
        --down: #0e8f6a;      /* 台股慣例：綠跌 */
        --amber: #b07d10;     /* 系統/警示 */
        --cyan: #1577a8;      /* 中性數據 */
        --mono: 'IBM Plex Mono', monospace;
        --sans: 'IBM Plex Sans TC', sans-serif;
    }

    html, body, [class*="css"] { font-family: var(--sans); }
    .stApp { background: var(--bg); }
    .stApp::before {
        content: "";
        position: fixed; inset: 0; pointer-events: none; z-index: 0;
        background:
            repeating-linear-gradient(0deg, transparent 0 2px, rgba(0,0,0,0.008) 2px 4px);
    }

    /* ── 終端機頂欄 ─────────────────────────────────────── */
    .term-header {
        display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
        border-bottom: 1px solid var(--amber);
        padding: 2px 0 10px 0; margin-bottom: 4px;
    }
    .term-header .brand {
        font-family: var(--mono); font-size: 20px; font-weight: 600;
        color: var(--text); letter-spacing: 3px;
    }
    .term-header .brand .tick { color: var(--amber); }
    .term-header .sub {
        font-family: var(--mono); font-size: 11px; color: var(--muted);
        letter-spacing: 2px; text-transform: uppercase;
    }
    .term-header .stamp {
        margin-left: auto; font-family: var(--mono); font-size: 11px;
        color: var(--faint); letter-spacing: 1px;
    }

    /* ── 訊號決策列 ─────────────────────────────────────── */
    .signal-strip {
        display: grid; grid-template-columns: 220px 1fr 1fr;
        gap: 1px; background: var(--hairline);
        border: 1px solid var(--border); margin: 14px 0 4px 0;
    }
    @media (max-width: 1100px) { .signal-strip { grid-template-columns: 1fr; } }
    .signal-cell { background: var(--panel); padding: 14px 18px; box-shadow: 0 1px 2px rgba(28,39,51,0.04); }
    .signal-cell .label {
        font-family: var(--mono); font-size: 10px; letter-spacing: 2px;
        text-transform: uppercase; color: var(--muted); margin-bottom: 8px;
    }
    .bias-badge {
        font-family: var(--mono); font-size: 30px; font-weight: 600;
        letter-spacing: 2px; line-height: 1.1;
    }
    .bias-badge.long  { color: var(--up);   text-shadow: 0 0 14px rgba(214,41,61,0.25); }
    .bias-badge.short { color: var(--down); text-shadow: 0 0 14px rgba(14,143,106,0.25); }
    .bias-badge.flat  { color: var(--amber); }
    .bias-note { font-size: 12px; color: var(--muted); margin-top: 6px; line-height: 1.5; }

    .level-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px 18px; }
    .level-item .lv-name { font-size: 11px; color: var(--muted); }
    .level-item .lv-val {
        font-family: var(--mono); font-size: 16px; font-weight: 500; color: var(--text);
    }
    .level-item .lv-val.amber { color: var(--amber); }
    .level-item .lv-val.cyan  { color: var(--cyan); }
    .level-item .lv-val.up    { color: var(--up); }
    .level-item .lv-val.down  { color: var(--down); }

    /* ── 濾網狀態燈 ─────────────────────────────────────── */
    .gate-row { display: flex; flex-direction: column; gap: 7px; }
    .gate {
        display: flex; align-items: center; gap: 8px;
        font-family: var(--mono); font-size: 12px; color: var(--text);
    }
    .gate .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
    .gate .dot.pass { background: var(--up); box-shadow: 0 0 6px rgba(214,41,61,0.45); }
    .gate .dot.block { background: var(--faint); }
    .gate .gv { margin-left: auto; color: var(--muted); }

    /* ── KPI 終端列 ─────────────────────────────────────── */
    .kpi-strip {
        display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
        gap: 1px; background: var(--hairline);
        border: 1px solid var(--border); margin: 10px 0 6px 0;
    }
    .kpi-cell { background: var(--panel); padding: 12px 16px 10px 16px; }
    .kpi-cell .k-name {
        font-family: var(--mono); font-size: 10px; letter-spacing: 1.5px;
        text-transform: uppercase; color: var(--muted); margin-bottom: 5px;
        white-space: nowrap;
    }
    .kpi-cell .k-val {
        font-family: var(--mono); font-size: 21px; font-weight: 600; color: var(--text);
        white-space: nowrap;
    }
    .kpi-cell .k-val.pos { color: var(--up); }
    .kpi-cell .k-val.neg { color: var(--down); }
    .kpi-cell .k-val.warn { color: var(--amber); }
    .kpi-cell .k-sub { font-family: var(--mono); font-size: 10px; color: var(--faint); margin-top: 3px; }

    /* ── 警示帶 ─────────────────────────────────────────── */
    .caution-band {
        border: 1px solid rgba(176,125,16,0.4); border-left: 3px solid var(--amber);
        background: rgba(176,125,16,0.07);
        font-family: var(--mono); font-size: 12px; color: var(--amber);
        padding: 8px 14px; margin: 8px 0; letter-spacing: 0.5px;
    }

    /* ── Streamlit 元件覆寫 ─────────────────────────────── */
    section[data-testid="stSidebar"] { background: var(--panel); border-right: 1px solid var(--border); }
    .stTabs [data-baseweb="tab-list"] { gap: 0; border-bottom: 1px solid var(--border); }
    .stTabs [data-baseweb="tab"] {
        font-family: var(--mono); font-size: 13px; letter-spacing: 1px;
        color: var(--muted); background: transparent; border-radius: 0;
        padding: 8px 18px;
    }
    .stTabs [aria-selected="true"] {
        color: var(--amber) !important;
        border-bottom: 2px solid var(--amber) !important;
    }
    div[data-testid="stMetric"] { background: var(--panel); border: 1px solid var(--border); padding: 10px 14px; }
</style>
""", unsafe_allow_html=True)

# =========================================================================
# 3. 數據載入輔助 (Cached Data Loading)
# =========================================================================
@st.cache_data(ttl=900, show_spinner=False)
def cached_fetch(ticker: str, period: str, interval: str):
    """
    快取版數據抓取：15 分鐘內重複的介面操作（拉桿、切分頁）不再重新打 API。
    專業工作站的基本要求——調一次參數不應該等一次網路請求。
    """
    return fetch_stock_data(ticker=ticker, period=period, interval=interval)

# =========================================================================
# 4. 側邊欄控制面板 (Control Panel)
# =========================================================================
st.sidebar.markdown(
    '<div style="font-family: var(--mono); letter-spacing:2px; font-size:13px; color:#b07d10;">'
    'CONTROL PANEL</div>', unsafe_allow_html=True
)
st.sidebar.header("策略與回測參數")

ticker_options = ["PORTFOLIO"] + cfg.data.tickers
ticker_option = st.sidebar.selectbox("交易標的 (Ticker)", ticker_options, index=1)
is_portfolio = (ticker_option == "PORTFOLIO")

st.sidebar.subheader("帳戶與風險")
init_capital = st.sidebar.number_input(
    "初始投資資金 (元)", min_value=10000.0, max_value=100000000.0,
    value=cfg.backtest.init_capital, step=50000.0
)
risk_pct = st.sidebar.slider(
    "單筆交易風險預算 (% 帳戶)", min_value=0.25, max_value=3.0, value=1.0, step=0.25,
    help="決策列的建議部位 = 風險預算 ÷ 止損距離，再取整至整張。專業部位管理從風險出發，不從金額出發。"
)

if not is_portfolio:
    p_opt = cfg.strategy.get_params_for_ticker(ticker_option)

    st.sidebar.subheader("指標與進場參數")
    atr_period = st.sidebar.slider("ATR 計算週期", 5, 40, int(p_opt.atr_period), 1)
    ladder_k = st.sidebar.slider("階梯觸發乘數 (k)", 0.5, 5.0, float(p_opt.ladder_k), 0.1)

    st.sidebar.subheader("動態部位與止損參數")
    ch_period = st.sidebar.slider("吊燈滾動週期", 10, 50, int(p_opt.chandelier_period), 1)
    ch_mult = st.sidebar.slider("吊燈止損乘數", 1.5, 6.0, float(p_opt.chandelier_mult), 0.1)
    time_limit = st.sidebar.slider("持有時間限制 (根數)", 5, 50, int(p_opt.time_limit), 1)

    st.sidebar.subheader("市況濾網 (Regime Filters)")
    use_adx = st.sidebar.checkbox("ADX 趨勢強度濾網", value=p_opt.use_adx_filter,
                                  help="ADX 低於門檻視為盤整，不進場。趨勢系統最大的敵人是盤整掃損。")
    adx_threshold = st.sidebar.slider("ADX 門檻", 10.0, 40.0, float(p_opt.adx_threshold), 1.0, disabled=not use_adx)
    use_ma = st.sidebar.checkbox("長均線大週期濾網 (200MA)", value=p_opt.use_ma_filter,
                                 help="價格低於長期均線不做多——最便宜的災難保險。")
    ma_period = st.sidebar.slider("長均線週期", 60, 300, int(p_opt.ma_period), 10, disabled=not use_ma)
    use_er = st.sidebar.checkbox("Kaufman ER 噪音濾網", value=p_opt.use_er_filter)

    st.sidebar.subheader("輔助技術指標")
    show_ema = st.sidebar.checkbox("EMA (20)", value=False)
    show_ema_200 = st.sidebar.checkbox("EMA (200)", value=True)
    show_kama = st.sidebar.checkbox("KAMA (10)", value=False)
    show_bb = st.sidebar.checkbox("布林通道", value=False)

    st.sidebar.subheader("交易摩擦成本")
    comm_rate = st.sidebar.number_input("手續費率 (單邊 %)", 0.0, 1.0, cfg.trading_cost.commission_rate * 100.0, 0.01) / 100.0
    tax_rate = st.sidebar.number_input("證交稅率 (%)", 0.0, 1.0, cfg.trading_cost.tax_rate * 100.0, 0.05) / 100.0
    slip_rate = st.sidebar.number_input("滑點率 (單邊 %)", 0.0, 1.0, cfg.trading_cost.slip_rate * 100.0, 0.01) / 100.0
else:
    st.sidebar.info("投資組合模式：使用 config.yaml 各標的最佳化參數與波動率倒數加權配置，參數不在此微調。")
    p_def = cfg.strategy.default
    atr_period, ladder_k = int(p_def.atr_period), float(p_def.ladder_k)
    ch_period, ch_mult, time_limit = int(p_def.chandelier_period), float(p_def.chandelier_mult), int(p_def.time_limit)
    use_adx, adx_threshold = p_def.use_adx_filter, float(p_def.adx_threshold)
    use_ma, ma_period, use_er = p_def.use_ma_filter, int(p_def.ma_period), p_def.use_er_filter
    show_ema = show_ema_200 = show_kama = show_bb = False
    comm_rate, tax_rate, slip_rate = cfg.trading_cost.commission_rate, cfg.trading_cost.tax_rate, cfg.trading_cost.slip_rate

# ── 觀察標的動態管理 ──────────────────────────────────────
st.sidebar.markdown("---")
with st.sidebar.expander("管理觀察標的"):
    st.markdown("**新增觀察標的**")
    new_ticker = st.text_input("輸入股票代號 (如 2454.TW)", key="new_ticker_input").strip()
    if st.button("確認新增"):
        if not new_ticker:
            st.error("請輸入有效的股票代號。")
        elif new_ticker in cfg.data.tickers:
            st.error("該標的已在觀察清單中。")
        else:
            import re
            if not re.match(r"^[a-zA-Z0-9.-]+$", new_ticker):
                st.error("股票代號格式錯誤，僅允許英文、數字、點與減號。")
            else:
                db_path_tmp = cfg.data.database_path
                with st.spinner(f"正在連線下載 {new_ticker} 最新數據……"):
                    df_verify = fetch_stock_data(ticker=new_ticker, period="10y", interval="1d")
                    df_verify_5m = fetch_stock_data(ticker=new_ticker, period="5d", interval="5m")

                if df_verify is not None and not df_verify.empty and df_verify_5m is not None and not df_verify_5m.empty:
                    daily_ok = safe_save_to_sqlite(df_verify, table_name_for(equity_instrument(new_ticker), "daily"), db_path_tmp)
                    m5_ok = safe_save_to_sqlite(df_verify_5m, table_name_for(equity_instrument(new_ticker), "5m"), db_path_tmp)

                    if daily_ok and m5_ok:
                        from config.config import save_config, SingleStrategyParams
                        cfg.data.tickers.append(new_ticker)
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
    st.markdown("**刪除觀察標的**")
    ticker_to_delete = st.selectbox("選擇要刪除的標的", options=cfg.data.tickers, key="delete_ticker_select")
    can_delete = len(cfg.data.tickers) > 1

    if st.button("確認刪除", disabled=not can_delete):
        db_path_tmp = cfg.data.database_path
        with st.spinner(f"正在移除 {ticker_to_delete} 並清理資料庫……"):
            from db_security import validate_table_name
            import sqlite3

            table_daily_del = table_name_for(equity_instrument(ticker_to_delete), "daily")
            table_5m_del = table_name_for(equity_instrument(ticker_to_delete), "5m")
            validate_table_name(table_daily_del)
            validate_table_name(table_5m_del)

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
# 5. 數據載入與回測執行 (Core Backtest Run)
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
    data_stamp = str(df_equity.index[-1].date()) if len(df_equity) else "N/A"
else:
    table_name = table_name_for(equity_instrument(ticker_option), "daily")

    # 抓取 10 年還原股價日線（快取 15 分鐘）；失敗時回退本地資料庫
    with st.spinner(f"正在更新 {ticker_option} 數據……"):
        df_kline = cached_fetch(ticker_option, "10y", "1d")

    if df_kline is not None and not df_kline.empty:
        if not safe_save_to_sqlite(df_kline, table_name, db_path):
            st.sidebar.warning("本地快取寫入失敗。")
    else:
        df_kline = safe_load_db_data(db_path, table_name)
        if not df_kline.empty:
            st.sidebar.warning("無法連線至 Yahoo Finance，已載入本地歷史快取數據。")
        else:
            st.error("獲取數據失敗：網路不可用且本地資料庫中無快取數據！請檢查網路連線。")
            st.stop()

    engine = BacktestEngine(
        initial_capital=init_capital,
        commission_rate=comm_rate,
        tax_rate=tax_rate,
        slippage_rate=slip_rate,
        lot_size=cfg.trading_cost.lot_size
    )
    results = engine.run_backtest(
        df=df_kline,
        atr_period=atr_period, k=ladder_k,
        ch_period=ch_period, ch_multiplier=ch_mult, time_limit=time_limit,
        use_adx_filter=use_adx, adx_threshold=adx_threshold,
        use_ma_filter=use_ma, ma_period=ma_period,
        use_er_filter=use_er,
        verbose=False
    )
    summary = results["summary"]
    df_equity = results["equity_curve"]
    df_trades = results["trades"]
    data_stamp = str(df_kline.index[-1].date())

# =========================================================================
# 6. 終端機頂欄 (Terminal Header)
# =========================================================================
st.markdown(f"""
<div class="term-header">
    <span class="brand">TREND<span class="tick">⌁</span>POINT</span>
    <span class="sub">多空階梯交易工作站</span>
    <span class="sub">{'PORTFOLIO MODE' if is_portfolio else ticker_option}</span>
    <span class="stamp">DATA AS OF {data_stamp} · ADJ PRICES · LOT {cfg.trading_cost.lot_size}</span>
</div>
""", unsafe_allow_html=True)

# =========================================================================
# 7. 訊號決策列 (Signal Decision Strip) — 單一標的模式
# =========================================================================
if not is_portfolio:
    # 計算決策所需的最新指標狀態
    sig_df = df_kline.copy()
    sig_df['atr'] = ladder_system.calculate_atr(
        ladder_system.calculate_tr(sig_df['high'], sig_df['low'], sig_df['close']), period=atr_period
    )
    mss_s, bos_s = detect_market_structure(sig_df, period=10)
    sig_df['ladder'] = calculate_ladder_levels(sig_df, sig_df['atr'], k=ladder_k)
    ch_long_s, _ = calculate_chandelier_exit(sig_df, sig_df['atr'], period=ch_period, multiplier=ch_mult)
    adx_series = calculate_adx(sig_df)
    er_series = calculate_efficiency_ratio(sig_df['close'])
    ma_series = sig_df['close'].rolling(window=ma_period, min_periods=1).mean()
    regime_series = calculate_regime_filter(
        sig_df, use_adx=use_adx, adx_threshold=adx_threshold,
        use_ma=use_ma, ma_period=ma_period, use_er=use_er
    )

    # 三關價（以昨日高低計算）
    y_high = sig_df['high'].iloc[-2] if len(sig_df) > 1 else sig_df['high'].iloc[-1]
    y_low = sig_df['low'].iloc[-2] if len(sig_df) > 1 else sig_df['low'].iloc[-1]
    mid_p = (y_high + y_low) / 2.0
    up_p = y_low + (y_high - y_low) * 1.382
    dn_p = y_high - (y_high - y_low) * 1.382

    last_close = float(sig_df['close'].iloc[-1])
    last_atr = float(sig_df['atr'].iloc[-1])
    last_ladder = float(sig_df['ladder'].iloc[-1])
    last_ch = float(ch_long_s.iloc[-1]) if pd.notna(ch_long_s.iloc[-1]) else last_close - ch_mult * last_atr
    last_adx = float(adx_series.iloc[-1])
    last_er = float(er_series.iloc[-1])
    last_ma = float(ma_series.iloc[-1])
    regime_ok_now = bool(regime_series.iloc[-1])
    above_mid = last_close > mid_p
    struct_now = int(mss_s.iloc[-1]) or int(bos_s.iloc[-1])

    # 多空偏見判定（決策優先：先講結論，再給依據）
    if last_close > up_p and regime_ok_now:
        bias, bias_cls = "強勢偏多", "long"
        bias_note = "價格突破上關價且市況濾網放行，極端趨勢盤，順勢操作。"
    elif above_mid and regime_ok_now:
        bias, bias_cls = "偏多", "long"
        bias_note = "價格高於中關價、市況濾網放行，僅執行多頭階梯邏輯。"
    elif not above_mid and last_close < dn_p:
        bias, bias_cls = "強勢偏空", "short"
        bias_note = "價格跌破下關價，空方極端趨勢，多單迴避。"
    elif not above_mid:
        bias, bias_cls = "偏空", "short"
        bias_note = "價格低於中關價，多頭訊號全數過濾，空手等待。"
    else:
        bias, bias_cls = "觀望", "flat"
        bias_note = "市況濾網未放行（盤整或位於長均線之下），不進場。"

    # 風險試算：風險預算 ÷ 初始止損距離 (2×ATR)，取整至整張
    stop_dist = 2.0 * last_atr
    initial_stop = last_close - stop_dist
    risk_amount = init_capital * (risk_pct / 100.0)
    lot = cfg.trading_cost.lot_size
    raw_shares = risk_amount / stop_dist if stop_dist > 0 else 0.0
    sized_shares = int(raw_shares // lot) * lot
    # 部位市值不得超過帳戶（防止低波動標的算出超額部位）
    while sized_shares * last_close > init_capital and sized_shares >= lot:
        sized_shares -= lot
    sized_cost = sized_shares * last_close

    gates_html = f"""
    <div class="gate"><span class="dot {'pass' if above_mid else 'block'}"></span>三關價（高於中關）<span class="gv">{mid_p:,.2f}</span></div>
    <div class="gate"><span class="dot {'pass' if (not use_adx or last_adx >= adx_threshold) else 'block'}"></span>ADX 趨勢強度 {'' if use_adx else '(停用)'}<span class="gv">{last_adx:.1f} / {adx_threshold:.0f}</span></div>
    <div class="gate"><span class="dot {'pass' if (not use_ma or last_close > last_ma) else 'block'}"></span>{ma_period}MA 大週期 {'' if use_ma else '(停用)'}<span class="gv">{last_ma:,.2f}</span></div>
    <div class="gate"><span class="dot {'pass' if struct_now == 1 else 'block'}"></span>結構訊號 (MSS/BOS)<span class="gv">{'看漲' if struct_now == 1 else ('看跌' if struct_now == -1 else '無')}</span></div>
    """

    st.markdown(f"""
    <div class="signal-strip">
        <div class="signal-cell">
            <div class="label">Market Bias · 當前偏見</div>
            <div class="bias-badge {bias_cls}">{bias}</div>
            <div class="bias-note">{bias_note}</div>
        </div>
        <div class="signal-cell">
            <div class="label">Key Levels · 今日關鍵價位</div>
            <div class="level-grid">
                <div class="level-item"><div class="lv-name">收盤價</div><div class="lv-val">{last_close:,.2f}</div></div>
                <div class="level-item"><div class="lv-name">上關價</div><div class="lv-val up">{up_p:,.2f}</div></div>
                <div class="level-item"><div class="lv-name">中關價</div><div class="lv-val amber">{mid_p:,.2f}</div></div>
                <div class="level-item"><div class="lv-name">下關價</div><div class="lv-val down">{dn_p:,.2f}</div></div>
                <div class="level-item"><div class="lv-name">階梯價位</div><div class="lv-val cyan">{last_ladder:,.2f}</div></div>
                <div class="level-item"><div class="lv-name">吊燈止損</div><div class="lv-val cyan">{last_ch:,.2f}</div></div>
                <div class="level-item"><div class="lv-name">ATR ({atr_period})</div><div class="lv-val">{last_atr:,.2f}</div></div>
                <div class="level-item"><div class="lv-name">效率比率 ER</div><div class="lv-val">{last_er:.2f}</div></div>
                <div class="level-item"><div class="lv-name">初始止損 (2×ATR)</div><div class="lv-val amber">{initial_stop:,.2f}</div></div>
            </div>
        </div>
        <div class="signal-cell">
            <div class="label">Gates &amp; Sizing · 濾網狀態與部位試算</div>
            <div class="gate-row">{gates_html}</div>
            <div style="border-top:1px solid var(--hairline); margin-top:10px; padding-top:8px;
                        font-family:var(--mono); font-size:12px; color:var(--text);">
                風險 {risk_pct:.2f}% = {risk_amount:,.0f} 元 ÷ 止損距離 {stop_dist:,.2f}
                → <span style="color:var(--amber); font-weight:600;">{sized_shares // lot} 張</span>
                <span style="color:var(--muted);">（{sized_shares:,} 股 ≈ {sized_cost:,.0f} 元）</span>
                {'<div style="color:var(--amber); margin-top:4px;">⚠ 風險預算不足以承載一張整股部位——加大資金、提高風險百分比，或改交易低價標的。</div>' if sized_shares < lot else ''}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# =========================================================================
# 8. KPI 終端列 (Risk-Adjusted KPI Strip)
# =========================================================================
def fmt_ratio(v):
    if v is None:
        return "—"
    if isinstance(v, float) and np.isinf(v):
        return "inf"
    return f"{v:.2f}"

ret = summary.get("total_return", 0.0)
cagr = summary.get("cagr", 0.0)
sharpe = summary.get("sharpe_ratio", 0.0)
sortino = summary.get("sortino_ratio", 0.0)
calmar = summary.get("calmar_ratio", 0.0)
mdd = summary.get("max_drawdown", 0.0)
exposure = summary.get("exposure")
n_trades = summary.get("total_trades", 0)
win_rate = summary.get("win_rate", 0.0)
pf = summary.get("profit_factor", 0.0)
uw_days = summary.get("max_underwater_days")

st.markdown(f"""
<div class="kpi-strip">
    <div class="kpi-cell"><div class="k-name">總報酬</div>
        <div class="k-val {'pos' if ret >= 0 else 'neg'}">{ret:+.2%}</div></div>
    <div class="kpi-cell"><div class="k-name">CAGR 年化</div>
        <div class="k-val {'pos' if cagr >= 0 else 'neg'}">{cagr:+.2%}</div></div>
    <div class="kpi-cell"><div class="k-name">Sharpe</div>
        <div class="k-val {'warn' if sharpe < 0.5 else ''}">{fmt_ratio(sharpe)}</div></div>
    <div class="kpi-cell"><div class="k-name">Sortino</div>
        <div class="k-val">{fmt_ratio(sortino)}</div></div>
    <div class="kpi-cell"><div class="k-name">Calmar</div>
        <div class="k-val">{fmt_ratio(calmar)}</div></div>
    <div class="kpi-cell"><div class="k-name">最大回撤</div>
        <div class="k-val neg">{mdd:.2%}</div>
        <div class="k-sub">水下最長 {f"{uw_days:.0f} 天" if uw_days else "—"}</div></div>
    <div class="kpi-cell"><div class="k-name">曝險時間</div>
        <div class="k-val">{f"{exposure:.1%}" if exposure is not None else "—"}</div></div>
    <div class="kpi-cell"><div class="k-name">交易 / 勝率</div>
        <div class="k-val {'warn' if n_trades < 30 else ''}">{n_trades} / {win_rate:.0%}</div>
        <div class="k-sub">PF {fmt_ratio(pf)}</div></div>
</div>
""", unsafe_allow_html=True)

if n_trades < 30:
    st.markdown(
        f'<div class="caution-band">⚠ SAMPLE WARNING — 交易樣本僅 {n_trades} 筆（&lt;30），'
        '所有統計指標僅供參考，不具統計顯著性。請拉長回測期間、放寬濾網或擴大標的池。</div>',
        unsafe_allow_html=True
    )

# =========================================================================
# 9. 工作區分頁 (Workspace Tabs)
# =========================================================================
tab_price, tab_perf, tab_risk, tab_log = st.tabs([
    "價格與訊號", "績效分析", "風險分析", "交易日誌"
])

PLOT_LAYOUT = dict(
    template="plotly_white",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(255,255,255,0.7)",
    font=dict(family="IBM Plex Mono, monospace", size=11, color="#1c2733"),
    margin=dict(l=10, r=10, t=30, b=10),
)
GRID = dict(gridcolor="rgba(28,39,51,0.08)")

# ── 9.1 價格與訊號 ────────────────────────────────────────
with tab_price:
    if is_portfolio:
        st.markdown("##### 投資組合標的相對走勢（歸一化 = 100）")
        try:
            normalized_dfs = []
            for t in cfg.data.tickers:
                df_t = safe_load_db_data(db_path, table_name_for(equity_instrument(t), "daily"))
                if not df_t.empty:
                    df_t[t] = (df_t['close'] / df_t['close'].iloc[0]) * 100.0
                    normalized_dfs.append(df_t[[t]])

            df_normalized = pd.concat(normalized_dfs, axis=1, sort=True).ffill().bfill()

            fig_norm = go.Figure()
            colors = ["#d6293d", "#1577a8", "#b07d10", "#7c4fc4", "#0e8f6a"]
            for idx, t in enumerate(df_normalized.columns):
                fig_norm.add_trace(go.Scatter(
                    x=df_normalized.index, y=df_normalized[t], name=t,
                    line=dict(color=colors[idx % len(colors)], width=1.8)
                ))
            fig_norm.update_layout(height=420, **PLOT_LAYOUT,
                                   xaxis=GRID, yaxis=dict(title="基準價 (%)", **GRID),
                                   legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
            st.plotly_chart(fig_norm, width='stretch')
        except Exception as e:
            st.warning(f"無法載入標的對照圖： {e} ")
    else:
        # 完整指標重算（繪圖用）
        plot_df = df_kline.copy()
        plot_df['atr'] = sig_df['atr']
        plot_df['ladder'] = sig_df['ladder']
        ch_long_p, _ = calculate_chandelier_exit(plot_df, plot_df['atr'], period=ch_period, multiplier=ch_mult)
        plot_df['ch_long'] = ch_long_p
        plot_df['ema'] = ladder_system.calculate_ema(plot_df['close'], span=20)
        plot_df['ema_200'] = ladder_system.calculate_ema(plot_df['close'], span=200)
        plot_df['kama'] = ladder_system.calculate_kama(plot_df['close'], period=10)
        bb_u, bb_m, bb_l = ladder_system.calculate_bollinger_bands(plot_df['close'], period=20, num_std=2.0)
        plot_df['bb_upper'], plot_df['bb_mid'], plot_df['bb_lower'] = bb_u, bb_m, bb_l

        fig_price = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            vertical_spacing=0.03, row_heights=[0.78, 0.22]
        )

        fig_price.add_trace(go.Candlestick(
            x=plot_df.index, open=plot_df['open'], high=plot_df['high'],
            low=plot_df['low'], close=plot_df['close'], name="K 線",
            increasing_line_color="#d6293d", decreasing_line_color="#0e8f6a",
            increasing_fillcolor="rgba(214,41,61,0.22)",
            decreasing_fillcolor="rgba(14,143,106,0.2)"
        ), row=1, col=1)

        fig_price.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['ladder'], name="多空階梯",
            line=dict(color="#1577a8", width=2.0, shape="hv"), opacity=0.9
        ), row=1, col=1)
        fig_price.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['ch_long'], name="吊燈止損",
            line=dict(color="#7c4fc4", width=1.3, dash="dashdot"), opacity=0.7
        ), row=1, col=1)

        if show_ema:
            fig_price.add_trace(go.Scatter(x=plot_df.index, y=plot_df['ema'], name="EMA 20",
                                line=dict(color="#d97f06", width=1.2), opacity=0.8), row=1, col=1)
        if show_ema_200:
            fig_price.add_trace(go.Scatter(x=plot_df.index, y=plot_df['ema_200'], name="EMA 200",
                                line=dict(color="#b07d10", width=1.4), opacity=0.85), row=1, col=1)
        if show_kama:
            fig_price.add_trace(go.Scatter(x=plot_df.index, y=plot_df['kama'], name="KAMA 10",
                                line=dict(color="#0d8077", width=1.4), opacity=0.8), row=1, col=1)
        if show_bb:
            fig_price.add_trace(go.Scatter(x=plot_df.index, y=plot_df['bb_upper'], name="BB 上軌",
                                line=dict(color="#2a93c9", width=0.9, dash="dash"), opacity=0.5), row=1, col=1)
            fig_price.add_trace(go.Scatter(x=plot_df.index, y=plot_df['bb_lower'], name="BB 下軌",
                                line=dict(color="#2a93c9", width=0.9, dash="dash"), opacity=0.5), row=1, col=1)

        # 進出場標記
        if not df_trades.empty:
            buys = df_trades[df_trades['action'] == 'BUY']
            if not buys.empty:
                fig_price.add_trace(go.Scatter(
                    x=pd.to_datetime(buys['datetime']), y=buys['price'], mode="markers",
                    marker=dict(symbol="triangle-up", size=11, color="#d6293d", line=dict(width=1, color="#1c2733")),
                    name="進場 BUY"
                ), row=1, col=1)
            halfs = df_trades[df_trades['action'] == 'SELL_HALF']
            if not halfs.empty:
                fig_price.add_trace(go.Scatter(
                    x=pd.to_datetime(halfs['datetime']), y=halfs['price'], mode="markers",
                    marker=dict(symbol="triangle-down", size=10, color="#b07d10", line=dict(width=1, color="#1c2733")),
                    name="止盈 50%"
                ), row=1, col=1)
            exits = df_trades[df_trades['action'] == 'SELL_ALL']
            if not exits.empty:
                fig_price.add_trace(go.Scatter(
                    x=pd.to_datetime(exits['datetime']), y=exits['price'], mode="markers",
                    marker=dict(symbol="x", size=10, color="#0e8f6a", line=dict(width=1, color="#1c2733")),
                    name="平倉 EXIT"
                ), row=1, col=1)

        # ADX 副圖（趨勢強度脈搏）
        adx_plot = calculate_adx(plot_df)
        fig_price.add_trace(go.Scatter(
            x=plot_df.index, y=adx_plot, name="ADX",
            line=dict(color="#b07d10", width=1.2), fill='tozeroy',
            fillcolor='rgba(176,125,16,0.08)'
        ), row=2, col=1)
        fig_price.add_hline(y=adx_threshold, line=dict(color="#9aa7b5", width=1, dash="dot"), row=2, col=1)

        # 預設視窗顯示最近一年，保留 10 年可回溯
        default_start = plot_df.index[-1] - pd.Timedelta(days=365)
        fig_price.update_layout(
            height=640, **PLOT_LAYOUT,
            xaxis=dict(rangeslider=dict(visible=False), range=[default_start, plot_df.index[-1]], **GRID),
            xaxis2=dict(range=[default_start, plot_df.index[-1]], **GRID),
            yaxis=GRID, yaxis2=dict(title="ADX", **GRID),
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
            xaxis_rangeselector=dict(
                buttons=[
                    dict(count=6, label="6M", step="month", stepmode="backward"),
                    dict(count=1, label="1Y", step="year", stepmode="backward"),
                    dict(count=3, label="3Y", step="year", stepmode="backward"),
                    dict(step="all", label="ALL"),
                ],
                bgcolor="rgba(255,255,255,0.9)", activecolor="rgba(176,125,16,0.25)",
                font=dict(color="#1c2733")
            )
        )
        st.plotly_chart(fig_price, width='stretch')

# ── 9.2 績效分析 ─────────────────────────────────────────
with tab_perf:
    fig_metrics = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                vertical_spacing=0.06, row_heights=[0.68, 0.32])
    fig_metrics.add_trace(go.Scatter(
        x=df_equity.index, y=df_equity['equity'], name="帳戶淨值",
        line=dict(color="#1577a8", width=2.0),
        fill='tozeroy', fillcolor='rgba(21,119,168,0.07)'
    ), row=1, col=1)

    peaks = df_equity['equity'].cummax()
    drawdowns = (df_equity['equity'] - peaks) / peaks * 100.0
    fig_metrics.add_trace(go.Scatter(
        x=df_equity.index, y=drawdowns, name="回撤 (%)",
        line=dict(color="#0e8f6a", width=1.2),
        fill='tozeroy', fillcolor='rgba(14,143,106,0.12)'
    ), row=2, col=1)

    fig_metrics.update_layout(height=430, showlegend=False, **PLOT_LAYOUT)
    fig_metrics.update_xaxes(**GRID)
    fig_metrics.update_yaxes(**GRID)
    fig_metrics.update_yaxes(title_text="淨值", row=1, col=1)
    fig_metrics.update_yaxes(title_text="水下 %", row=2, col=1)
    st.plotly_chart(fig_metrics, width='stretch')

    col_rs, col_hm = st.columns(2)

    with col_rs:
        st.markdown("##### 滾動 Sharpe（126 日）")
        st.caption("檢驗績效是否來自全期間的穩定貢獻——過擬合系統的特徵是只靠單一幸運區段。")
        rs = rolling_sharpe(df_equity['equity'], window=126)
        fig_rs = go.Figure()
        fig_rs.add_trace(go.Scatter(x=rs.index, y=rs, name="Rolling Sharpe",
                                    line=dict(color="#b07d10", width=1.5)))
        fig_rs.add_hline(y=0, line=dict(color="#9aa7b5", width=1, dash="dot"))
        fig_rs.add_hline(y=1, line=dict(color="#c2ccd6", width=1, dash="dot"))
        fig_rs.update_layout(height=300, showlegend=False, **PLOT_LAYOUT, xaxis=GRID, yaxis=GRID)
        st.plotly_chart(fig_rs, width='stretch')

    with col_hm:
        st.markdown("##### 月度報酬熱圖")
        st.caption("紅 = 正報酬、綠 = 負報酬（台股慣例）。觀察獲利月份的分散程度。")
        try:
            m_equity = df_equity['equity'].resample('ME').last()
            m_ret = m_equity.pct_change().dropna() * 100.0
            hm = pd.DataFrame({
                'year': m_ret.index.year,
                'month': m_ret.index.month,
                'ret': m_ret.values
            }).pivot(index='year', columns='month', values='ret').sort_index(ascending=False)

            fig_hm = go.Figure(data=go.Heatmap(
                z=hm.values,
                x=[f"{m:02d}" for m in hm.columns],
                y=[str(y) for y in hm.index],
                colorscale=[[0.0, "#0e8f6a"], [0.5, "#f3f5f8"], [1.0, "#d6293d"]],
                zmid=0.0,
                text=np.where(np.isnan(hm.values), "", np.round(hm.values, 1)),
                texttemplate="%{text}",
                textfont=dict(size=10, family="IBM Plex Mono"),
                hovertemplate="%{y}-%{x}: %{z:.2f}%<extra></extra>",
                showscale=False
            ))
            fig_hm.update_layout(height=300, **PLOT_LAYOUT,
                                 xaxis=dict(title="月份", **GRID), yaxis=GRID)
            st.plotly_chart(fig_hm, width='stretch')
        except Exception as e:
            st.info(f"數據不足以生成月度熱圖：{e}")

# ── 9.3 風險分析 ─────────────────────────────────────────
with tab_risk:
    trade_returns = summary.get("trade_returns", [])
    if not trade_returns:
        st.info("此設定下無交易紀錄，無法執行風險分析。")
    else:
        st.markdown("##### 蒙地卡羅交易序列重抽（5,000 次）")
        st.caption(
            "歷史回測的回撤只是「歷史上剛好出現的那一次」。把逐筆交易報酬打亂重抽，"
            "看分布的尾端——風險預算應以回撤分布的最差 5% 為準。"
        )
        mc = bootstrap_trades(trade_returns, n_sims=5000, seed=42)

        if "warning" in mc:
            st.markdown(f'<div class="caution-band">⚠ {mc["warning"]}</div>', unsafe_allow_html=True)

        if mc.get("n_source_trades", 0) > 0:
            # 重抽分布視覺化：自行重算路徑以畫直方圖
            rng = np.random.default_rng(42)
            arr = np.asarray(trade_returns, dtype=float)
            sims_ret, sims_mdd = [], []
            for _ in range(3000):
                sample = rng.choice(arr, size=len(arr), replace=True)
                eq = np.cumprod(1.0 + sample)
                pk = np.maximum.accumulate(eq)
                sims_ret.append(eq[-1] - 1.0)
                sims_mdd.append(((eq - pk) / pk).min())

            col_r, col_d = st.columns(2)
            with col_r:
                fig_mr = go.Figure(go.Histogram(
                    x=np.array(sims_ret) * 100, nbinsx=60,
                    marker=dict(color="rgba(21,119,168,0.45)", line=dict(color="#1577a8", width=0.5))
                ))
                fig_mr.add_vline(x=mc["total_return"][5] * 100, line=dict(color="#b07d10", width=1.5, dash="dash"),
                                 annotation_text="5%", annotation_font_color="#b07d10")
                fig_mr.add_vline(x=mc["total_return"][50] * 100, line=dict(color="#39434f", width=1.5, dash="dot"),
                                 annotation_text="中位", annotation_font_color="#39434f")
                fig_mr.update_layout(height=320, title="總報酬分布 (%)", showlegend=False,
                                     **PLOT_LAYOUT, xaxis=GRID, yaxis=GRID)
                st.plotly_chart(fig_mr, width='stretch')

            with col_d:
                fig_md = go.Figure(go.Histogram(
                    x=np.array(sims_mdd) * 100, nbinsx=60,
                    marker=dict(color="rgba(14,143,106,0.4)", line=dict(color="#0e8f6a", width=0.5))
                ))
                fig_md.add_vline(x=mc["max_drawdown"][5] * 100, line=dict(color="#b07d10", width=1.5, dash="dash"),
                                 annotation_text="最差 5%", annotation_font_color="#b07d10")
                fig_md.update_layout(height=320, title="最大回撤分布 (%)", showlegend=False,
                                     **PLOT_LAYOUT, xaxis=GRID, yaxis=GRID)
                st.plotly_chart(fig_md, width='stretch')

            tr_d, dd_d = mc["total_return"], mc["max_drawdown"]
            st.markdown(f"""
            <div class="kpi-strip">
                <div class="kpi-cell"><div class="k-name">報酬 最差5%</div>
                    <div class="k-val {'pos' if tr_d[5] >= 0 else 'neg'}">{tr_d[5]:+.1%}</div></div>
                <div class="kpi-cell"><div class="k-name">報酬 中位</div>
                    <div class="k-val {'pos' if tr_d[50] >= 0 else 'neg'}">{tr_d[50]:+.1%}</div></div>
                <div class="kpi-cell"><div class="k-name">報酬 最佳5%</div>
                    <div class="k-val pos">{tr_d[95]:+.1%}</div></div>
                <div class="kpi-cell"><div class="k-name">回撤 最差5%</div>
                    <div class="k-val neg">{dd_d[5]:.1%}</div></div>
                <div class="kpi-cell"><div class="k-name">回撤 中位</div>
                    <div class="k-val">{dd_d[50]:.1%}</div></div>
                <div class="kpi-cell"><div class="k-name">虧損機率</div>
                    <div class="k-val warn">{mc['prob_loss']:.1%}</div></div>
            </div>
            """, unsafe_allow_html=True)

        # 逐筆交易報酬分布
        st.markdown("##### 逐筆交易報酬分布")
        fig_tr = go.Figure(go.Bar(
            x=list(range(1, len(trade_returns) + 1)),
            y=[r * 100 for r in trade_returns],
            marker=dict(color=["#d6293d" if r >= 0 else "#0e8f6a" for r in trade_returns])
        ))
        fig_tr.update_layout(height=260, showlegend=False, **PLOT_LAYOUT,
                             xaxis=dict(title="交易序號", **GRID), yaxis=dict(title="報酬 %", **GRID))
        st.plotly_chart(fig_tr, width='stretch')

# ── 9.4 交易日誌 ─────────────────────────────────────────
with tab_log:
    if not df_trades.empty:
        display_trades = df_trades.copy()
        for col in ['price', 'commission', 'tax', 'cash']:
            display_trades[col] = display_trades[col].round(2)
        display_trades['shares'] = display_trades['shares'].astype(int)
        display_trades['張數'] = (display_trades['shares'] // cfg.trading_cost.lot_size).astype(int)

        if not is_portfolio:
            display_trades.insert(1, 'ticker', ticker_option)

        action_map = {"BUY": "▲ 進場", "SELL_HALF": "◆ 止盈50%", "SELL_ALL": "✕ 平倉"}
        display_trades['action'] = display_trades['action'].map(action_map).fillna(display_trades['action'])

        display_cols = ["datetime", "ticker", "action", "張數", "shares", "price", "commission", "tax", "cash", "event"]
        display_trades = display_trades[display_cols]
        display_trades.columns = ["時間", "標的", "動作", "張數", "股數", "成交價", "手續費", "交易稅", "持有現金", "觸發事件"]

        st.dataframe(
            display_trades.sort_values(by="時間", ascending=False),
            width='stretch', hide_index=True
        )

        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                "⬇ 下載交易日誌 CSV",
                df_trades.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{'portfolio' if is_portfolio else ticker_option}_trades.csv",
                mime="text/csv"
            )
        with col_dl2:
            st.download_button(
                "⬇ 下載淨值曲線 CSV",
                df_equity.to_csv().encode("utf-8-sig"),
                file_name=f"{'portfolio' if is_portfolio else ticker_option}_equity.csv",
                mime="text/csv"
            )
    else:
        st.info("此回測設定下未觸發任何交易進場。請試著調整參數、放寬市況濾網或更換標的。")
