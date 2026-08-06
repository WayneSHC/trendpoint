#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 盤中時框可行性評估（研究用途，非績效驗證）

回答一個問題：**這份盤中資料跑得出統計意義嗎？**

背景：docs/reviews/2026-07-30-tradingview-mcp-workflow-review.md 第一節指出，
現貨即時監控走 5 分線（monitor_signals.py）、回測走日線（run_backtest.py），
同一個 structure_period=10 在兩端相差 78 倍，**推播訊號從未被回測驗證**。
該 review 第五節把「真正的盤中系統」封存於「前置是換 5m 資料源」——
yfinance 的 5m 只給 5 天（約 270 根），ma_period=200 連 1.35 個週期都跑不完。

本腳本吃一份盤中 CSV，輸出判斷「值不值得做」所需的診斷數字。

**本腳本刻意不輸出績效結論。** 交易數與期望值只用來看**樣本量**是否足夠支撐
任何統計推論；在 walk-forward 與消融之前，那些數字不構成對策略有效性的宣稱
（憲章原則 III）。

用法：
    python run_5m_evaluation.py data/2330_TW_5m.csv
    python run_5m_evaluation.py data/2330_TW_5m.csv --ticker 2330.TW
    python run_5m_evaluation.py data/2330_TW_5m.csv --data-only   # 略過回測
    python run_5m_evaluation.py --fetch 2330.TW                   # 現抓 yfinance 60 天

CSV 格式（csv_source.py:30-38 的契約）：
    datetime,open,high,low,close,volume
    2026-08-04 09:05:00,1085.0,1090.0,1084.0,1089.0,1234000
欄名大小寫不拘；首欄非 `datetime` 時取首欄為時間軸。
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from backtester import BacktestEngine
from config import load_config
from ladder_system import build_indicator_frame
from performance import infer_periods_per_year

# backtester.py:209 把 structure_period 寫死為 10（monitor_signals.py:179 亦同）。
# 這是本評估的核心變數之一，故顯式標出而非隱含沿用。
HARDCODED_STRUCTURE_PERIOD = 10

TRADING_DAYS_PER_YEAR = 252
SESSION_HOURS = 4.5          # 台股一般交易時段 09:00–13:30（performance.py:35 同此假設）

# 統計意義的下限。非硬性門檻，是判讀時的參考線：
# 少於 30 筆交易，任何期望值/PF 的信賴區間都寬到無法據以決策。
MIN_TRADES_FOR_INFERENCE = 30
MIN_BARS_PER_PARAM_CYCLE = 10   # 最長週期參數至少要能跑滿 10 個循環


def fetch_yfinance(ticker: str, period: str) -> pd.DataFrame:
    """現抓 yfinance 5 分線。

    period 上限為 **60 天**（Yahoo 對 interval<1d 的回溯限制；7 天是 1m 的限制）。
    走 data_ingestion.fetch_stock_data 以沿用既有的清洗與資料契約驗證。
    """
    from data_ingestion import fetch_stock_data
    df = fetch_stock_data(ticker=ticker, period=period, interval="5m")
    if df is None or df.empty:
        sys.exit(f"yfinance 取得 {ticker} 5m 失敗或回傳空資料（period={period}）")
    df.columns = [c.lower() for c in df.columns]
    missing = {"open", "high", "low", "close", "volume"} - set(df.columns)
    if missing:
        sys.exit(f"yfinance 回傳缺少欄位 {sorted(missing)}；實際：{sorted(df.columns)}")
    return df[["open", "high", "low", "close", "volume"]]


def load_csv(path: str) -> pd.DataFrame:
    """沿用 csv_source.py 的解析契約，但不經 registry（研究用，標的可不在 config）。"""
    df = pd.read_csv(path)
    dt_col = "datetime" if "datetime" in df.columns else df.columns[0]
    df[dt_col] = pd.to_datetime(df[dt_col])
    df = df.set_index(dt_col).sort_index()
    df.index.name = "datetime"
    df.columns = [c.lower() for c in df.columns]
    missing = {"open", "high", "low", "close", "volume"} - set(df.columns)
    if missing:
        sys.exit(f"CSV 缺少必要欄位 {sorted(missing)}；實際欄位：{sorted(df.columns)}")
    return df[["open", "high", "low", "close", "volume"]]


