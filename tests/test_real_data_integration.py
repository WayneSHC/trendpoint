# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 010 — 消費端整合（SC-006 精修版 + SC-007）。全離線。

- 含負價之 back-adjust 連續序列通過放寬版品質契約（僅期貨連續層）。
- taifex 源之監控：取數走 DB 連續表 + fetch_latest（不呼叫重量 fetch()）、
  訊息無 MOCK 前綴；DB 未回填 → 警告略過不觸發回填。
- 回測引擎直接消費真實 fixture 產生之連續序列。
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import monitor_signals
from backtester import BacktestEngine
from config.config import FuturesCostConfig, FuturesDataSourceConfig
from data_ingestion import save_to_sqlite, validate_data_contract
from data_sources.rollover import (build_continuous, compute_roll_events,
                                   select_front_month)
from data_sources.taifex_source import TaifexAdapter
from db_security import table_name_for
from instruments import AssetClass, ContractSpec, Instrument
from trading_costs import FuturesCostModel, FuturesSizer

FIXTURE = Path(__file__).parent / "fixtures" / "taifex_sample_big5.csv"

TXF_REAL = Instrument(
    id="TXF", asset_class=AssetClass.FUTURES, source="taifex",
    display_name="臺股期貨",
    contract=ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0),
)


def _continuous_from_fixture() -> pd.DataFrame:
    ad = TaifexAdapter(cfg=FuturesDataSourceConfig(throttle_seconds=0.0),
                       session=object(), sleeper=lambda s: None)
    raw = ad._parse_csv(FIXTURE.read_bytes(), commodity="TX")
    front = select_front_month(raw)
    return build_continuous(raw, front, compute_roll_events(raw, front))


# ---------------------------------------------------------------------------
# SC-007：品質契約（負價放寬僅限期貨連續層）
# ---------------------------------------------------------------------------

def test_contract_allows_negative_backadjusted_prices():
    cont = _continuous_from_fixture()
    shifted = cont.copy()
    shifted[["open", "high", "low", "close"]] -= 20000.0   # 模擬深度 back-adjust 負價
    assert validate_data_contract(shifted, asset_class="futures",
                                  allow_nonpositive_prices=True)


def test_contract_still_rejects_nan_and_default_rejects_nonpositive():
    cont = _continuous_from_fixture()
    bad = cont.copy()
    bad.iloc[2, bad.columns.get_loc("close")] = np.nan
    with pytest.raises(ValueError):
        validate_data_contract(bad, asset_class="futures",
                               allow_nonpositive_prices=True)
    neg = cont.copy()
    neg[["open", "high", "low", "close"]] -= 20000.0
    with pytest.raises(ValueError):     # 預設（現貨/raw 語意）仍拒非正價
        validate_data_contract(neg, asset_class="futures")


# ---------------------------------------------------------------------------
# SC-006 精修版：監控取數走 DB + fetch_latest；無 MOCK 前綴；未回填略過
# ---------------------------------------------------------------------------

class SpyAlert:
    def __init__(self):
        self.sent = []

    def send_alert(self, msg):
        self.sent.append(msg)
        return True


class _LatestSpy:
    def __init__(self, frame):
        self.frame = frame
        self.calls = 0

    def fetch_latest(self, instrument):
        self.calls += 1
        return self.frame

    def fetch(self, *a, **kw):          # 重量呼叫——監控絕不可觸碰
        raise AssertionError("monitor 不得呼叫 taifex fetch()（全歷史回填）")


def _bear_frame(n=60):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = np.full(n, 100.0)
    close[-1] = 80.0
    open_ = np.full(n, 100.0)
    df = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + 0.5,
                       "low": np.minimum(open_, close) - 0.5, "close": close,
                       "volume": np.full(n, 1000.0)}, index=idx)
    return df


@pytest.fixture()
def monitor_env(tmp_path, monkeypatch):
    db = str(tmp_path / "monitor_test.db")
    monkeypatch.setattr(monitor_signals, "DB_PATH", db)
    monitor_signals.init_sent_alerts_db(db)
    return db


def test_taifex_monitor_reads_db_and_latest_no_mock_prefix(monitor_env, monkeypatch):
    db = monitor_env
    save_to_sqlite(_bear_frame(), table_name_for(TXF_REAL, "daily"), db)
    spy_adapter = _LatestSpy(pd.DataFrame())     # 尾日 < 今日 → 會打一次、空回應照常
    monkeypatch.setattr(monitor_signals, "get_adapter", lambda key: spy_adapter)
    spy = SpyAlert()

    monitor_signals.check_new_signals("TXF", spy, instrument=TXF_REAL)

    assert spy_adapter.calls == 1, "當日端點應恰被呼叫一次（1 請求/輪詢）"
    assert spy.sent, "空頭 BOS 應觸發推播"
    assert all("MOCK" not in m for m in spy.sent), "真源訊息不得帶 MOCK 前綴"
    assert any("空頭" in m for m in spy.sent)


def test_taifex_monitor_skips_when_db_empty(monitor_env, monkeypatch):
    spy_adapter = _LatestSpy(pd.DataFrame())
    monkeypatch.setattr(monitor_signals, "get_adapter", lambda key: spy_adapter)
    spy = SpyAlert()
    monitor_signals.check_new_signals("TXF", spy, instrument=TXF_REAL)   # DB 無表
    assert spy.sent == [] and spy_adapter.calls == 0, "未回填應略過且不觸發任何取數"


# ---------------------------------------------------------------------------
# 回測直接消費真實 fixture 連續序列
# ---------------------------------------------------------------------------

def test_backtest_consumes_real_continuous_series():
    cont = _continuous_from_fixture()
    cfg = FuturesCostConfig()
    res = BacktestEngine(initial_capital=10_000_000.0).run_backtest(
        cont, asset_class="futures",
        cost_model=FuturesCostModel(TXF_REAL.contract, cfg),
        sizer=FuturesSizer(TXF_REAL.contract, cfg),
        point_value=TXF_REAL.contract.point_value,
        verbose=False,
    )
    assert "summary" in res
    assert not res["equity_curve"]["equity"].isna().any()
