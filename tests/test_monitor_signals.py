# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
monitor_signals 之 TXF 當日補值（spec 010 analyze H1 路徑）迴歸測試。全離線。

補值的前提是「取回的 bar 比庫內最後一根更新」，而非「今天比庫內最後一根晚」。
非交易日（週末/假日）兩者分歧：時鐘前進、當日端點回的仍是前一交易日那根，
若以時鐘為準就會把同一根 append 第二次——階梯系統的 rolling 結構會把同一根
算兩次，等同窗口位移一格，可能翻轉訊號判定。

三種日期關係各鎖一條：== 不得重複、> 正常補、< 不得亂序。
"""

import numpy as np
import pandas as pd
import pytest

import ladder_system
import monitor_signals
from data_ingestion import save_to_sqlite
from db_security import table_name_for
from instruments import AssetClass, ContractSpec, Instrument

TXF_REAL = Instrument(
    id="TXF", asset_class=AssetClass.FUTURES, source="taifex",
    display_name="臺股期貨",
    contract=ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0),
)


class SpyAlert:
    def __init__(self):
        self.sent = []

    def send_alert(self, msg):
        self.sent.append(msg)
        return True


class _LatestStub:
    """僅提供當日端點；重量 fetch() 一經呼叫即失敗（監控不得觸發全歷史回填）。"""

    def __init__(self, frame):
        self.frame = frame
        self.calls = 0

    def fetch_latest(self, instrument):
        self.calls += 1
        return self.frame

    def fetch(self, *a, **kw):
        raise AssertionError("monitor 不得呼叫 taifex fetch()（全歷史回填）")


def _db_frame(n=60):
    """庫內連續表：末根 2024-02-29，close 收在可辨識的 100.0。"""
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = np.full(n, 100.0)
    open_ = np.full(n, 100.0)
    return pd.DataFrame({"open": open_, "high": close + 0.5, "low": close - 0.5,
                         "close": close, "volume": np.full(n, 1000.0)}, index=idx)


def _latest_raw(day: pd.Timestamp, close: float) -> pd.DataFrame:
    """當日端點原始列（多個月契約，量最大者為近月）——與 taifex parser 輸出同構。"""
    return pd.DataFrame([
        {"date": day, "contract": "202404", "open": close - 1, "high": close + 1,
         "low": close - 2, "close": close - 0.5, "volume": 500.0},
        {"date": day, "contract": "202403", "open": close - 2, "high": close + 2,
         "low": close - 3, "close": close, "volume": 90000.0},   # 量最大 → 近月
    ])


@pytest.fixture()
def monitor_env(tmp_path, monkeypatch):
    db = str(tmp_path / "monitor_dup.db")
    monkeypatch.setattr(monitor_signals, "DB_PATH", db)
    monitor_signals.init_sent_alerts_db(db)
    save_to_sqlite(_db_frame(), table_name_for(TXF_REAL, "daily"), db)
    return db


@pytest.fixture()
def captured_frame(monkeypatch):
    """攔截送進指標組裝的 frame——訊號正確性的前提是這份輸入乾淨。"""
    box = {}
    real = ladder_system.build_indicator_frame

    def spy(df, *a, **kw):
        box["df"] = df.copy()
        return real(df, *a, **kw)

    monkeypatch.setattr(ladder_system, "build_indicator_frame", spy)
    return box


def _run(monkeypatch, latest_frame):
    stub = _LatestStub(latest_frame)
    monkeypatch.setattr(monitor_signals, "get_adapter", lambda key: stub)
    monitor_signals.check_new_signals("TXF", SpyAlert(), instrument=TXF_REAL)
    return stub


def test_same_date_as_db_tail_is_not_appended(monitor_env, captured_frame, monkeypatch):
    """非交易日：端點回的日期 == 庫內末根 → 不得產生重複索引。"""
    _run(monkeypatch, _latest_raw(pd.Timestamp("2024-02-29"), close=80.0))

    df = captured_frame["df"]
    dups = df.index[df.index.duplicated()]
    assert dups.empty, f"重複索引：{list(dups)}"
    assert len(df) == 60, f"不應補值，實得 {len(df)} 列"
    # 庫內末根（rollover 引擎產出的正式連續值）不得被端點的單月近似值覆蓋
    assert df.loc[pd.Timestamp("2024-02-29"), "close"] == 100.0


def test_newer_date_is_appended(monitor_env, captured_frame, monkeypatch):
    """交易日：端點回的日期 > 庫內末根 → 照常補值。"""
    _run(monkeypatch, _latest_raw(pd.Timestamp("2024-03-01"), close=80.0))

    df = captured_frame["df"]
    assert len(df) == 61, f"應補上一根，實得 {len(df)} 列"
    assert df.index[-1] == pd.Timestamp("2024-03-01")
    assert df.iloc[-1]["close"] == 80.0, "應取當日量最大之月契約列"
    assert df.index.is_unique and df.index.is_monotonic_increasing


def test_stale_date_is_not_appended(monitor_env, captured_frame, monkeypatch):
    """端點落後庫內（回補後/端點延遲）→ 不得插入亂序列。"""
    _run(monkeypatch, _latest_raw(pd.Timestamp("2024-02-20"), close=80.0))

    df = captured_frame["df"]
    assert len(df) == 60, f"不應補值，實得 {len(df)} 列"
    assert df.index.is_monotonic_increasing, "索引亂序：rolling 結構計算將失真"


def test_empty_endpoint_response_is_tolerated(monitor_env, captured_frame, monkeypatch):
    """端點空回應 → 以既有入庫資料判定，仍恰打一次。"""
    stub = _run(monkeypatch, pd.DataFrame())

    assert stub.calls == 1, "當日端點應恰被呼叫一次（1 請求/輪詢）"
    assert len(captured_frame["df"]) == 60