def describe_data(df: pd.DataFrame) -> dict:
    """資料體質：根數、覆蓋、每日根數、缺口。"""
    idx = df.index
    days = pd.Series(idx.date).nunique()
    median_gap = pd.Series(idx).diff().median()
    bars_per_day = len(df) / days if days else float("nan")

    # 盤中資料的「缺口」定義為同一交易日內相鄰棒間隔超過中位數的 3 倍。
    # 跨日間隔本來就大，故先按日分組再比較。
    intraday_gaps = 0
    for _, grp in df.groupby(idx.date):
        if len(grp) < 2:
            continue
        d = pd.Series(grp.index).diff().dropna()
        intraday_gaps += int((d > median_gap * 3).sum())

    times = pd.Series(idx.time)
    return {
        "bars": len(df),
        "start": idx.min(),
        "end": idx.max(),
        "days": days,
        "median_gap": median_gap,
        "bars_per_day": bars_per_day,
        "first_time": times.min(),
        "last_time": times.max(),
        "intraday_gaps": intraday_gaps,
        "periods_per_year": infer_periods_per_year(idx),
    }


def fmt_span(bars: int, median_gap: pd.Timedelta, bars_per_day: float) -> str:
    """把「根數」翻譯成人看得懂的時長。"""
    total = median_gap * bars
    if total >= pd.Timedelta(days=1) and bars_per_day and bars_per_day > 1:
        # 盤中：以交易日計，跨夜的掛鐘時間沒有意義
        d = bars / bars_per_day
        return f"{d:.1f} 交易日" if d >= 1 else f"{total.total_seconds() / 60:.0f} 分鐘"
    if bars_per_day and bars_per_day > 1:
        mins = total.total_seconds() / 60
        return f"{mins:.0f} 分鐘" if mins < 600 else f"{bars / bars_per_day:.1f} 交易日"
    return f"{bars} 交易日（約 {bars / 21:.1f} 個月）"


def report_data_health(info: dict) -> None:
    print("=" * 74)
    print("【一】資料體質")
    print("=" * 74)
    print(f"  根數                {info['bars']:,}")
    print(f"  期間                {info['start']}  →  {info['end']}")
    print(f"  交易日數            {info['days']:,}")
    print(f"  棒間隔（中位數）    {info['median_gap']}")
    print(f"  每日平均根數        {info['bars_per_day']:.1f}")
    print(f"  每日時間覆蓋        {info['first_time']} – {info['last_time']}")
    print(f"  盤中缺口數          {info['intraday_gaps']}"
          f"{'   ⚠ 資料有洞，會影響 rolling 計算' if info['intraday_gaps'] else ''}")
    print(f"  推定年化倍率        {info['periods_per_year']:,.0f} 根/年"
          f"（performance.infer_periods_per_year）")

    # 夜盤判定：台股日盤 09:00–13:30；期貨日盤 08:45–13:45、夜盤到隔日 05:00
    if info["last_time"].hour >= 15 or info["first_time"].hour < 8:
        print("  → 時間覆蓋含盤後/夜盤時段")


def report_param_scale(info: dict, p) -> None:
    """同一組參數在此時框與日線下各代表多長——本評估的核心對照。"""
    print()
    print("=" * 74)
    print("【二】參數尺度對照   ——「根數」在兩個時框下的實際意義")
    print("=" * 74)
    print("  repo 所有週期參數都是**根數**，config 只有一組值、不區分時框。")
    print("  同一個數字在日線與盤中差了兩個數量級：")
    print()
    print(f"  {'參數':<22}{'根數':>7}   {'本資料':<20}{'日線基準':<20}")
    print(f"  {'-' * 70}")

    gap, bpd = info["median_gap"], info["bars_per_day"]
    rows = [
        ("structure_period", HARDCODED_STRUCTURE_PERIOD, "★ 硬編碼於 backtester.py:209"),
        ("atr_period", p.atr_period, ""),
        ("chandelier_period", p.chandelier_period, ""),
        ("time_limit", p.time_limit, "持倉上限"),
        ("adx_period", p.adx_period, ""),
        ("ma_period", p.ma_period, "★ 最長週期，決定樣本下限"),
    ]
    for name, bars, note in rows:
        here = fmt_span(bars, gap, bpd)
        daily = f"{bars} 交易日（約 {bars / 21:.1f} 個月）"
        print(f"  {name:<22}{bars:>7}   {here:<20}{daily:<20}{note}")

    longest = max(p.ma_period, p.chandelier_period, p.atr_period,
                  HARDCODED_STRUCTURE_PERIOD)
    cycles = info["bars"] / longest if longest else 0
    print()
    print(f"  最長週期參數 = {longest} 根；本資料可跑 {cycles:.1f} 個完整循環")
    if cycles < MIN_BARS_PER_PARAM_CYCLE:
        print(f"  ❌ 低於 {MIN_BARS_PER_PARAM_CYCLE} 個循環——長週期濾網在這份資料上"
              f"幾乎沒有作用空間")
    else:
        print(f"  ✅ 達 {MIN_BARS_PER_PARAM_CYCLE} 個循環以上")


