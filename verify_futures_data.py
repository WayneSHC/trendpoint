# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 期貨雙源交叉驗證器 (spec 010 US3)

對重疊區間逐（日期×契約）比對 TAIFEX（主源）與 FinMind（驗證哨兵）之原始列
（開高低收/結算價/成交量）：容差（config `verify_tolerance`，預設 0——同源鏡像
理應全等）內通過；超差列產出告警報表（stdout 摘要 + data/verify_futures_report.csv）。
驗證**不阻塞**匯入：FinMind 不可用（無 FINMIND_TOKEN/HTTP 錯）→ skipped 並如實
記錄、退出碼 0。

CLI: python verify_futures_data.py --start 2024-01-01 --end 2024-03-31
（`run_ingestion.py --verify` 呼叫同一函式，預設近 30 日）
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

_COMPARE_COLS = ["open", "high", "low", "close", "settlement", "volume"]


@dataclass
class VerifyReport:
    skipped: bool = False
    reason: str = ""
    total_rows: int = 0
    mismatches: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def passed(self) -> bool:
        return (not self.skipped) and self.mismatches.empty


def cross_verify(start: date, end: date, *, tolerance: float = None,
                 taifex=None, finmind=None, instrument=None) -> VerifyReport:
    """逐（date×contract×欄位）比對兩源 raw 列；|diff| > tolerance → 告警列。"""
    from config import load_config
    cfg = load_config()
    if tolerance is None:
        tolerance = cfg.data.futures_source.verify_tolerance
    if instrument is None:
        futs = [i for i in cfg.data.instruments
                if getattr(i.asset_class, "value", i.asset_class) == "futures"
                and i.source == "taifex"]
        if not futs:
            return VerifyReport(skipped=True, reason="config 無 taifex 期貨 instrument")
        instrument = futs[0]
    if taifex is None:
        from data_sources import get_adapter
        taifex = get_adapter("taifex")
    if finmind is None:
        from data_sources import get_adapter
        finmind = get_adapter("finmind")

    from data_sources.finmind_source import MissingTokenError
    try:
        fm = finmind.fetch_raw(instrument, "daily", start, end)
    except MissingTokenError as e:
        return VerifyReport(skipped=True, reason=str(e))
    except Exception as e:                              # noqa: BLE001（哨兵不可用不阻塞）
        return VerifyReport(skipped=True, reason=f"FinMind 取數失敗：{e}")

    tx = taifex.fetch_raw(instrument, "daily", start, end)
    if tx.empty or fm.empty:
        return VerifyReport(skipped=True,
                            reason=f"重疊區間無資料（taifex {len(tx)} 列 / finmind {len(fm)} 列）")

    merged = tx.merge(fm, on=["date", "contract"], suffixes=("_taifex", "_finmind"),
                      how="inner")
    records = []
    for col in _COMPARE_COLS:
        a, b = merged[f"{col}_taifex"], merged[f"{col}_finmind"]
        diff = (a - b).abs()
        bad = diff > tolerance
        for _, row in merged[bad].iterrows():
            records.append({
                "date": row["date"], "contract": row["contract"], "field": col,
                "taifex": row[f"{col}_taifex"], "finmind": row[f"{col}_finmind"],
                "diff": abs(row[f"{col}_taifex"] - row[f"{col}_finmind"]),
            })
    mismatches = pd.DataFrame(records)
    return VerifyReport(total_rows=len(merged), mismatches=mismatches)


def run_and_report(start: date, end: date) -> int:
    report = cross_verify(start, end)
    if report.skipped:
        print(f"[交叉驗證] 未執行：{report.reason}")
        return 0
    print(f"[交叉驗證] 比對 {report.total_rows} 列（{start} ~ {end}）")
    if report.passed:
        print("[交叉驗證] 全數通過（零告警）")
        return 0
    os.makedirs("data", exist_ok=True)
    out = "data/verify_futures_report.csv"
    report.mismatches.to_csv(out, index=False)
    print(f"[交叉驗證] ⚠ 超差 {len(report.mismatches)} 列——報表：{out}")
    print(report.mismatches.head(10).to_string(index=False))
    return 1


def main():
    parser = argparse.ArgumentParser(description="TrendPoint 期貨雙源交叉驗證")
    parser.add_argument("--start", type=date.fromisoformat,
                        default=date.today() - timedelta(days=30))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    sys.exit(run_and_report(args.start, args.end))


if __name__ == "__main__":
    main()
