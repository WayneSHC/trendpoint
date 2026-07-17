# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 003 — 監控推播空方能力 dry-run（SC-008）。

mock 期貨空方訊號 → 檢測 → 訊息含方向與【MOCK 資料—dry-run】前綴 → 通知端送達；
去重行為不變。以 stub adapter 產生確定性的空頭 BOS 末根訊號（真源接上即生效，
本測試不依賴網路）。
"""

import numpy as np
import pandas as pd
import pytest

import monitor_signals
from instruments import AssetClass, ContractSpec, Instrument

TXF = Instrument(
    id="TXF", asset_class=AssetClass.FUTURES, source="mock",
    display_name="台指期近月（mock）",
    contract=ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0),
)


class SpyAlertManager:
    def __init__(self):
        self.sent = []

    def send_alert(self, msg: str) -> bool:
        self.sent.append(msg)
        return True


def _bear_bos_frame(n=60):
    """尾根確定性空頭 BOS：平盤後末根重挫跌破前低（日線、已收盤之過去日期）。"""
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = np.full(n, 100.0)
    close[-1] = 80.0                      # 大幅跌破 rolling low → bear BOS
    open_ = np.full(n, 100.0)
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    vol = np.full(n, 1000.0)
    vol[-1] = 9000.0
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


class StubAdapter:
    source_key = "mock"

    def fetch(self, instrument, timeframe):
        return _bear_bos_frame()


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db = str(tmp_path / "alerts_test.db")
    monkeypatch.setattr(monitor_signals, "DB_PATH", db)
    monitor_signals.init_sent_alerts_db(db)
    return db


def test_short_signal_dry_run_full_chain(isolated_db, monkeypatch):
    monkeypatch.setattr(monitor_signals, "get_adapter", lambda key: StubAdapter())
    spy = SpyAlertManager()

    monitor_signals.check_new_signals("TXF", spy, instrument=TXF)

    assert spy.sent, "空頭 BOS 訊號應觸發推播"
    bearish = [m for m in spy.sent if "空頭" in m]
    assert bearish, f"訊息應含空方方向，實得: {spy.sent}"
    # mock 源標示（FR-010：dry-run 語意，真源接上即無此前綴）
    assert all("【MOCK 資料—dry-run】" in m for m in spy.sent), spy.sent


def test_short_signal_dedup_unchanged(isolated_db, monkeypatch):
    monkeypatch.setattr(monitor_signals, "get_adapter", lambda key: StubAdapter())
    spy = SpyAlertManager()
    monitor_signals.check_new_signals("TXF", spy, instrument=TXF)
    n_first = len(spy.sent)
    assert n_first > 0
    monitor_signals.check_new_signals("TXF", spy, instrument=TXF)  # 同 K 線再跑
    assert len(spy.sent) == n_first, "去重失效：同 bar 訊號重複推播"


def test_equity_path_untouched(isolated_db, monkeypatch):
    """equity ticker 不帶 instrument → 走現行 yfinance 路徑（stub 掉網路）、無 mock 前綴。"""
    def fake_fetch_stock(ticker, period, interval):
        return _bear_bos_frame()
    monkeypatch.setattr(monitor_signals, "fetch_stock_data", fake_fetch_stock)
    spy = SpyAlertManager()
    monitor_signals.check_new_signals("2330.TW", spy)
    assert spy.sent
    assert all("MOCK" not in m for m in spy.sent)