def report_signal_density(df: pd.DataFrame, p) -> pd.DataFrame:
    """訊號層診斷：走 backtester.py:207 的同一組參數，確保與回測一致。"""
    print()
    print("=" * 74)
    print("【三】訊號密度")
    print("=" * 74)

    ind = build_indicator_frame(
        df,
        structure_period=HARDCODED_STRUCTURE_PERIOD,
        atr_period=p.atr_period,
        ladder_k=p.ladder_k,
        chandelier_period=p.chandelier_period,
        chandelier_multiplier=p.chandelier_mult,
        include_regime=True,
        regime_kwargs=dict(
            use_adx=p.use_adx_filter, adx_period=p.adx_period,
            adx_threshold=p.adx_threshold,
            use_ma=p.use_ma_filter, ma_period=p.ma_period,
            use_er=p.use_er_filter, er_period=p.er_period,
            er_threshold=p.er_threshold,
        ),
    )

    n = len(ind)
    for col, label in [("bos_signal", "BOS（續勢）"),
                       ("mss_signal", "MSS（反轉）"),
                       ("regime_ok", "regime_ok（多方市況通過）"),
                       ("regime_ok_short", "regime_ok_short（空方）")]:
        if col not in ind.columns:
            continue
        cnt = int(ind[col].fillna(False).astype(bool).sum())
        print(f"  {label:<28}{cnt:>7,} 根 / {n:,}  （{cnt / n * 100:5.2f}%）")

    warm = int(ind["atr"].isna().sum())
    ma_warm = p.ma_period if p.use_ma_filter else 0
    print(f"\n  ATR 暖機損失              {warm:>7,} 根")
    print(f"  長均線暖機（ma_period）   {ma_warm:>7,} 根")
    print(f"  → 實際可用於判定的根數    {max(0, n - max(warm, ma_warm)):>7,} 根")
    return ind


