#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
FinMind 盤中資料集探針 —— 一次回答四個未知
（TrendPoint 5m 可行性評估的前置查核，不寫入任何檔案或 DB）

  1. 權限    TaiwanStockKBar / TaiwanFuturesTick 免費層能不能取
  2. 解析度  KBar 是 1 分還是 5 分
  3. 夜盤    tick / KBar 的時間覆蓋範圍
  4. 起始日  分鐘級歷史從哪年開始

用法：
    export FINMIND_TOKEN='...'                    # 必要；GitHub Actions 走 Secrets
    export PROBE_STOCK_ID=2330 PROBE_FUTURES_ID=TX  # 選填，預設即此
    python probe_finmind_intraday.py

沿用 spec 010 D4 的模式：REST 直打、免 SDK、token 走環境變數 Authorization header
（不放 URL 查詢參數——會隨 HTTPError 訊息洩入日誌）。

**探針性質**：唯讀、不寫任何檔案、不碰 trendpoint.db、不改組態。
唯一目的是回答「盤中資料集取不取得到」，供 5m 可行性評估決策用。
"""

from __future__ import annotations

import os
import sys
import time

import requests

URL = "https://api.finmindtrade.com/api/v4/data"
THROTTLE = 1.2          # 秒；免費層 600 req/hr，本腳本約 20 請求
PROBE_YEARS = [2015, 2018, 2020, 2022, 2024, 2026]
# 每年挑三個平常的週間日，避開連假；取有資料者
PROBE_DAYS = ["-03-12", "-06-11", "-09-10"]

STOCK_ID = os.environ.get("PROBE_STOCK_ID", "").strip() or "2330"
FUTURES_ID = os.environ.get("PROBE_FUTURES_ID", "").strip() or "TX"


def token() -> str:
    tok = os.environ.get("FINMIND_TOKEN", "").strip()
    if not tok:
        sys.exit("FINMIND_TOKEN 未設定 —— export FINMIND_TOKEN='...' 後再跑")
    return tok


def fetch(dataset: str, data_id: str, date: str, tok: str) -> tuple[int, str, list]:
    """回傳 (http_status, msg, rows)。網路/解析錯誤不中斷整體探測。"""
    try:
        r = requests.get(
            URL,
            params={"dataset": dataset, "data_id": data_id, "start_date": date},
            headers={"Authorization": f"Bearer {tok}"},
            timeout=120,
        )
    except requests.RequestException as e:
        return -1, f"連線失敗：{str(e).replace(tok, '***')}", []
    try:
        payload = r.json()
    except ValueError:
        return r.status_code, f"非 JSON 回應：{r.text[:120]}", []
    return r.status_code, str(payload.get("msg", "")), payload.get("data") or []


def minute_field(row: dict) -> str | None:
    """KBar 的時間欄位名不確定，逐一試。"""
    for k in ("minute", "time", "Time", "datetime", "date_time"):
        if k in row:
            return str(row[k])
    return None


def report_resolution(rows: list) -> None:
    """由相鄰時間戳推解析度。"""
    stamps = [minute_field(r) for r in rows[:400]]
    stamps = [s for s in stamps if s]
    if len(stamps) < 3:
        print("     解析度：無法判定（時間欄位缺失或樣本不足）")
        return
    uniq = sorted(set(stamps))
    print(f"     時間欄位範例：{uniq[:3]} ... {uniq[-2:]}")
    print(f"     當日不重複時間戳數：{len(uniq)}")

    def to_min(s: str) -> int | None:
        s = s.strip()
        if ":" in s:                       # "09:05:00" / "09:05"
            p = s.split(":")
            try:
                return int(p[0]) * 60 + int(p[1])
            except ValueError:
                return None
        if s.isdigit():                    # 905 或 0905
            v = int(s)
            return (v // 100) * 60 + v % 100 if v > 59 else v
        return None

    mins = [m for m in (to_min(s) for s in uniq) if m is not None]
    if len(mins) >= 3:
        gaps = [b - a for a, b in zip(mins, mins[1:]) if 0 < b - a <= 120]
        if gaps:
            common = min(set(gaps), key=lambda g: (-gaps.count(g), g))
            print(f"     → 推定解析度：{common} 分鐘")
        lo, hi = min(mins), max(mins)
        print(f"     → 時間覆蓋：{lo // 60:02d}:{lo % 60:02d} ~ {hi // 60:02d}:{hi % 60:02d}"
              f"{'   ★ 含夜盤' if (hi >= 15 * 60 or lo < 8 * 60) else '   （僅日盤）'}")


def probe_permission(tok: str) -> dict:
    print("=" * 72)
    print("【1+2+3】權限 / 解析度 / 夜盤   —— 取最近一個交易日")
    print("=" * 72)
    ok = {}
    for ds, did, label in [
        ("TaiwanStockKBar", STOCK_ID, f"台股分 K（{STOCK_ID}）"),
        ("TaiwanFuturesTick", FUTURES_ID, f"台指期逐筆（{FUTURES_ID}）"),
        ("TaiwanFuturesDaily", FUTURES_ID, "台指期日線（對照組，已知可用）"),
    ]:
        # 往回找最近一個有資料的日子，最多試 6 天
        got = None
        for back in range(1, 7):
            d = time.strftime("%Y-%m-%d",
                              time.localtime(time.time() - back * 86400))
            status, msg, rows = fetch(ds, did, d, tok)
            time.sleep(THROTTLE)
            if rows:
                got = (d, status, msg, rows)
                break
            if status != 200 or ("sponsor" in msg.lower() or "upgrade" in msg.lower()
                                 or "權限" in msg or "level" in msg.lower()):
                got = (d, status, msg, rows)
                break
        d, status, msg, rows = got or ("-", -1, "無回應", [])
        verdict = "✅ 可取" if rows else "❌ 取不到"
        ok[ds] = bool(rows)
        print(f"\n  {label}")
        print(f"     dataset={ds}  date={d}  HTTP={status}  msg={msg!r}")
        print(f"     {verdict}   筆數：{len(rows)}")
        if rows:
            print(f"     欄位：{sorted(rows[0].keys())}")
            print(f"     首列：{rows[0]}")
            if ds == "TaiwanStockKBar":
                report_resolution(rows)
            if ds == "TaiwanFuturesTick":
                contracts = sorted({str(r.get("contract_date", "?")) for r in rows})
                print(f"     契約月份（連續月拼接所需）：{contracts[:8]}"
                      f"{' ...' if len(contracts) > 8 else ''}")
        elif msg:
            print("     ↑ 若訊息提到 sponsor/upgrade/權限 → 卡在付費層級")
    return ok


def probe_history_start(tok: str, ok: dict) -> None:
    print("\n" + "=" * 72)
    print("【4】歷史起始日   —— 逐年探測")
    print("=" * 72)
    for ds, did in [("TaiwanStockKBar", STOCK_ID), ("TaiwanFuturesTick", FUTURES_ID)]:
        if not ok.get(ds):
            print(f"\n  {ds}：上一步取不到，跳過")
            continue
        print(f"\n  {ds}")
        for yr in PROBE_YEARS:
            best = 0
            for suffix in PROBE_DAYS:
                _, _, rows = fetch(ds, did, f"{yr}{suffix}", tok)
                time.sleep(THROTTLE)
                best = max(best, len(rows))
                if best:
                    break
            print(f"     {yr}: {'✅ 有資料' if best else '—  無'}"
                  f"{f'（{best} 筆）' if best else ''}")


def main() -> None:
    tok = token()
    ok = probe_permission(tok)
    probe_history_start(tok, ok)

    print("\n" + "=" * 72)
    print("【判讀】")
    print("=" * 72)
    kbar, tick = ok.get("TaiwanStockKBar"), ok.get("TaiwanFuturesTick")
    if kbar and tick:
        print("  台股與台指期盤中資料皆可取 → 5m 評估整條路走 FinMind，不需要 TradingView。")
    elif kbar and not tick:
        print("  台股可、台指期不可 → 先做台股 5m 評估；台指期另尋來源。")
    elif tick and not kbar:
        print("  台指期可、台股不可 → 反過來，先做台指期。")
    else:
        print("  兩者皆不可取 → 看上面的 msg：若是付費層級，比較「贊助 FinMind」")
        print("  與「TradingView 訂閱 + 交易所資料費」的成本；前者仍是唯一能進排程的路。")
    print("\n  把整段輸出貼回對話即可接著規劃 adapter 擴充。")


if __name__ == "__main__":
    main()
