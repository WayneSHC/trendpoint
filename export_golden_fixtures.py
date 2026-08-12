# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""匯出黃金案例，供 TradingAgent 的差分 oracle 對照。

**為什麼是匯出而不是 import。**
TradingAgent 把本專案的向量化公式重寫成事件驅動的增量算法。憲章要求以本
專案的既有輸出當黃金案例做數值比對，但把 trendpoint 拉進那邊的測試相依，
會讓它的 CI 綁死在這個 repo 上。匯出成 JSON fixture 是兩邊都不綁的作法。

**這個檔案不進任何訊號或回測路徑**，只讀 DB、算公式、寫 JSON。

用法：

    python export_golden_fixtures.py --out /path/to/tradingagent/tests/fixtures/golden

匯出內容與各自的可比性：

* ``three_gate``——公式兩邊完全相同（本專案寫死 1.382，對方參數化且預設
  同值），**應逐點相符**。注意回傳順序相反：本專案為 (upper, mid, lower)，
  對方為 (lower, mid, upper)。
* ``wilder_atr``——兩邊都是 Wilder 平滑、都以前 period 根的 SMA 起算，
  **但輸入不同**：本專案吃完整 True Range（含跳空），對方吃日振幅
  （high−low），且對方原始碼明文標註那是刻意簡化。故此處匯出的是
  **餵入日振幅序列**的結果，隔離出「平滑配方」這個唯一被重寫的部分；
  另外附上餵入真實 TR 的結果作為對照，供人閱讀，不供斷言。
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from typing import List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ladder_system import (  # noqa: E402
    calculate_atr,
    calculate_three_bands,
    calculate_tr,
)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trendpoint.db")

#: 匯出的來源。刻意選最長的兩張表——黃金案例要涵蓋足夠多的價格量級，
#: 只取近期資料會讓浮點誤差在低價位下被低估。
SOURCES = [
    ("fut_TXF_daily", "TXF"),
    ("stock_2330_TW_daily", "2330"),
]

#: 每張表取的列數。全歷史（近 7000 根）會讓 fixture 檔膨脹到數 MB 且對
#: 驗證力毫無增益——Wilder 平滑是收斂的，誤差不會隨長度發散。
SAMPLE_ROWS = 400

ATR_PERIODS = [5, 14, 22]


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=5, check=False,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _load(table: str) -> pd.DataFrame:
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        df = pd.read_sql_query(
            f'SELECT datetime AS date, high, low, close FROM "{table}" ORDER BY datetime', conn
        )
    if df.empty:
        raise SystemExit(f"{table} 沒有資料")
    return df.tail(SAMPLE_ROWS).reset_index(drop=True)


def _three_gate_cases(df: pd.DataFrame) -> List[dict]:
    """逐日以「前一日高低」算三關價。

    以前一日為輸入而非當日，是因為對方的 SessionLevelService 就是這樣用的
    ——黃金案例要對齊的是實際用法，不只是函式簽名。
    """
    cases = []
    for i in range(1, len(df)):
        yh, yl = float(df.high[i - 1]), float(df.low[i - 1])
        if yh < yl:
            continue
        upper, mid, lower = calculate_three_bands(yh, yl)
        cases.append({
            "date": str(df.date[i]),
            "prior_high": repr(yh),
            "prior_low": repr(yl),
            "upper": repr(upper),
            "mid": repr(mid),
            "lower": repr(lower),
        })
    return cases


def _atr_cases(df: pd.DataFrame) -> dict:
    spans = (df.high - df.low).astype(float)
    tr = calculate_tr(df.high.astype(float), df.low.astype(float),
                              df.close.astype(float))

    out = {"spans": [repr(float(v)) for v in spans], "by_period": {}}
    for period in ATR_PERIODS:
        on_spans = calculate_atr(spans, period)
        on_tr = calculate_atr(tr, period)
        out["by_period"][str(period)] = {
            # 可供斷言：與對方的輸入語意一致（日振幅）
            "atr_on_spans_final": repr(float(on_spans.iloc[-1])),
            "atr_on_spans_series": [
                None if np.isnan(v) else repr(float(v)) for v in on_spans
            ],
            # 僅供閱讀：本專案生產路徑用的是完整 TR，兩者本就不同
            "atr_on_true_range_final_reference_only": repr(float(on_tr.iloc[-1])),
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="fixture 輸出目錄")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        raise SystemExit(f"找不到 {DB_PATH}——先跑 run_ingestion.py")

    os.makedirs(args.out, exist_ok=True)
    commit = _git_commit()

    for table, label in SOURCES:
        df = _load(table)
        payload = {
            "_provenance": {
                "generated_by": "trendpoint/export_golden_fixtures.py",
                "trendpoint_commit": commit,
                "source_table": table,
                "rows": len(df),
                "date_from": str(df.date.iloc[0]),
                "date_to": str(df.date.iloc[-1]),
                "three_gate_k": "1.382",
                "note": (
                    "數值以 repr(float) 保存，float64 往返無損；"
                    "atr_on_true_range_* 僅供閱讀，與對方的日振幅定義本就不同"
                ),
            },
            "three_gate": _three_gate_cases(df),
            "wilder_atr": _atr_cases(df),
        }
        path = os.path.join(args.out, f"trendpoint-{label}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print(f"寫出 {path}：three_gate {len(payload['three_gate'])} 例、"
              f"ATR periods {ATR_PERIODS}（{df.date.iloc[0]} ~ {df.date.iloc[-1]}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
