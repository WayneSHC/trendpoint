# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - TAIFEX 官方主源 adapter (spec 010)

歷史回填：`POST /cht/3/futDataDown`（單次限一個月、Big5 CSV、無驗證碼；
未公告限流 → 保守節流）。每日增量：OpenAPI `/v1/DailyMarketReportFut`（僅當日）。
`fetch()` 維持 008a 契約——回傳**已拼接連續序列**（rollover 引擎，spec 010）；
⚠ 重量呼叫（全區間網路），僅供 ingestion 與 network 測試；監控取數走
DB 連續表 + `fetch_latest`（spec 010 analyze H1）。

費率/節流/重試參數自 config `data.futures_source`（憲章 V）；本模組不寫 DB
（儲存屬 ingestion 職責）。
"""

from __future__ import annotations

import csv
import io
import time
from datetime import date, timedelta

import pandas as pd
import requests

from data_sources import register_adapter
from data_sources.base import DataSourceAdapter
from data_sources.rollover import (
    build_continuous,
    compute_roll_events,
    select_front_month,
)

_DOWN_URL = "https://www.taifex.com.tw/cht/3/futDataDown"
_OPENAPI_URL = "https://openapi.taifex.com.tw/v1/DailyMarketReportFut"
_UA = {"User-Agent": "Mozilla/5.0 (TrendPoint research; contact: local)"}

# instrument id → TAIFEX commodity_id（查詢代碼）
_COMMODITY_MAP = {"TXF": "TX"}

# Big5 CSV 欄位 → raw schema（缺一即 fail-fast）
_COL_MAP = {
    "交易日期": "date",
    "到期月份(週別)": "contract",
    "開盤價": "open",
    "最高價": "high",
    "最低價": "low",
    "收盤價": "close",
    "成交量": "volume",
    "結算價": "settlement",
    "未沖銷契約數": "open_interest",
}
_SESSION_COL = "交易時段"
_CONTRACT_ID_COL = "契約"


class TaifexAdapter(DataSourceAdapter):
    """TAIFEX 官方資料源（主源，權威）。"""

    source_key = "taifex"

    def __init__(self, cfg=None, session=None, sleeper=None):
        if cfg is None:
            from config import load_config
            cfg = load_config().data.futures_source
        self._cfg = cfg
        self._session = session or requests
        self._sleep = sleeper if sleeper is not None else time.sleep
        self._today = date.today   # 可注入（測試固定今日）

    # ------------------------------------------------------------------ 解析

    def _parse_csv(self, content: bytes, *, commodity: str) -> pd.DataFrame:
        """Big5 CSV → raw DataFrame（過濾一般時段、僅月契約、數值化）。"""
        text = content.decode("big5", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        header = reader.fieldnames or []
        header = [h.strip() for h in header]
        missing = (set(_COL_MAP) | {_CONTRACT_ID_COL}) - set(header)
        if missing:
            raise ValueError(
                f"TAIFEX CSV 格式異常：缺少欄位 {sorted(missing)}；實際欄位 {header}"
            )

        rows = []
        for rec in reader:
            # 尾隨逗號使 DictReader 產生 None 鍵（值為 list）——一律略過
            rec = {k.strip(): (v or "").strip()
                   for k, v in rec.items()
                   if k is not None and not isinstance(v, list)}
            if rec.get(_CONTRACT_ID_COL) != commodity:
                continue
            session_val = rec.get(_SESSION_COL, "")
            if session_val not in ("", "一般"):        # 盤後列排除（2017-05 起）
                continue
            contract = rec.get("到期月份(週別)", "")
            if not (len(contract) == 6 and contract.isdigit()):   # 週契約排除
                continue
            try:
                row = {
                    "date": pd.Timestamp(rec["交易日期"].replace("/", "-")),
                    "contract": contract,
                }
                numeric_ok = True
                for src, dst in _COL_MAP.items():
                    if dst in ("date", "contract"):
                        continue
                    val = rec[src].replace(",", "")
                    if val in ("", "-"):
                        numeric_ok = False          # 無成交列（開高低收缺值）→ 略過
                        break
                    row[dst] = float(val)
                if not numeric_ok:
                    continue
            except (KeyError, ValueError) as e:
                raise ValueError(f"TAIFEX CSV 列解析失敗：{rec}") from e
            rows.append(row)

        if not rows:
            return pd.DataFrame(columns=["date", "contract", *(
                c for c in _COL_MAP.values() if c not in ("date", "contract"))])
        df = pd.DataFrame(rows)
        df = df.drop_duplicates(subset=["date", "contract"], keep="last")
        return df.sort_values(["date", "contract"]).reset_index(drop=True)

    # ------------------------------------------------------------ 取數（網路）

    def _request_month(self, commodity: str, start: date, end: date) -> bytes:
        """單月查詢（含重試；用罄 fail-fast）。"""
        payload = {
            "down_type": "1",
            "commodity_id": commodity,
            "queryStartDate": start.strftime("%Y/%m/%d"),
            "queryEndDate": end.strftime("%Y/%m/%d"),
        }
        last_err: Exception | None = None
        for attempt in range(self._cfg.max_retries + 1):
            try:
                resp = self._session.post(_DOWN_URL, data=payload,
                                          headers=_UA, timeout=30)
                resp.raise_for_status()
                return resp.content
            except Exception as e:                    # noqa: BLE001（重試邊界）
                last_err = e
                if attempt < self._cfg.max_retries:
                    self._sleep(self._cfg.throttle_seconds)
        raise RuntimeError(
            f"TAIFEX 下載失敗（{start}~{end}，重試 {self._cfg.max_retries} 次用罄）: {last_err}"
        ) from last_err

    def _commodity(self, instrument) -> str:
        return _COMMODITY_MAP.get(instrument.id, instrument.id)

    def fetch_raw(self, instrument, timeframe: str, start: date, end: date) -> pd.DataFrame:
        """逐月抓取原始按契約列（[start, end]，含端點；每請求間節流）。"""
        if timeframe != "daily":
            raise ValueError(f"TAIFEX adapter 僅支援 daily（收到 {timeframe!r}）")
        commodity = self._commodity(instrument)
        frames = []
        cur = start.replace(day=1)
        first = True
        while cur <= end:
            nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
            m_start = max(cur, start)
            m_end = min(nxt - timedelta(days=1), end)
            if not first:
                self._sleep(self._cfg.throttle_seconds)
            first = False
            content = self._request_month(commodity, m_start, m_end)
            frames.append(self._parse_csv(content, commodity=commodity))
            cur = nxt
        raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if raw.empty:
            return raw
        raw = raw[(raw["date"] >= pd.Timestamp(start)) & (raw["date"] <= pd.Timestamp(end))]
        return (raw.drop_duplicates(subset=["date", "contract"], keep="last")
                   .sort_values(["date", "contract"]).reset_index(drop=True))

    def fetch_latest(self, instrument) -> pd.DataFrame:
        """OpenAPI 當日列（JSON，中文欄位同 CSV header）。"""
        commodity = self._commodity(instrument)
        resp = self._session.get(_OPENAPI_URL, headers=_UA, timeout=30)
        resp.raise_for_status()
        recs = resp.json()
        if not isinstance(recs, list):
            raise ValueError(f"TAIFEX OpenAPI 回應非列表：{type(recs)}")
        # 轉為 CSV 等價文字走同一解析器（欄位名一致、單一真實解析路徑）
        if not recs:
            return pd.DataFrame()
        fieldnames = list(recs[0].keys())
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fieldnames)
        w.writeheader()
        for r in recs:
            w.writerow(r)
        return self._parse_csv(buf.getvalue().encode("big5", errors="replace"),
                               commodity=commodity)

    # ---------------------------------------------------------------- fetch

    def fetch(self, instrument, timeframe: str) -> pd.DataFrame:
        """008a 契約：回傳已拼接連續序列。⚠ 重量（全區間網路）——僅 ingestion/測試。"""
        if timeframe != "daily":
            raise ValueError(f"TAIFEX adapter 僅支援 daily（收到 {timeframe!r}）")
        start = date.fromisoformat(self._cfg.backfill_start)
        raw = self.fetch_raw(instrument, timeframe, start, self._today())
        if raw.empty:
            raise ValueError(f"TAIFEX 回傳空資料（{instrument.id}，{start} 起）")
        front = select_front_month(raw)
        events = compute_roll_events(raw, front)
        return build_continuous(raw, front, events)


register_adapter(TaifexAdapter())
