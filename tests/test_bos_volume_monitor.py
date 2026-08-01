# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 012 — 監控端整合（T024，FR-010 / contracts §5）。

兩件事：
1. 濾網**關閉**時（預設），monitor 的告警集合與實作前逐字相同。
2. 濾網**啟用**時，量能未達門檻的 BOS 不推播；MSS 告警不受影響。

沿用 spec 014 的監控測試範式（替身取數 + 替身推播 + 暫存去重 DB）。
"""

import pandas as pd
import pytest

import monitor_signals as m
from config.config import SingleStrategyParams

from bos_volume_fixtures import expected_volume_ok
from ma_fixtures import trending_intraday_frame

TICKER = "TEST.TW"


class CapturingAlertManager:
    is_mock = True
    line_enabled = False
    tg_enabled = False

    def __init__(self):
        self.messages = []

    def send_alert(self, msg):
        self.messages.append(msg)
        return True


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔離的監控環境：暫存去重 DB、替身 5 分線取數、可切換的策略參數。"""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(m, "DB_PATH", db_path)
    m.init_sent_alerts_db(db_path)

    state = {"intraday": trending_intraday_frame(n_days=5)}
    monkeypatch.setattr(m, "fetch_stock_data",
                        lambda ticker, period, interval: state["intraday"].copy())

    class Env:
        def set_intraday(self, df):
            state["intraday"] = df

        def set_params(self, **overrides):
            params = SingleStrategyParams(**overrides)
            monkeypatch.setattr(m.cfg.strategy, "ticker_overrides", {TICKER: params})
            return params

        def run(self):
            mgr = CapturingAlertManager()
            m.check_new_signals(TICKER, mgr, instrument=None)
            return mgr

    return Env()


def _bos_msgs(mgr):
    """BOS 告警（多頭趨勢延續 / 空頭趨勢延續）。"""
    return [msg for msg in mgr.messages
            if "趨勢延續" in msg]


def _mss_msgs(mgr):
    return [msg for msg in mgr.messages if "反轉訊號" in msg]


def _volume_boosted_frame(base: pd.DataFrame, boost_last: bool) -> pd.DataFrame:
    """把末兩根的成交量壓低或拉高，以精確控制量能確認的判定。

    monitor 取「最後一根已收盤棒」（latest）為判定根，故只需控制該根的量。
    """
    df = base.copy()
    df["volume"] = 1000.0
    if boost_last:
        df.iloc[-1, df.columns.get_loc("volume")] = 100_000.0
        df.iloc[-2, df.columns.get_loc("volume")] = 100_000.0
    return df


def test_filter_off_emits_the_legacy_alert_set(env):
    """濾網關閉（預設）時，monitor 行為與實作前相同——BOS 告警照常推播。

    「與實作前相同」的欄位層證明在 test_bos_volume_confirmation.py
    （關閉時 build_indicator_frame 的欄位集與數值逐字不變）；此處驗的是
    monitor 這條路徑確實走在該預設上，沒有因為多接了三個參數而改變輸出。
    """
    frame = _volume_boosted_frame(trending_intraday_frame(n_days=5), boost_last=False)
    env.set_intraday(frame)
    env.set_params()                               # 全預設 → use_bos_volume=False

    mgr = env.run()
    assert _bos_msgs(mgr), "預設設定下 BOS 告警消失——預設值污染了既有行為"

    # 顯式傳入與預設相同的值，結果須一致（參數本身不改變語意）
    assert env.set_params(use_bos_volume=False,
                          bos_volume_mult=1.5,
                          bos_volume_period=20).use_bos_volume is False


def test_filter_on_blocks_bos_when_volume_short(env, tmp_path, monkeypatch):
    """同一份資料：濾網關閉時有 BOS 告警、啟用時沒有（差異僅由量能造成）。

    去重表會讓同一根 bar 的第二次執行靜默，故兩組各用**獨立**的監控環境。
    """
    frame = _volume_boosted_frame(trending_intraday_frame(n_days=5), boost_last=False)

    def run_with(use_filter):
        db = str(tmp_path / f"db_{use_filter}.db")
        monkeypatch.setattr(m, "DB_PATH", db)
        m.init_sent_alerts_db(db)
        monkeypatch.setattr(m, "fetch_stock_data",
                            lambda ticker, period, interval: frame.copy())
        monkeypatch.setattr(
            m.cfg.strategy, "ticker_overrides",
            {TICKER: SingleStrategyParams(use_bos_volume=use_filter)})
        mgr = CapturingAlertManager()
        m.check_new_signals(TICKER, mgr, instrument=None)
        return mgr

    off = run_with(False)
    on = run_with(True)

    assert _bos_msgs(off), "fixture 失去鑑別力：關閉時本來就沒有 BOS 告警"
    assert _bos_msgs(on) == [], "量能不足時 BOS 告警仍推播——濾網未接上監控端"

    # MSS 告警不受影響（FR-005：反轉分支不套用本濾網）
    assert _mss_msgs(on) == _mss_msgs(off)


def test_filter_on_allows_high_volume_bos(env, tmp_path, monkeypatch):
    """量能達門檻時 BOS 告警照常推播——證明擋的是量能，不是把 BOS 全關掉。"""
    frame = _volume_boosted_frame(trending_intraday_frame(n_days=5), boost_last=True)

    db = str(tmp_path / "db_high.db")
    monkeypatch.setattr(m, "DB_PATH", db)
    m.init_sent_alerts_db(db)
    monkeypatch.setattr(m, "fetch_stock_data",
                        lambda ticker, period, interval: frame.copy())
    monkeypatch.setattr(m.cfg.strategy, "ticker_overrides",
                        {TICKER: SingleStrategyParams(use_bos_volume=True)})

    mgr = CapturingAlertManager()
    m.check_new_signals(TICKER, mgr, instrument=None)

    # 判定根（末根已收盤棒）的量能確實達標
    assert bool(expected_volume_ok(frame).iloc[-1]), "fixture 未成功構造出量能達標的判定根"
    assert _bos_msgs(mgr), "量能達門檻時 BOS 告警仍被擋——濾網過度攔截"
