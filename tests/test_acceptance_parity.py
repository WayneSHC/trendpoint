# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 004 US1 — 回測 ↔ 即時一致性（Parity），對應 spec 001 SC-003。

**前綴一致性（prefix consistency）**：對任一截斷點 i，
`build_indicator_frame(df.iloc[:i])` 的每一列必須與
`build_indicator_frame(df)` 的對應列逐位元相等（check_exact=True，零容差）。

這正是本系統「即時計算」的數學模型——監控端沒有帶狀態的逐根增量引擎，
其「即時」就是對整段歷史全量重算後取最後一根已收盤 bar；因此回測與即時的
唯一差異就是歷史前綴長度。前綴一致性同時是看前偏誤的結構性防禦：任何指標
若使用第 i 根之後的資料，全量計算的第 i 列就會與截斷於 i 的前綴末列不符。

**已知限制**：Wilder ATR 與階梯遞迴皆自序列「起點」播種，故不同起點的兩段
歷史（如監控端只抓 5 天）與全量回測的指標值本就不會逐位元相等——這是演算法
性質，非缺陷。本測試保證的是「同起點、增長端點」的一致性，與監控端實際
運作方式（每次對同一下載視窗全量重算）相符。
"""

import pandas as pd
import pytest

from acceptance_fixtures import make_klines
from ladder_system import build_indicator_frame

# 契約（contracts/indicator-frame.md）要求覆蓋的欄位
PARITY_COLUMNS = [
    "atr",
    "ladder",
    "mid_price",
    "upper_price",
    "lower_price",
    "mss_signal",
    "bos_signal",
    "chandelier_long",
    "chandelier_short",
    "vwap",
    "daily_open",
]

_PARAMS = dict(structure_period=10, include_regime=False)


def _truncation_points(n: int, cadence: int | None) -> list[int]:
    """取樣 ~40 個截斷點：warmup 邊界密、日界密、中段均勻、尾部密。"""
    pts: set[int] = set()
    # rolling 週期 warmup 邊界（10/14/20/22 前後）
    for b in (11, 12, 14, 15, 20, 22, 23, 25, 30):
        if b <= n:
            pts.add(b)
    # 日界（5 分線每 cadence 根換日；日線無此結構）
    if cadence:
        for m in range(cadence, n, cadence):
            pts.add(m)
            if m + 1 <= n:
                pts.add(m + 1)
    # 中段均勻網格（~20 點，日線亦有足夠取樣密度）
    step = max(1, n // 20)
    for m in range(step, n, step):
        pts.add(m)
    # 尾部密集
    for d in (1, 2, 3, 5, 10, 20, 50):
        if n - d > 25:
            pts.add(n - d)
    return sorted(p for p in pts if 2 <= p <= n)


@pytest.mark.parametrize(
    "n,freq,cadence",
    [
        (600, "5min", 54),  # ~11 個交易日的 5 分線
        (500, "1D", None),  # 500 根日線
    ],
)
def test_prefix_consistency(n, freq, cadence):
    """全量計算的第 i 列 == 截斷於 i 的前綴末列（逐欄零容差）。"""
    df = make_klines(n, freq=freq)
    full = build_indicator_frame(df, **_PARAMS)

    points = _truncation_points(n, cadence)
    assert len(points) >= 30, f"截斷點取樣過少：{len(points)}"

    for i in points:
        prefix = build_indicator_frame(df.iloc[:i], **_PARAMS)
        assert len(prefix) == i
        for col in PARITY_COLUMNS:
            pd.testing.assert_series_equal(
                prefix[col].reset_index(drop=True),
                full[col].iloc[:i].reset_index(drop=True),
                check_exact=True,
                check_names=False,
                obj=f"{col} @ truncation i={i} (freq={freq})",
            )


def test_incremental_replay_exhaustive():
    """
    窮舉式逐根重播：對每一根 bar j，`build(df[:j+1])` 的末列必須等於
    `build(df)` 的第 j 列。這是 parity 的最強形式，也是 SC-002 有效性的
    關鍵——前綴與全量的差異只會出現在前綴「末列」，故必須讓每一根 bar
    都輪流當末列，才能捕捉任何位置的未來資料洩漏（如把 .shift(1) 誤寫成
    .shift(-1)：全量的某根突破 bar 會偷看下一根，其值與該根當末列時的
    前綴不符）。取樣式測試會漏掉這種「僅末列差異」，故此處窮舉。
    """
    n = 250
    df = make_klines(n, freq="5min")
    full = build_indicator_frame(df, **_PARAMS)

    mismatches = []
    for j in range(1, n):  # j = 當前「最新已收盤」bar 的位置
        last_row = build_indicator_frame(df.iloc[: j + 1], **_PARAMS).iloc[-1]
        full_row = full.iloc[j]
        for col in PARITY_COLUMNS:
            a, b = last_row[col], full_row[col]
            if pd.isna(a) and pd.isna(b):
                continue
            if a != b:
                mismatches.append((j, col, a, b))

    assert not mismatches, (
        f"逐根重播發現 {len(mismatches)} 處前綴↔全量不一致，前 3 例："
        f"{mismatches[:3]}"
    )
