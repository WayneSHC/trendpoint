#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 盤中時框評估協定的入口（spec 016）。

契約見 `specs/016-intraday-evaluation-protocol/contracts/cli.md`。
本檔**只做編排**——取數、合併、評估、渲染皆委派給三個模組，
自身不含任何判定邏輯（便於測試繞過 CLI 直接測邏輯）。

三個不變式：
  1. 不寫 `config/config.yaml`（組態覆寫全在記憶體內）
  2. 不寫 `trendpoint.db`、不建立任何 SQLite 表
  3. 不觸發推播（與 alerts.py 無任何呼叫關係）

用法：
    python run_intraday_eval.py accumulate --tickers "2330.TW 2454.TW"
    python run_intraday_eval.py evaluate   --out-json artifacts/report.json
    python run_intraday_eval.py universe
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from typing import Dict, List, Optional

import pandas as pd

import intraday_report as irep
import intraday_snapshot as isnap
import intraday_universe as iuni
from config import load_config

EXIT_OK = 0
EXIT_PARTIAL_FAILURE = 1
EXIT_BAD_ARGS = 2
EXIT_CORRUPT_STATE = 3


# ---------------------------------------------------------------------------
# accumulate
# ---------------------------------------------------------------------------


def _fetch(ticker: str, period: str) -> pd.DataFrame:
    """現抓 5 分線。走 `data_ingestion.fetch_stock_data` 以沿用既有的清洗與
    資料契約驗證；本容器的 agent proxy 對 yfinance 回 403，故實跑一律在
    GitHub Actions runner 上進行。"""
    from data_ingestion import fetch_stock_data

    df = fetch_stock_data(ticker=ticker, period=period, interval="5m")
    if df is None or df.empty:
        raise RuntimeError(f"取得 {ticker} 5m 失敗或回傳空資料（period={period}）")
    return df


def _load_offline(csv_dir: str, ticker: str) -> pd.DataFrame:
    path = os.path.join(csv_dir, f"{ticker.replace('.', '_')}_5m.csv")
    if not os.path.exists(path):
        raise RuntimeError(f"離線 CSV 不存在：{path}")
    df = pd.read_csv(path)
    dt_col = "datetime" if "datetime" in df.columns else df.columns[0]
    df[dt_col] = pd.to_datetime(df[dt_col])
    return df.set_index(dt_col).sort_index()


def cmd_accumulate(args) -> int:
    state_dir = args.state_dir
    tickers = [t for t in args.tickers.split() if t]
    if not tickers:
        print("錯誤：--tickers 不得為空", file=sys.stderr)
        return EXIT_BAD_ARGS

    prev_state = isnap.read_chain_state(state_dir)
    chain_broken = prev_state is None
    if chain_broken:
        # 三種成因（首次執行／逾 90 天保留期／上次 run 失敗）對資料的後果相同，
        # 對判讀的後果也相同，故不需區分處理——但**必須**顯式回報（FR-023）。
        print("⚠ 取不回前次累積歷史：鏈結中斷，自本次快照重新起算。")
        print("  這不是可以忽略的訊息——在鏈結重新累積至足夠長度前，")
        print("  樣本外切分不會成立，報告的效力標籤會維持樣本內描述性統計。")

    tickers_state: Dict[str, dict] = dict((prev_state or {}).get("tickers", {}))
    failures: List[str] = []
    origins: List[str] = []

    for ticker in sorted(tickers):
        try:
            incoming = (
                _load_offline(args.offline_csv_dir, ticker)
                if args.offline_csv_dir
                else _fetch(ticker, args.period)
            )
            existing = isnap.read_history(state_dir, ticker)
            merged, event = isnap.merge_history(existing, incoming)
            isnap.write_history(state_dir, ticker, merged)

            meta = isnap.describe(ticker, merged)
            gaps = [g.to_dict() for g in isnap.detect_gaps(merged)]
            if chain_broken:
                gaps.append(isnap.chain_restart_gap(meta["first_ts"]).to_dict())
            prior_events = list(tickers_state.get(ticker, {}).get("merge_events", []))
            tickers_state[ticker] = {
                "fingerprint": meta["fingerprint"],
                "bars": meta["bars"],
                "first_ts": meta["first_ts"],
                "last_ts": meta["last_ts"],
                "merge_events": prior_events + [event.to_dict()],
                "gaps": gaps,
            }
            origins.append(meta["first_ts"])
            print(
                f"  {ticker:<12} 併入 {event.bars_added:>6,} 根 → 共 {meta['bars']:>7,} 根"
                f"（重疊 {event.overlap_bars:,}、衝突 {event.conflicts:,}）"
            )
        except Exception as exc:      # 一檔失敗不影響其他檔
            failures.append(ticker)
            print(f"  {ticker:<12} 失敗：{exc}", file=sys.stderr)

    if not tickers_state:
        print("所有標的皆失敗，未寫入任何累積歷史", file=sys.stderr)
        return EXIT_PARTIAL_FAILURE

    chain_origin = (
        (prev_state or {}).get("chain_origin")
        if not chain_broken and prev_state
        else min(origins) if origins else ""
    )
    cfg = load_config()
    isnap.write_chain_state(
        state_dir,
        chain_origin=chain_origin or "",
        chain_broken=chain_broken,
        tickers=tickers_state,
        criteria_version=iuni.criteria_version(cfg.intraday_evaluation),
    )
    return EXIT_PARTIAL_FAILURE if failures else EXIT_OK


