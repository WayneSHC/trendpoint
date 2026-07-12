# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 004 US2 — 延遲預算（Latency Budget），對應 spec 001 SC-004。

量測「新一根 K 線到達 → 指標更新 + 訊號判斷」整段路徑的耗時，中位數須 < 100ms。
本系統無帶狀態的逐根增量引擎，故「新 bar 到達」的真實成本就是對整段視窗
全量重算（build_indicator_frame）後取最後一根已收盤 bar 判定訊號——因此量測
全量重算即量測真實延遲。

以中位數（非平均）抵抗 CI 排程抖動；計時前先做一次 warmup 呼叫排除 numba
jit 首次編譯成本。標記 performance，可用 `-m "not performance"` 排除。
"""

import statistics
import time

import pandas as pd
import pytest

from acceptance_fixtures import make_klines
from ladder_system import build_indicator_frame
from monitor_signals import select_closed_bar_indices

LATENCY_BUDGET_S = 0.1  # 100ms（OpenSpec §6 / spec 001 SC-004）
_PARAMS = dict(structure_period=10, include_regime=False)


def _new_bar_cycle(df: pd.DataFrame) -> int:
    """模擬監控端單次循環：全量重算 + 取最後已收盤 bar 判定訊號。回傳訊號碼。"""
    frame = build_indicator_frame(df, **_PARAMS)
    bar_interval = pd.Timedelta(minutes=5)
    # 以末根時間 + 間隔為「現在」，讓末根視為已收盤（複刻 monitor 的取樣邏輯）
    now = frame.index[-1] + bar_interval
    latest_idx, _ = select_closed_bar_indices(frame.index, now, bar_interval)
    latest = frame.iloc[latest_idx]
    return int(latest["mss_signal"]) + int(latest["bos_signal"])


@pytest.mark.performance
@pytest.mark.parametrize(
    "n,label",
    [
        (270, "監控實況（5 日 × 5 分 K）"),
        (10_000, "壓力情境（OpenSpec 上限）"),
    ],
)
def test_new_bar_latency_within_budget(n, label):
    df = make_klines(n, freq="5min")

    _new_bar_cycle(df)  # warmup：排除 numba jit 首次編譯

    samples = []
    for _ in range(21):
        start = time.perf_counter()
        _new_bar_cycle(df)
        samples.append(time.perf_counter() - start)

    median_s = statistics.median(samples)
    # 診斷輸出（pytest -s 或失敗時可見）
    print(f"\n[{label}] n={n} 中位數={median_s * 1000:.2f}ms "
          f"最大={max(samples) * 1000:.2f}ms 預算={LATENCY_BUDGET_S * 1000:.0f}ms")
    assert median_s < LATENCY_BUDGET_S, (
        f"{label}：新 bar 循環中位數 {median_s * 1000:.1f}ms 超過 "
        f"{LATENCY_BUDGET_S * 1000:.0f}ms 預算"
    )
