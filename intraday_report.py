# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 盤中評估報告（spec 016）。

**本模組的產出不是策略績效。** 它輸出的每一個報酬、回撤、獲利因子與勝率
都附帶效力標籤，且在通過樣本外驗證前一律標示為「樣本內描述性統計」
（FR-005）。序列化後的全文不得出現任何有效性宣稱措辭（FR-006，
由 `EFFICACY_CLAIM_PHRASES` 與 `tests/test_intraday_report.py` 檢核）。

## 兩個容易被改壞的設計

1. **JSON 是權威格式，文字報表由它渲染**。兩條計算路徑必然漂移——
   人看到的數字與被測試比對的數字不同，是最難發現的一類缺陷。
   `render_text` 因此只讀 `to_json` 的輸出結構，不自行計算任何值。

2. **效力標籤是累積狀態的純函式，不接受呼叫端指定**（research.md R6）。
   若可指定，遲早有人為了報告好看而指定它，FR-005 形同虛設。
"""

from __future__ import annotations

import json
import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from backtester import BacktestEngine
from ladder_system import build_indicator_frame

import intraday_snapshot as isnap

# backtester.py:209 與 monitor_signals.py 把 structure_period 寫死為 10。
# 這是**既有缺陷**（spec 016 FR-021 明列修正不在範圍內），但報告必須顯式
# 標示它，否則讀者會以為那是個組態參數。
HARDCODED_STRUCTURE_PERIOD = 10

# 效力標籤（research.md R6）。三者皆不構成有效性宣稱——
# out_of_sample_validated 只宣稱「樣本外驗證的**程序**已執行」。
LABEL_IN_SAMPLE = "in_sample_descriptive"
LABEL_OOS_INSUFFICIENT = "out_of_sample_insufficient"
LABEL_OOS_VALIDATED = "out_of_sample_validated"
VALIDITY_LABELS = (LABEL_IN_SAMPLE, LABEL_OOS_INSUFFICIENT, LABEL_OOS_VALIDATED)

# 零交易成因（互斥，判定順序即宣告順序）。
ZERO_NO_STRUCTURE = "no_structure_signal"
ZERO_FILTERS_REJECTED = "filters_rejected_all"
ZERO_BLOCKED_BY_POSITION = "all_candidates_blocked_by_position"
ZERO_NEVER_EXITED = "entered_but_never_exited"
ZERO_TRADE_CAUSES = (
    ZERO_NO_STRUCTURE,
    ZERO_FILTERS_REJECTED,
    ZERO_BLOCKED_BY_POSITION,
    ZERO_NEVER_EXITED,
)

# 措辭檢核清單（契約見 contracts/evaluation-report.md）。單一來源——
# 測試與人工檢視共用，分兩份會讓其中一份悄悄過期。
EFFICACY_CLAIM_PHRASES = (
    "策略有效",
    "確實有效",
    "可用於實盤",
    "建議啟用",
    "應該啟用",
    "證明有效",
    "穩定獲利",
    "勝率高",
    "值得投入實盤",
)

PERFORMANCE_KEYS = ("total_return", "max_drawdown", "profit_factor", "win_rate")

_RATIO_DECIMALS = 4


def _r(v, decimals: int = _RATIO_DECIMALS):
    """固定小數位。確定性的四個風險點之一（research.md R8）。"""
    if v is None:
        return None
    if isinstance(v, (np.floating, float)):
        if math.isnan(float(v)) or math.isinf(float(v)):
            return None
        return round(float(v), decimals)
    if isinstance(v, (np.integer, int)):
        return int(v)
    return v


# ---------------------------------------------------------------------------
# 資料體質
# ---------------------------------------------------------------------------


def build_data_health(df: pd.DataFrame) -> dict:
    """根數、交易日數、每日根數中位數、盤中缺口根數。

    缺口以「每日根數低於中位數的差額總和」估計——比對絕對時間軸更穩健，
    因為不同標的的交易時段長度可能不同。
    """
    idx = df.index
    per_day = pd.Series(1, index=idx).groupby(idx.date, sort=True).sum()
    median_bars = float(per_day.median()) if len(per_day) else 0.0
    gap_bars = int(np.clip(median_bars - per_day.values, 0, None).sum())
    return {
        "bars": int(len(df)),
        "trading_days": int(len(per_day)),
        "bars_per_day_median": _r(median_bars),
        "bars_per_day_cv": _r(
            float(per_day.std(ddof=0) / per_day.mean()) if len(per_day) > 1 and per_day.mean() else 0.0
        ),
        "gap_bars": gap_bars,
        "first_ts": idx[0].strftime(isnap.DATETIME_FORMAT),
        "last_ts": idx[-1].strftime(isnap.DATETIME_FORMAT),
    }


# ---------------------------------------------------------------------------
# 訊號密度與逐道流失
# ---------------------------------------------------------------------------


def build_indicator(df: pd.DataFrame, p, scale: float = 1.0) -> pd.DataFrame:
    """組裝指標框。走與回測相同的入口，確保兩端逐值一致。

    `scale` 為週期參數的倍率（US4 的尺度掃描）；1.0 即未縮放。
    覆寫**全在記憶體內**，不寫回 config。
    """
    def s(v: int) -> int:
        return max(2, int(round(v * scale)))

    return build_indicator_frame(
        df,
        structure_period=s(HARDCODED_STRUCTURE_PERIOD),
        atr_period=s(p.atr_period),
        ladder_k=p.ladder_k,
        chandelier_period=s(p.chandelier_period),
        chandelier_multiplier=p.chandelier_mult,
        include_regime=True,
        regime_kwargs=dict(
            use_adx=p.use_adx_filter, adx_period=s(p.adx_period),
            adx_threshold=p.adx_threshold,
            use_ma=p.use_ma_filter, ma_period=s(p.ma_period),
            use_er=p.use_er_filter, er_period=s(p.er_period),
            er_threshold=p.er_threshold,
        ),
    )


def build_signal_density(ind: pd.DataFrame, p, scale: float = 1.0) -> dict:
    """訊號層診斷。

    **BOS/MSS 一律分方向計**（FR-008）：現貨只走多方進場，把兩個方向合計
    會讓此處的基數與逐道流失的基數對不起來——那正是 2026-08-06 探查裡
    「訊號多而交易少」看不出成因的原因之一。
    """
    n = len(ind)
    out = {"bars": n}
    for col, prefix in (("bos_signal", "bos"), ("mss_signal", "mss")):
        if col in ind.columns:
            v = ind[col].fillna(0).astype(int)
            out[f"{prefix}_up"] = int((v == 1).sum())
            out[f"{prefix}_down"] = int((v == -1).sum())
        else:
            out[f"{prefix}_up"] = 0
            out[f"{prefix}_down"] = 0
    out["regime_ok"] = (
        int(ind["regime_ok"].fillna(False).astype(bool).sum())
        if "regime_ok" in ind.columns else 0
    )
    warm = int(ind["atr"].isna().sum()) if "atr" in ind.columns else 0
    ma_warm = int(round(p.ma_period * scale)) if p.use_ma_filter else 0
    out["warmup_bars"] = max(warm, ma_warm)
    out["usable_bars"] = max(0, n - out["warmup_bars"])
    return out


def build_attrition(ind: pd.DataFrame) -> dict:
    """進場合取的逐道流失——回答「哪一道濾網是瓶頸」。

    **對齊**：引擎在第 i 根判定時，結構訊號取 `iloc[i-2]`、其餘四道取
    `iloc[i-1]`（backtester.py:298-299）。此處把 BOS 於索引 k 的訊號與
    索引 k+1 的濾網配對，與引擎逐值一致。

    僅長側 BOS 續勢——這就是現貨的全部進場路徑：`enable_short` 對現貨是
    結構硬邊界，MSS 反轉分支需 `mss_reversal_entry=True`（預設 False）。
    """
    need = {"daily_open", "vwap", "mid_price", "regime_ok", "atr", "bos_signal"}
    if not need.issubset(ind.columns):
        return {
            "bos_signals": 0,
            "single_pass_rates": {},
            "conjunction_passed": 0,
            "missing_columns": sorted(need - set(ind.columns)),
        }

    bos = ind["bos_signal"].fillna(0).astype(int).values[:-1]
    k = ind.index[:-1][bos == 1]
    if len(k) == 0:
        return {"bos_signals": 0, "single_pass_rates": {}, "conjunction_passed": 0}

    s = ind.shift(-1).loc[k]
    atr_ok = s["atr"].notna() & (s["atr"] > 0)
    checks = {
        "momentum": s["close"] > s["open"],
        "trend": (s["close"] > s["daily_open"]) & (s["close"] > s["vwap"]),
        "volatility": atr_ok & ((s["high"] - s["low"]) > 1.2 * s["atr"]),
        "global": (s["close"] > s["mid_price"])
        & s["regime_ok"].fillna(False).astype(bool),
    }

    n = len(k)
    # 單道通過率是**順序無關**的量，故歸因以它為準；合取的逐道歸因隨排列而變。
    rates = {
        name: _r(int(mask.fillna(False).sum()) / n)
        for name, mask in sorted(checks.items())
    }
    cum = pd.Series(True, index=s.index)
    for _, mask in sorted(checks.items()):
        cum = cum & mask.fillna(False)

    bottleneck = min(rates.items(), key=lambda kv: (kv[1], kv[0]))[0] if rates else None
    return {
        "bos_signals": int(n),
        "single_pass_rates": rates,
        "conjunction_passed": int(cum.sum()),
        "bottleneck": bottleneck,
    }


# ---------------------------------------------------------------------------
# 回測與零交易分解
# ---------------------------------------------------------------------------


def run_backtest(df: pd.DataFrame, cfg, p, scale: float = 1.0) -> dict:
    """跑一次回測，回傳 (來回交易數, summary, trades)。

    成本一律經 `BacktestEngine(config=cfg)`——費率單一來源仍是
    `config.yaml` 的 `trading_cost`（憲章原則 II）。
    """
    def s(v: int) -> int:
        return max(2, int(round(v * scale)))

    engine = BacktestEngine(config=cfg)
    res = engine.run_backtest(
        df,
        atr_period=s(p.atr_period), k=p.ladder_k,
        ch_period=s(p.chandelier_period), ch_multiplier=p.chandelier_mult,
        time_limit=p.time_limit,
        use_adx_filter=p.use_adx_filter, adx_period=s(p.adx_period),
        adx_threshold=p.adx_threshold,
        use_ma_filter=p.use_ma_filter, ma_period=s(p.ma_period),
        use_er_filter=p.use_er_filter, er_period=s(p.er_period),
        er_threshold=p.er_threshold,
        asset_class="equity",
        verbose=False,
    )
    trades = res.get("trades", pd.DataFrame())
    # `trades` 是**逐筆買賣明細**（進場/出場各一列），不是來回交易數。
    # 直接取 len() 會把腿數當成交易數、虛報一倍。
    if len(trades) and "event" in trades.columns:
        entries = trades["event"].astype(str).str.contains("進場", na=False)
        exits = ~entries
        n_round_trips = int(min(entries.sum(), exits.sum()))
        n_entries = int(entries.sum())
    else:
        n_round_trips = 0
        n_entries = 0
    return {
        "round_trips": n_round_trips,
        "entries": n_entries,
        "summary": res.get("summary", {}) or {},
        "trades": trades,
    }


def classify_zero_trade(signal_density: dict, attrition: dict, bt: dict) -> Optional[str]:
    """零交易的成因分解（FR-007）。四個成因互斥，判定順序即宣告順序，
    第一個成立者勝——保證無「原因不明」（SC-004）。

    只在來回交易數為 0 時回傳非 None。
    """
    if bt["round_trips"] > 0:
        return None

    structure_total = (
        signal_density.get("bos_up", 0) + signal_density.get("bos_down", 0)
        + signal_density.get("mss_up", 0) + signal_density.get("mss_down", 0)
    )
    if structure_total == 0:
        return ZERO_NO_STRUCTURE
    if attrition.get("conjunction_passed", 0) == 0:
        return ZERO_FILTERS_REJECTED
    if bt["entries"] == 0:
        return ZERO_BLOCKED_BY_POSITION
    return ZERO_NEVER_EXITED


# ---------------------------------------------------------------------------
# 效力標籤
# ---------------------------------------------------------------------------


def decide_validity_label(
    split_result: Optional[isnap.SplitResult],
    per_window_trades: Optional[List[int]],
    min_test_windows: int,
    min_trades_per_window: int,
) -> str:
    """由累積狀態決定效力標籤。**純函式，不接受呼叫端指定**（research.md R6）。

    三態的判定：
    - 切不出足夠窗數（或根本沒切） → 樣本內描述性統計
    - 切得出但任一窗樣本量不足     → 樣本外但樣本量不足
    - 窗數與逐窗樣本量皆達標       → 樣本外驗證的**程序**已執行

    第三態**不宣稱策略有效**——FR-006 的措辭檢核對三者一律適用。
    """
    if split_result is None or not split_result.sufficient:
        return LABEL_IN_SAMPLE
    if len(split_result.splits) < min_test_windows:
        return LABEL_IN_SAMPLE
    if not per_window_trades:
        return LABEL_OOS_INSUFFICIENT
    if any(t < min_trades_per_window for t in per_window_trades):
        return LABEL_OOS_INSUFFICIENT
    return LABEL_OOS_VALIDATED


def _labeled(value, label: str) -> dict:
    """每個績效數字都是 {value, validity_label} 物件（FR-005）。

    裸數值即為缺陷——把標籤與值綁在同一個結構裡，是唯一能讓「引用時
    看得到邊界」在長期成立的做法。
    """
    return {"value": _r(value), "validity_label": label}


# ---------------------------------------------------------------------------
# 逐標的結果
# ---------------------------------------------------------------------------


def build_per_ticker_result(
    ticker: str, df: pd.DataFrame, cfg, p, label: str, scale: float = 1.0
) -> dict:
    ind = build_indicator(df, p, scale=scale)
    density = build_signal_density(ind, p, scale=scale)
    attrition = build_attrition(ind)
    bt = run_backtest(df, cfg, p, scale=scale)
    summary = bt["summary"]

    return {
        "ticker": ticker,
        "data_health": build_data_health(df),
        "signal_density": density,
        "attrition": attrition,
        "trades": bt["round_trips"],
        "zero_trade_cause": classify_zero_trade(density, attrition, bt),
        "performance": {
            key: _labeled(summary.get(key), label) for key in PERFORMANCE_KEYS
        },
        "structure_period_hardcoded": HARDCODED_STRUCTURE_PERIOD,
    }


# ---------------------------------------------------------------------------
# 跨標的合併統計
# ---------------------------------------------------------------------------


def build_pooled(per_ticker: List[dict]) -> List[dict]:
    """跨標的合併統計，**必帶離散度**（FR-002）。

    `pooled_value` 不得單獨序列化：pooling 隱含「這些標的同質」的假設，
    而 2026-08-06 實測顯示五道合取率在 2308 是 2.1%、在 1301 是 15.8%——
    差 7 倍。把離散度放在同一個結構裡，讀者才能當場檢驗那個假設。
    """
    metrics = {
        "conjunction_pass_rate": lambda t: (
            t["attrition"]["conjunction_passed"] / t["attrition"]["bos_signals"]
            if t["attrition"].get("bos_signals") else None
        ),
        "trades": lambda t: t["trades"],
        "bars": lambda t: t["data_health"]["bars"],
    }
    out = []
    for name in sorted(metrics):
        values = [metrics[name](t) for t in per_ticker]
        values = [v for v in values if v is not None]
        if not values:
            continue
        lo, hi = float(min(values)), float(max(values))
        out.append({
            "metric": name,
            "pooled_value": _r(float(np.mean(values))),
            "min": _r(lo),
            "max": _r(hi),
            "ratio": _r(hi / lo) if lo else None,
            "n_tickers": len(values),
        })
    return out


# ---------------------------------------------------------------------------
# 尺度掃描（US4）
# ---------------------------------------------------------------------------


def run_scale_sweep(df: pd.DataFrame, cfg, p, scale_factors: List[float]) -> List[dict]:
    """對週期參數施加**倍率**並逐倍率輸出反應曲線。

    倍率而非絕對值：要回答的問題是「尺度是否為瓶頸」，不是「哪個值最好」
    ——後者是調參，明列於 spec 016 範圍外。

    **曲線平坦即為「尺度不是瓶頸」的證據**，此時正確產出是刪除該假設
    （FR-018），不是實作參數時框語意。

    覆寫全在記憶體內，不寫回 config（沿用 run_b_segment.py 慣例）。
    """
    rows = []
    for factor in sorted(scale_factors):
        ind = build_indicator(df, p, scale=factor)
        attrition = build_attrition(ind)
        bt = run_backtest(df, cfg, p, scale=factor)
        rows.append({
            "factor": _r(factor),
            "single_pass_rates": attrition.get("single_pass_rates", {}),
            "conjunction_passed": attrition.get("conjunction_passed", 0),
            "bos_signals": attrition.get("bos_signals", 0),
            "trades": bt["round_trips"],
        })
    return rows


def summarize_scale_sweep(rows: List[dict]) -> dict:
    """把反應曲線化為一句**由量測驅動**的判讀（FR-018）。

    無掃描結果時回傳 `measured=False`，呼叫端**不得**輸出任何既定處方——
    這正是 `run_5m_evaluation.py::verdict` 原本做錯的事：它在交易數不足時
    無條件輸出「先做參數時框化」，而同一份實測顯示四道濾網通過率皆在
    24–70% 正常範圍、無一因尺度失效。
    """
    if not rows:
        return {"measured": False, "verdict": "未執行尺度掃描，無從判斷參數尺度是否為瓶頸"}

    conj = [r["conjunction_passed"] for r in rows]
    trades = [r["trades"] for r in rows]
    lo, hi = min(conj), max(conj)
    # 平坦的判準：合取數的極值比小於 2，且交易數的極差不超過 1 筆。
    flat = (hi <= max(1, 2 * lo)) and (max(trades) - min(trades) <= 1)
    if flat:
        verdict = (
            "尺度掃描顯示合取數與交易數對週期倍率不敏感："
            "本資料上參數尺度不構成瓶頸。此為推翻「需先做參數時框化」假設的證據。"
        )
    else:
        best = max(rows, key=lambda r: (r["conjunction_passed"], r["factor"]))
        verdict = (
            f"尺度掃描顯示合取數隨倍率變動（{lo} → {hi}），"
            f"倍率 {best['factor']} 時最高：參數尺度在本資料上構成瓶頸，"
            "其影響幅度如上表。後續是否調整參數時框語意，由另立規格處理。"
        )
    return {
        "measured": True,
        "flat": bool(flat),
        "conjunction_min": lo,
        "conjunction_max": hi,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# 報告組裝
# ---------------------------------------------------------------------------


def build_report(
    *,
    inputs: dict,
    per_ticker: List[dict],
    universe: dict,
    windows: dict,
    scale_sweep: Optional[List[dict]] = None,
    provenance: Optional[dict] = None,
) -> dict:
    """三區結構。`provenance` 排除在 SC-001 的確定性比對之外（research.md R8）。"""
    results = {
        "universe": universe,
        "per_ticker": sorted(per_ticker, key=lambda t: t["ticker"]),
        "pooled": build_pooled(per_ticker),
        "windows": windows,
        "scale_sweep": scale_sweep or [],
        "scale_sweep_verdict": summarize_scale_sweep(scale_sweep or []),
    }
    return {
        "schema_version": "1",
        "inputs": inputs,
        "results": results,
        "provenance": provenance or {},
    }


def to_json(report: dict) -> str:
    """序列化。鍵序排序 + 固定小數位——確定性的兩個風險點。"""
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def find_efficacy_claims(text: str) -> List[str]:
    """回傳文本中命中的有效性宣稱措辭（FR-006 / SC-012）。"""
    return [p for p in EFFICACY_CLAIM_PHRASES if p in text]


# ---------------------------------------------------------------------------
# 文字渲染
# ---------------------------------------------------------------------------

_LABEL_TEXT = {
    LABEL_IN_SAMPLE: "樣本內描述性統計（未經樣本外驗證，不可用於推論）",
    LABEL_OOS_INSUFFICIENT: "樣本外程序已切分，但逐窗樣本量不足",
    LABEL_OOS_VALIDATED: "樣本外驗證程序已執行（此標籤不表述策略優劣）",
}


def render_text(report: dict) -> str:
    """由 JSON 結構**渲染**文字報表，不自行計算任何數值。"""
    inp = report["inputs"]
    res = report["results"]
    L: List[str] = []
    bar = "=" * 74

    L.append(bar)
    L.append("TrendPoint 盤中時框評估報告（研究用途）")
    L.append(bar)
    L.append(f"  累積鏈起算    {inp.get('chain_origin', '（未知）')}")
    if inp.get("chain_broken"):
        L.append("  ⚠ 累積鏈中斷：前次累積歷史取不回，本次自上列時點重新起算。")
        L.append("    在鏈結重新累積至足夠長度前，樣本外切分不會成立。")
    L.append(f"  納入準則版本  {inp.get('criteria_version', '（未設定）')}")
    L.append(
        f"  硬編碼參數    structure_period={inp.get('structure_period_hardcoded')}"
        "（既有缺陷，非組態參數；spec 016 明列修正為範圍外）"
    )

    uni = res.get("universe", {})
    L.append("")
    L.append(bar)
    L.append("【一】標的納入")
    L.append(bar)
    L.append(f"  納入 {len(uni.get('included', []))} 檔：{', '.join(uni.get('included', [])) or '（無）'}")
    for d in uni.get("decisions", []):
        if not d.get("included"):
            L.append(f"  排除 {d['ticker']:<12} 未達：{', '.join(d.get('failed_criteria', []))}")

    L.append("")
    L.append(bar)
    L.append("【二】逐標的結果")
    L.append(bar)
    for t in res.get("per_ticker", []):
        dh, sd, at = t["data_health"], t["signal_density"], t["attrition"]
        L.append(f"  ── {t['ticker']}")
        L.append(f"     資料      {dh['bars']:,} 根 / {dh['trading_days']} 交易日"
                 f"（{dh['first_ts']} → {dh['last_ts']}）")
        L.append(f"     結構訊號  BOS 多 {sd['bos_up']:,} / 空 {sd['bos_down']:,}"
                 f"   MSS 多 {sd['mss_up']:,} / 空 {sd['mss_down']:,}")
        L.append(f"     暖機損失  {sd['warmup_bars']:,} 根 → 可用 {sd['usable_bars']:,} 根")
        if at.get("single_pass_rates"):
            rates = "  ".join(f"{k} {v * 100:.1f}%" for k, v in sorted(at["single_pass_rates"].items()))
            L.append(f"     單道通過  {rates}")
            L.append(f"     五道合取  {at['conjunction_passed']:,} / {at['bos_signals']:,}"
                     f"   瓶頸：{at.get('bottleneck', '—')}")
        L.append(f"     來回交易  {t['trades']:,}")
        if t.get("zero_trade_cause"):
            L.append(f"     零交易成因 {t['zero_trade_cause']}")
        for key, item in sorted(t["performance"].items()):
            v = item["value"]
            shown = "—" if v is None else f"{v:.4f}"
            L.append(f"     {key:<14}{shown:>12}   [{_LABEL_TEXT[item['validity_label']]}]")

    if res.get("pooled"):
        L.append("")
        L.append(bar)
        L.append("【三】跨標的合併統計   —— 離散度與合併值同列，供當場檢驗同質性假設")
        L.append(bar)
        L.append(f"  {'指標':<26}{'合併值':>12}{'極小':>12}{'極大':>12}{'倍差':>10}")
        for p in res["pooled"]:
            ratio = "—" if p["ratio"] is None else f"{p['ratio']:.2f}×"
            L.append(f"  {p['metric']:<26}{p['pooled_value']:>12}{p['min']:>12}"
                     f"{p['max']:>12}{ratio:>10}")

    w = res.get("windows", {})
    L.append("")
    L.append(bar)
    L.append("【四】樣本外切分")
    L.append(bar)
    if w.get("sufficient"):
        L.append(f"  切出 {len(w.get('splits', []))} 組互不重疊的測試窗：")
        for s in w.get("splits", []):
            L.append(f"    #{s['index']}  訓練 {s['train_start']} → {s['train_end']}"
                     f"   測試 {s['test_start']} → {s['test_end']}（{s['test_bars']:,} 根）")
    else:
        L.append("  ❌ 累積長度尚不足以進行樣本外驗證。")
        L.append(f"     還差 {w.get('shortfall_trading_days', 0)} 個交易日。")
        L.append("     在此之前，上列所有績效數字皆為樣本內描述性統計。")

    sweep = res.get("scale_sweep", [])
    L.append("")
    L.append(bar)
    L.append("【五】參數尺度敏感度")
    L.append(bar)
    if sweep:
        L.append(f"  {'倍率':>8}{'BOS 訊號':>12}{'五道合取':>12}{'來回交易':>12}")
        for r in sweep:
            L.append(f"  {r['factor']:>8}{r['bos_signals']:>12,}"
                     f"{r['conjunction_passed']:>12,}{r['trades']:>12,}")
    L.append(f"  → {res.get('scale_sweep_verdict', {}).get('verdict', '')}")

    L.append("")
    L.append(bar)
    L.append("本報告為研究產出。所有績效數字均附效力標籤，未經樣本外驗證前")
    L.append("一律為樣本內描述性統計，不構成對任何策略之判斷（憲章原則 III）。")
    L.append(bar)
    return "\n".join(L) + "\n"