# ---------------------------------------------------------------------------
# evaluate / universe
# ---------------------------------------------------------------------------


def _load_histories(state_dir: str) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for ticker in isnap.list_tickers(state_dir):
        df = isnap.read_history(state_dir, ticker)
        if df is not None and len(df):
            out[ticker] = df
    return out


def _per_window_trades(df: pd.DataFrame, splits, cfg, p) -> List[int]:
    """逐測試窗的來回交易數——效力標籤升級的第二道門檻。"""
    counts = []
    for s in splits:
        mask = (df.index >= pd.Timestamp(s.test_start)) & (
            df.index <= pd.Timestamp(s.test_end) + pd.Timedelta(days=1)
        )
        window = df[mask]
        if len(window) < 10:
            counts.append(0)
            continue
        try:
            counts.append(irep.run_backtest(window, cfg, p)["round_trips"])
        except Exception:
            counts.append(0)
    return counts


def cmd_universe(args) -> int:
    cfg = load_config()
    histories = _load_histories(args.state_dir)
    if not histories:
        print(f"{args.state_dir} 內無累積歷史", file=sys.stderr)
        return EXIT_PARTIAL_FAILURE
    decisions, included = iuni.build_universe(histories, cfg.intraday_evaluation)
    print(f"納入準則版本：{iuni.criteria_version(cfg.intraday_evaluation)}")
    for d in decisions:
        mark = "✅ 納入" if d.included else "❌ 排除"
        print(f"  {mark}  {d.ticker:<12} {'、'.join(d.failed_criteria) or ''}")
        for k in sorted(d.measured):
            print(f"        {k:<24}{d.measured[k]}")
    return EXIT_OK


