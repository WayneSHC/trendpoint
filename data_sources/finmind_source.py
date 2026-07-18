# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - FinMind 驗證源 adapter (spec 010)

角色：交叉驗證**哨兵**（TAIFEX 為準）——同源清洗鏡像，用於捕捉任一側的
解析/傳輸錯誤。REST 直打（免 SDK，D4）；token 走環境變數 `FINMIND_TOKEN`
（安全鐵律：憑證不入 config）；缺失 → MissingTokenError（驗證器捕捉後跳過，
不阻塞匯入）。
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
import requests

from data_sources import register_adapter
from data_sources.base import DataSourceAdapter
from data_sources.rollover import (
    build_continuous,
    compute_roll_events,
    select_front_month,
)

_URL = "https://api.finmindtrade.com/api/v4/data"
_COMMODITY_MAP = {"TXF": "TX"}


class MissingTokenError(RuntimeError):
    """FINMIND_TOKEN 未設定——驗證跳過（哨兵不可用不阻塞主流程）。"""


class FinMindAdapter(DataSourceAdapter):
    source_key = "finmind"

    def __init__(self, session=None):
        self._session = session or requests

    @staticmethod
    def _token() -> str:
        token = os.environ.get("FINMIND_TOKEN", "").strip()
        if not token:
            raise MissingTokenError(
                "FINMIND_TOKEN 環境變數未設定——FinMind 驗證源不可用（跳過交叉驗證）"
            )
        return token

    @staticmethod
    def _commodity(instrument) -> str:
        return _COMMODITY_MAP.get(instrument.id, instrument.id)

    @staticmethod
    def _parse(records: list) -> pd.DataFrame:
        """FinMind JSON 列 → raw schema（同 TAIFEX：date/contract/ohlc/volume/
        settlement/open_interest）；僅一般時段（trading_session=='position' 或空）、
        僅月契約（6 碼數字）。"""
        rows = []
        for rec in records:
            session = str(rec.get("trading_session", "") or "")
            if session not in ("", "position"):
                continue
            contract = str(rec.get("contract_date", "")).strip()
            if not (len(contract) == 6 and contract.isdigit()):
                continue
            try:
                rows.append({
                    "date": pd.Timestamp(rec["date"]),
                    "contract": contract,
                    "open": float(rec["open"]),
                    "high": float(rec["max"]),
                    "low": float(rec["min"]),
                    "close": float(rec["close"]),
                    "volume": float(rec["volume"]),
                    "settlement": float(rec["settlement_price"]),
                    "open_interest": float(rec["open_interest"]),
                })
            except (KeyError, TypeError, ValueError) as e:
                raise ValueError(f"FinMind 列解析失敗：{rec}") from e
        if not rows:
            return pd.DataFrame(columns=["date", "contract", "open", "high", "low",
                                         "close", "volume", "settlement", "open_interest"])
        return (pd.DataFrame(rows)
                .drop_duplicates(subset=["date", "contract"], keep="last")
                .sort_values(["date", "contract"]).reset_index(drop=True))

    def fetch_raw(self, instrument, timeframe: str, start: date, end: date) -> pd.DataFrame:
        if timeframe != "daily":
            raise ValueError(f"FinMind adapter 僅支援 daily（收到 {timeframe!r}）")
        token = self._token()
        # 安全：token 走 Authorization header——放 URL 查詢參數會隨 HTTPError
        # 訊息（含完整 URL）洩入日誌；錯誤訊息再洗一層以防萬一。
        resp = self._session.get(_URL, params={
            "dataset": "TaiwanFuturesDaily",
            "data_id": self._commodity(instrument),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(
                f"FinMind HTTP 錯誤：{str(e).replace(token, '***')}") from None
        payload = resp.json()
        if payload.get("status") not in (200, None) and payload.get("msg") != "success":
            raise RuntimeError(f"FinMind 回應異常：{payload.get('msg')}")
        return self._parse(payload.get("data", []))

    def fetch(self, instrument, timeframe: str) -> pd.DataFrame:
        raw = self.fetch_raw(instrument, timeframe, date(1998, 7, 21), date.today())
        if raw.empty:
            raise ValueError(f"FinMind 回傳空資料（{instrument.id}）")
        front = select_front_month(raw)
        return build_continuous(raw, front, compute_roll_events(raw, front))


register_adapter(FinMindAdapter())