def report_filter_attrition(ind: pd.DataFrame) -> None:
    """進場合取的逐道流失——回答「哪一道濾網是瓶頸」。

    進場需**同時**滿足五道（`ladder_system.check_entry_signal:668-705`）：
    結構 / 動能 / 趨勢 / 波動 / 全域。訊號多而交易少時，光看訊號數不知道
    是哪一道把單子殺掉的，本段即為此而存在——它直接決定參數時框化該從
    哪個參數下手。

    **對齊**：回測引擎在第 i 根判定時，結構訊號取 `iloc[i-2]`、其餘四道取
    `iloc[i-1]`（backtester.py:298-299）。故此處把 BOS 於索引 k 的訊號與
    索引 k+1 的濾網配對，與引擎逐值一致。

    僅長側（現貨 enable_short 為結構硬邊界）。
    """
    print()
    print("=" * 74)
    print("【三之二】進場合取的逐道流失   —— 哪一道是瓶頸")
    print("=" * 74)

    need = {"daily_open", "vwap", "mid_price", "regime_ok", "atr"}
    if not need.issubset(ind.columns):
        print(f"  缺少欄位 {sorted(need - set(ind.columns))}，略過")
        return

    k = ind.index[:-1][ind["bos_signal"].fillna(0).astype(int).values[:-1] == 1]
    if len(k) == 0:
        print("  無 BOS 訊號，無可分析對象")
        return
    s = ind.shift(-1).loc[k]        # 判定根（訊號根的下一根）

    atr_ok = s["atr"].notna() & (s["atr"] > 0)
    checks = {
        "動能（收陽線）": s["close"] > s["open"],
        "趨勢（>當日開盤 且 >VWAP）": (s["close"] > s["daily_open"]) & (s["close"] > s["vwap"]),
        "波動（振幅 > 1.2×ATR）": atr_ok & ((s["high"] - s["low"]) > 1.2 * s["atr"]),
        "全域（>三關價中值 且 regime_ok）": (s["close"] > s["mid_price"]) & s["regime_ok"].fillna(False).astype(bool),
    }

    n = len(k)
    print(f"  BOS 訊號（結構端已通過）  {n:>6,} 根\n")

    # 單道通過率是**順序無關**的量，故歸因以它為準。
    rates = {name: int(mask.fillna(False).sum()) for name, mask in checks.items()}
    print(f"  {'單看每一道的通過率':<34}{'通過':>7}{'通過率':>10}")
    print(f"  {'-' * 62}")
    for name, c in rates.items():
        print(f"  {name:<34}{c:>7,}{c / n * 100:>9.1f}%")

    print(f"\n  {'逐道累積（合取）':<34}{'剩餘':>7}{'本道殺掉':>12}")
    print(f"  {'-' * 62}")
    cum = pd.Series(True, index=s.index)
    prev = n
    for name, mask in checks.items():
        cum = cum & mask.fillna(False)
        left = int(cum.sum())
        print(f"  {name:<34}{left:>7,}{prev - left:>12,}")
        prev = left
    print("  （「本道殺掉」隨排列順序而變——合取沒有唯一歸因。歸因請看上表。）")

    final = int(cum.sum())
    print(f"\n  → 五道全過（可進場根數）  {final:,}")

    bottleneck, passed = min(rates.items(), key=lambda kv: kv[1])
    print(f"  → **瓶頸：{bottleneck}**——單道通過率僅 {passed / n * 100:.1f}%，"
          f"為四道中最低")
    if final == 0:
        print("\n  ⚠ 合取為 0——交易數 0 的成因在此，不是回測引擎異常。")


def report_backtest(df: pd.DataFrame, cfg, p, info: dict) -> int:
    print()
    print("=" * 74)
    print("【四】回測樣本量   —— 看的是筆數，不是績效")
    print("=" * 74)

    engine = BacktestEngine(config=cfg)
    res = engine.run_backtest(
        df,
        atr_period=p.atr_period, k=p.ladder_k,
        ch_period=p.chandelier_period, ch_multiplier=p.chandelier_mult,
        time_limit=p.time_limit,
        use_adx_filter=p.use_adx_filter, adx_period=p.adx_period,
        adx_threshold=p.adx_threshold,
        use_ma_filter=p.use_ma_filter, ma_period=p.ma_period,
        use_er_filter=p.use_er_filter, er_period=p.er_period,
        er_threshold=p.er_threshold,
        asset_class="equity",
    )
    trades = res.get("trades", pd.DataFrame())
    summary = res.get("summary", {}) or {}
    n_trades = len(trades)

    print(f"  完成交易筆數        {n_trades:,}")
    if n_trades:
        span_days = info["days"]
        print(f"  交易頻率            每 {span_days / n_trades:.1f} 個交易日一筆")
    for key, label in [("total_return", "總報酬"), ("max_drawdown", "最大回撤"),
                       ("profit_factor", "獲利因子"), ("win_rate", "勝率")]:
        if key in summary and summary[key] is not None:
            v = summary[key]
            print(f"  {label:<20}{v:.4f}" if isinstance(v, (int, float, np.floating))
                  else f"  {label:<20}{v}")

    print()
    if n_trades < MIN_TRADES_FOR_INFERENCE:
        print(f"  ❌ 交易數 < {MIN_TRADES_FOR_INFERENCE}：上面的績效數字**不可用於任何推論**，")
        print("     信賴區間寬到無意義。需要更長的歷史。")
    else:
        print(f"  ✅ 交易數 ≥ {MIN_TRADES_FOR_INFERENCE}：樣本量足以進入下一步"
              f"（walk-forward + 消融）。")
    print("     ——但**這不代表策略有效**。在樣本外驗證與摩擦成本敏感度分析之前，")
    print("     不得據此宣稱盤中系統可用（憲章原則 III）。")
    return n_trades


