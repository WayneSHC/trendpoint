import time
import threading
from functools import wraps
from typing import Callable, Any

# -----------------------------------------------------------------------------
# Rate Limiter (Token Bucket) – 防止過度呼叫外部 API（例如 yfinance）
# -----------------------------------------------------------------------------
# 使用方式：
# @rate_limiter(calls=5, period=60)
# def fetch_stock_data(...):
#     ...
# 會在同一執行緒內保持呼叫次數限制；超過限制時「等待」至窗口釋出再執行，
# 而非拋出例外——避免批次抓取流程（如 run_ingestion 連抓多檔標的）中途崩潰。

def rate_limiter(calls: int, period: int) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Return a decorator that limits a function to *calls* executions per *period* seconds.

    - *calls*: 最大呼叫次數。
    - *period*: 時間窗口（秒）。
    - 實作為簡易的令牌桶（token bucket）。
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        # 使用 list 以保證閉包內可變（存放呼叫時間戳）
        timestamps: list[float] = []
        lock = threading.Lock()

        @wraps(func)
        def wrapper(*args, **kwargs):
            while True:
                with lock:
                    now = time.time()
                    # 移除已過期的時間戳
                    while timestamps and now - timestamps[0] > period:
                        timestamps.pop(0)
                    if len(timestamps) < calls:
                        timestamps.append(now)
                        break
                    # 已達上限：計算最早時間戳過期所需的等待時間
                    wait_time = period - (now - timestamps[0]) + 0.01
                print(f"[rate_limiter] {func.__name__} 達到 {calls} 次/{period} 秒上限，等待 {wait_time:.1f} 秒...")
                time.sleep(wait_time)
            return func(*args, **kwargs)

        return wrapper

    return decorator

# -----------------------------------------------------------------------------
# Login Lockout – 防止暴力破解密碼
# -----------------------------------------------------------------------------
# 在 Streamlit UI 中使用：
#   if not check_password():
#       st.stop()
# 內部會自動追蹤失敗次數與鎖定時間。

_MAX_ATTEMPTS = 5               # 允許的最大失敗次數
_LOCKOUT_SECONDS = 300           # 鎖定時長（5 分鐘）

def _init_lockout_state(state: dict) -> None:
    """確保 SessionState 中存在必要欄位。"""
    if "failed_attempts" not in state:
        state["failed_attempts"] = 0
    if "lockout_until" not in state:
        state["lockout_until"] = 0.0

def is_locked(state: dict) -> bool:
    """判斷目前是否處於鎖定狀態。"""
    _init_lockout_state(state)
    return time.time() < state["lockout_until"]

def register_failed_attempt(state: dict) -> None:
    """在密碼驗證失敗時呼叫，更新失敗計數與可能的鎖定時間。"""
    _init_lockout_state(state)
    state["failed_attempts"] += 1
    if state["failed_attempts"] >= _MAX_ATTEMPTS:
        state["lockout_until"] = time.time() + _LOCKOUT_SECONDS
        state["failed_attempts"] = 0

def reset_lockout(state: dict) -> None:
    """在成功登入後重置計數與鎖定時間。"""
    state["failed_attempts"] = 0
    state["lockout_until"] = 0.0

__all__ = [
    "rate_limiter",
    "is_locked",
    "register_failed_attempt",
    "reset_lockout",
]
