# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 資產類別抽象與 Instrument Registry (spec 008a)。

引入 `Instrument` 值物件，取代裸 `ticker` 字串所隱含的「yfinance symbol → stock_*
表 → 現貨」單一假設。008a 只承載**資料相關**中繼資料（id/asset_class/source/
timeframes）；點值/合約/成本由 008b 擴充。純字串 ticker 向後相容解析為
equity/yfinance instrument。
"""

from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class AssetClass(str, Enum):
    EQUITY = "equity"
    FUTURES = "futures"


class Instrument(BaseModel):
    """單一交易標的的資料層描述（frozen 值物件）。"""

    model_config = {"frozen": True}

    id: str = Field(..., min_length=1, description="識別碼，如 '2330.TW'、'TXF'")
    asset_class: AssetClass = Field(default=AssetClass.EQUITY, description="資產類別")
    source: str = Field(default="yfinance", description="資料來源 adapter 鍵")
    display_name: str = Field(default="", description="顯示名，預設 = id")
    timeframes: List[str] = Field(default_factory=lambda: ["daily"], description="支援時框")

    @property
    def name(self) -> str:
        return self.display_name or self.id


def equity_instrument(ticker: str) -> Instrument:
    """純字串 ticker → equity/yfinance Instrument（向後相容，spec 008a SC-005）。
    timeframes 為 daily+5m，維持 008a 前 run_ingestion 對現貨同時抓日線與 5 分線的行為。"""
    return Instrument(id=ticker, asset_class=AssetClass.EQUITY, source="yfinance",
                      display_name=ticker, timeframes=["daily", "5m"])


class InstrumentRegistry:
    """由 config 宣告解析出的 instrument 集合；id 唯一（衝突 fail-fast）。"""

    def __init__(self, instruments: List[Instrument]):
        by_id = {}
        for inst in instruments:
            if inst.id in by_id:
                raise ValueError(f"Instrument id 衝突：'{inst.id}' 被重複宣告（tickers 與 instruments 不得撞名）")
            by_id[inst.id] = inst
        self._by_id = by_id

    @classmethod
    def from_config(cls, tickers: List[str], instruments: List[Instrument]) -> "InstrumentRegistry":
        """合併 config 的 `data.tickers`（→equity/yfinance）與 `data.instruments`（結構化）。"""
        merged = [equity_instrument(t) for t in tickers] + list(instruments)
        return cls(merged)

    def resolve(self, instrument_id: str) -> Instrument:
        if instrument_id not in self._by_id:
            raise KeyError(f"未知 instrument id：'{instrument_id}'")
        return self._by_id[instrument_id]

    def all(self) -> List[Instrument]:
        return list(self._by_id.values())
