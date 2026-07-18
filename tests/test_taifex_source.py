# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 010 — TAIFEX 主源 adapter 測試（SC-003/008）。

離線：真實 Big5 fixture 解析（欄位/時段過濾/週契約排除/錨定值）、格式 fail-fast、
重試與節流語意（注入 fake session/sleeper）。
網路（@pytest.mark.network，CI 跳過）：小區間真實 e2e。
"""

import io
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from config.config import FuturesDataSourceConfig
from data_sources.taifex_source import TaifexAdapter
from instruments import AssetClass, ContractSpec, Instrument

FIXTURE = Path(__file__).parent / "fixtures" / "taifex_sample_big5.csv"

TXF = Instrument(
    id="TXF", asset_class=AssetClass.FUTURES, source="taifex",
    display_name="臺股期貨",
    contract=ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0),
)


class _FakeResp:
    def __init__(self, content=b"", status_code=200, json_data=None):
        self.content = content
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    """可編程回應序列：每次 post/get 取下一個回應（或拋例外）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def _next(self):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def post(self, *a, **kw):
        return self._next()

    def get(self, *a, **kw):
        return self._next()


def _adapter(responses=None, **cfg_kw):
    cfg = FuturesDataSourceConfig(throttle_seconds=0.0, **cfg_kw)
    sleeps = []
    ad = TaifexAdapter(cfg=cfg,
                       session=_FakeSession(responses or []),
                       sleeper=sleeps.append)
    return ad, sleeps


# ---------------------------------------------------------------------------
# 解析（離線真實 fixture）
# ---------------------------------------------------------------------------

def test_parse_fixture_anchor_values():
    raw_bytes = FIXTURE.read_bytes()
    ad, _ = _adapter()
    df = ad._parse_csv(raw_bytes, commodity="TX")
    assert set(df.columns) >= {"date", "contract", "open", "high", "low",
                               "close", "volume", "settlement", "open_interest"}
    # 錨定列：2023-07-03 × 202307（一般時段）
    row = df[(df["date"] == pd.Timestamp("2023-07-03")) & (df["contract"] == "202307")]
    assert len(row) == 1
    r = row.iloc[0]
    assert (r["open"], r["high"], r["low"], r["close"]) == (16901.0, 17038.0, 16897.0, 17035.0)
    assert r["volume"] == 75504.0 and r["settlement"] == 17035.0 and r["open_interest"] == 84331.0


def test_parse_filters_sessions_and_week_contracts():
    ad, _ = _adapter()
    df = ad._parse_csv(FIXTURE.read_bytes(), commodity="TX")
    # 週契約排除：contract 全為 6 碼數字
    assert df["contract"].str.fullmatch(r"\d{6}").all()
    # 一般時段唯一（盤後已過濾）：同（date×contract）唯一列
    assert not df.duplicated(subset=["date", "contract"]).any()


def test_parse_broken_header_failfast():
    ad, _ = _adapter()
    bad = "交易日期,契約,收盤價\n2023/07/03,TX,17035\n".encode("big5")
    with pytest.raises(ValueError):
        ad._parse_csv(bad, commodity="TX")


# ---------------------------------------------------------------------------
# 重試與節流（注入 fake session / sleeper）
# ---------------------------------------------------------------------------

def test_fetch_raw_retry_then_success():
    ok = _FakeResp(content=FIXTURE.read_bytes())
    ad, sleeps = _adapter(responses=[ConnectionError("boom"), ConnectionError("boom"), ok],
                          max_retries=3)
    df = ad.fetch_raw(TXF, "daily", date(2023, 7, 1), date(2023, 7, 31))
    assert not df.empty
    assert ad._session.calls == 3          # 2 失敗 + 1 成功


def test_fetch_raw_retries_exhausted_failfast():
    ad, _ = _adapter(responses=[ConnectionError("boom")] * 3, max_retries=2)
    with pytest.raises(RuntimeError):
        ad.fetch_raw(TXF, "daily", date(2023, 7, 1), date(2023, 7, 31))


def test_fetch_builds_continuous_series():
    """fetch() = fetch_raw → rollover → 連續序列（008a 契約）。"""
    ok = _FakeResp(content=FIXTURE.read_bytes())
    ad, _ = _adapter(responses=[ok], backfill_start="2023-07-01")
    ad._today = lambda: date(2023, 7, 31)   # 固定「今日」使單月單請求
    cont = ad.fetch(TXF, "daily")
    assert set(cont.columns) >= {"open", "high", "low", "close", "volume"}
    assert cont.index.is_monotonic_increasing and not cont["close"].isna().any()
    assert len(cont) >= 15                  # 2023-07 交易日數


def test_fetch_rejects_non_daily():
    ad, _ = _adapter()
    with pytest.raises(ValueError):
        ad.fetch(TXF, "5m")


# ---------------------------------------------------------------------------
# 真實網路 e2e（CI 預設跳過）
# ---------------------------------------------------------------------------

@pytest.mark.network
def test_network_small_range_e2e():
    ad = TaifexAdapter(cfg=FuturesDataSourceConfig(throttle_seconds=1.0))
    raw = ad.fetch_raw(TXF, "daily", date(2024, 3, 1), date(2024, 3, 31))
    assert not raw.empty and raw["contract"].str.fullmatch(r"\d{6}").all()
    latest = ad.fetch_latest(TXF)
    assert not latest.empty