def verdict(info: dict, p, n_trades: int | None) -> None:
    print()
    print("=" * 74)
    print("【判讀】這份資料值不值得往下走")
    print("=" * 74)
    longest = max(p.ma_period, p.chandelier_period, HARDCODED_STRUCTURE_PERIOD)
    cycles = info["bars"] / longest if longest else 0

    print(f"  對照基準：yfinance 的 5m 只給 5 天 ≈ 270 根"
          f"（2026-07-30 review 判定跑不出統計意義的起點）")
    print(f"  本資料：{info['bars']:,} 根 = 該基準的 {info['bars'] / 270:.1f} 倍")
    print()
    data_ok = cycles >= 50
    data_marginal = MIN_BARS_PER_PARAM_CYCLE <= cycles < 50

    if not data_ok and not data_marginal:
        print("  ❌ 資料量不足。結論：維持 2026-07-30 review 第五節的封存狀態，")
        print("     並將其從『待辦』升格為『已驗證的結案』——這同樣是有價值的產出。")
        return

    if data_marginal:
        print("  ⚠ 資料量勉強。可做初步判讀，但結論偏弱，不足以支撐改變預設組態。")
    else:
        print("  ✅ 資料量充足（可跑滿長週期參數）。")

    # 資料夠但交易少，與資料不夠是**不同的問題**，處方也不同：
    # 前者要改參數尺度，後者要換資料源。混為一談會走錯路。
    if n_trades is None:
        print("     （--data-only 模式，未評估交易樣本量）")
    elif n_trades < MIN_TRADES_FOR_INFERENCE:
        print()
        print(f"  ⚠ **但完成交易僅 {n_trades} 筆**——資料夠，訊號卻不夠。")
        print("     這不是資料源問題，是**參數尺度問題**：日線調校的週期套到 5 分線，")
        print(f"     structure_period={HARDCODED_STRUCTURE_PERIOD} 從 10 個交易日縮成 50 分鐘、")
        print(f"     ma_period={p.ma_period} 從 9.5 個月縮成 3.7 個交易日。")
        print("     → 先做**參數時框化**（讓參數帶時框語意，而非一組共用根數），")
        print("       再重跑本評估。在那之前不要下『盤中系統沒用』的結論。")
    else:
        print()
        print(f"  ✅ 完成交易 {n_trades} 筆，樣本量足以進入下一步：")
        print("     walk-forward 切分 + 與日線基準的消融對照。")
        print("     並處理參數時框化——同一組根數在兩個時框下語意不同，")
        print("     不先解決這點，多時框只是把不一致從兩條路徑擴大到 N 條。")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="盤中時框可行性評估（研究用途，不輸出績效結論）")
    ap.add_argument("csv", nargs="?", default=None, help="盤中 OHLCV CSV 路徑")
    ap.add_argument("--fetch", metavar="TICKER", default=None,
                    help="改為現抓 yfinance 5 分線（與 csv 二擇一）")
    ap.add_argument("--period", default="60d",
                    help="--fetch 的回溯期間，上限 60d（Yahoo 對 5m 的限制）")
    ap.add_argument("--save-csv", default=None,
                    help="--fetch 時另存 CSV 供重現（預設不存）")
    ap.add_argument("--ticker", default=None,
                    help="用哪個標的的 config 參數（預設走 strategy.default）")
    ap.add_argument("--data-only", action="store_true",
                    help="只跑資料體質與參數尺度，略過訊號與回測")
    args = ap.parse_args()

    if bool(args.csv) == bool(args.fetch):
        ap.error("請擇一提供 CSV 路徑或 --fetch TICKER")

    if args.fetch:
        df = fetch_yfinance(args.fetch, args.period)
        source = f"yfinance {args.fetch} 5m period={args.period}"
        if args.save_csv:
            df.to_csv(args.save_csv)
            print(f"已存 {args.save_csv}")
    else:
        df = load_csv(args.csv)
        source = args.csv

    cfg = load_config()
    p = (cfg.strategy.get_params_for_ticker(args.ticker or args.fetch)
         if (args.ticker or args.fetch) else cfg.strategy.default)

    print(f"\n來源：{source}")
    _t = args.ticker or args.fetch
    print(f"參數來源：{'get_params_for_ticker(' + _t + ')' if _t else 'strategy.default'}")
    print()

    info = describe_data(df)
    report_data_health(info)
    report_param_scale(info, p)

    n_trades = None
    if not args.data_only:
        ind = report_signal_density(df, p)
        report_filter_attrition(ind)
        n_trades = report_backtest(df, cfg, p, info)

    verdict(info, p, n_trades)
    print()


if __name__ == "__main__":
    main()
