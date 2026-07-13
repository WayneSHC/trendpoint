# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 資料來源 adapter 註冊與分派 (spec 008a)。

`get_adapter(source_key)` 依鍵取 adapter；未知鍵 fail-fast。內建 adapter
（yfinance/mock/csv）於首次分派時延後匯入並自我註冊（避免循環匯入）。
"""

from typing import Dict

from .base import DataSourceAdapter

_REGISTRY: Dict[str, DataSourceAdapter] = {}
_BOOTSTRAPPED = False


def register_adapter(adapter: DataSourceAdapter) -> None:
    """由各 adapter 模組於匯入時呼叫，登記自身。"""
    if not adapter.source_key:
        raise ValueError(f"adapter {type(adapter).__name__} 未設定 source_key")
    _REGISTRY[adapter.source_key] = adapter


def _bootstrap() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True
    for mod in ("yfinance_source", "mock_source", "csv_source"):
        try:
            __import__(f"data_sources.{mod}")
        except ImportError:
            # 尚未實作的 adapter 於開發期可缺席；get_adapter 會對未知鍵 fail-fast
            pass


def get_adapter(source_key: str) -> DataSourceAdapter:
    _bootstrap()
    if source_key not in _REGISTRY:
        raise ValueError(
            f"未知資料來源 adapter：'{source_key}'（已註冊：{sorted(_REGISTRY)}）"
        )
    return _REGISTRY[source_key]
