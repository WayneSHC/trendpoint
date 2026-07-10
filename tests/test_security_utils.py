# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 安全工具模組測試 (pytest)

涵蓋:
1. rate_limiter：限流節奏（超限時等待而非拋錯，保證批次抓取不中斷）
2. 登入鎖定：失敗計數、鎖定觸發、成功登入重置
"""

import time

from security_utils import (
    rate_limiter,
    is_locked,
    register_failed_attempt,
    reset_lockout,
)


# =========================================================================
# 1. Rate Limiter
# =========================================================================

def test_rate_limiter_allows_within_limit():
    calls = []

    @rate_limiter(calls=5, period=60)
    def fn(x):
        calls.append(x)
        return x

    for i in range(5):
        assert fn(i) == i
    assert len(calls) == 5


def test_rate_limiter_waits_instead_of_raising():
    """
    超過限制時必須等待窗口釋出後繼續執行，而非拋出例外。
    （run_ingestion 連抓多檔標的時，中途拋錯會讓整個批次崩潰）
    """
    @rate_limiter(calls=2, period=1)
    def fn():
        return time.time()

    t0 = time.time()
    fn()
    fn()
    # 第三次呼叫應被迫等待約 1 秒，且不得拋出例外
    fn()
    elapsed = time.time() - t0

    assert elapsed >= 0.9, f"第三次呼叫應等待窗口釋出，實際僅耗時 {elapsed:.2f} 秒"


# =========================================================================
# 2. 登入鎖定 (Login Lockout)
# =========================================================================

def test_lockout_triggers_after_max_attempts():
    state = {}
    assert not is_locked(state)

    # 連續失敗 5 次後觸發鎖定
    for _ in range(5):
        register_failed_attempt(state)

    assert is_locked(state), "達到最大失敗次數後必須進入鎖定狀態"


def test_lockout_not_triggered_below_threshold():
    state = {}
    for _ in range(4):
        register_failed_attempt(state)
    assert not is_locked(state)


def test_reset_lockout_clears_state():
    state = {}
    for _ in range(5):
        register_failed_attempt(state)
    assert is_locked(state)

    reset_lockout(state)
    assert not is_locked(state)
    assert state["failed_attempts"] == 0