def cmd_evaluate(args) -> int:
    cfg = load_config()
    ie = cfg.intraday_evaluation

    try:
        histories = _load_histories(args.state_dir)
    except isnap.SnapshotError as exc:
        print(f"累積歷史損毀：{exc}", file=sys.stderr)
        return EXIT_CORRUPT_STATE

    if not histories:
        print(f"{args.state_dir} 內無累積歷史", file=sys.stderr)
        return EXIT_PARTIAL_FAILURE

    chain_state = isnap.read_chain_state(args.state_dir) or {}
    decisions, included = iuni.build_universe(histories, ie)

    if not included:
        # 明確失敗，不產出空報告當成結論。
        print("無標的通過納入準則——不產出報告。", file=sys.stderr)
        for d in decisions:
            print(f"  {d.ticker}: {'、'.join(d.failed_criteria)}", file=sys.stderr)
        return EXIT_PARTIAL_FAILURE

    per_ticker: List[dict] = []
    splits_payload = None
    scale_sweep: Optional[List[dict]] = None

    for ticker in sorted(included):
        df = included[ticker]
        p = cfg.strategy.get_params_for_ticker(ticker)

        split_result = isnap.split_windows(
            df, n_windows=ie.min_test_windows, train_ratio=ie.train_ratio
        )
        if splits_payload is None:
            splits_payload = split_result

        window_trades = (
            _per_window_trades(df, split_result.splits, cfg, p)
            if split_result.sufficient else []
        )
        label = irep.decide_validity_label(
            split_result, window_trades, ie.min_test_windows, ie.min_trades_per_window
        )

        if args.data_only:
            per_ticker.append({
                "ticker": ticker,
                "data_health": irep.build_data_health(df),
                "signal_density": {},
                "attrition": {},
                "trades": 0,
                "zero_trade_cause": None,
                "performance": {},
                "structure_period_hardcoded": irep.HARDCODED_STRUCTURE_PERIOD,
            })
            continue

        per_ticker.append(
            irep.build_per_ticker_result(ticker, df, cfg, p, label)
        )
        if args.scale_sweep and scale_sweep is None:
            scale_sweep = irep.run_scale_sweep(df, cfg, p, ie.scale_factors)

    inputs = {
        "accumulated_fingerprints": {
            t: isnap.fingerprint(histories[t]) for t in sorted(histories)
        },
        "chain_origin": chain_state.get("chain_origin", ""),
        "chain_broken": bool(chain_state.get("chain_broken", True)),
        "actual_span": {
            t: {
                "first_ts": histories[t].index[0].strftime(isnap.DATETIME_FORMAT),
                "last_ts": histories[t].index[-1].strftime(isnap.DATETIME_FORMAT),
                "trading_days": int(pd.Series(histories[t].index.date).nunique()),
            }
            for t in sorted(histories)
        },
        "criteria_version": iuni.criteria_version(ie),
        "label_thresholds": {
            "min_test_windows": ie.min_test_windows,
            "min_trades_per_window": ie.min_trades_per_window,
            "train_ratio": ie.train_ratio,
        },
        "lookback_days": ie.lookback_days,
        "structure_period_hardcoded": irep.HARDCODED_STRUCTURE_PERIOD,
    }

    report = irep.build_report(
        inputs=inputs,
        per_ticker=per_ticker,
        universe={
            "included": sorted(included),
            "decisions": [d.to_dict() for d in decisions],
        },
        windows=(splits_payload.to_dict() if splits_payload
                 else {"splits": [], "sufficient": False, "shortfall_trading_days": 0}),
        scale_sweep=scale_sweep,
        provenance={
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "state_dir": args.state_dir,
        },
    )

    if args.out_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
        with open(args.out_json, "w", encoding="utf-8") as f:
            f.write(irep.to_json(report))
        print(f"已寫出 {args.out_json}")

    text = irep.render_text(report)
    if args.out_text:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_text)), exist_ok=True)
        with open(args.out_text, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"已寫出 {args.out_text}")
    else:
        print(text)
    return EXIT_OK


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    cfg_default_state = "accumulated"
    ap = argparse.ArgumentParser(
        description="盤中時框評估協定（研究用途，不輸出策略有效性宣稱）"
    )
    sub = ap.add_subparsers(dest="command", required=True)

    acc = sub.add_parser("accumulate", help="取數並併入累積歷史")
    acc.add_argument("--tickers", required=True, help="空白分隔的取數目標")
    acc.add_argument("--period", default="60d", help="回溯期間，上限 60d")
    acc.add_argument("--state-dir", default=cfg_default_state)
    acc.add_argument("--offline-csv-dir", default=None,
                     help="改由本機 CSV 併入（測試與無網路環境）")
    acc.set_defaults(func=cmd_accumulate)

    ev = sub.add_parser("evaluate", help="對累積歷史產出報告")
    ev.add_argument("--state-dir", default=cfg_default_state)
    ev.add_argument("--out-json", default=None)
    ev.add_argument("--out-text", default=None)
    ev.add_argument("--scale-sweep", action="store_true")
    ev.add_argument("--data-only", action="store_true")
    ev.set_defaults(func=cmd_evaluate)

    un = sub.add_parser("universe", help="只印納入決定（除錯用）")
    un.add_argument("--state-dir", default=cfg_default_state)
    un.set_defaults(func=cmd_universe)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
