# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 015：事後表現追蹤的 monitor 整合測試。

涵蓋 SC-001~005、SC-009、SC-010。純函式與儲存層見
`tests/test_alert_outcomes.py`。

**SC-001 是本檔最重要的一條**：本案是觀察層，對既有七種告警的行為必須
零影響。基準凍結於 `tests/fixtures_015_baseline_alerts.json`（T002，
於實作前產生）——那份檔案一旦被污染就再也做不出來。
"""

import json
import os
import sqlite3

import pytest

import alert_outcomes as ao
import monitor_signals as m
from config.config import MaAlertConfig, OutcomeTrackingConfig
from db_security import safe_save_to_sqlite, table_name_for
from instruments import equity_instrument
from tests.ma_fixtures import daily_frame, intraday_frame, trending_intraday_frame

TICKER = "TEST.TW"
FLAT_LEVEL = 100.0
BASELINE_PATH = os.path.join(os.path.dirname(__file__),
                             "fixtures_015_baseline_alerts.json")


class CapturingAlertManager:
    """替身推播管道：記錄訊息而不實際送出。"""
    is_mock = True
    line_enabled = False
    tg_enabled = False

    def __init__(self, succeed=True):
        self.messages = []
        self._succeed = succeed

    def send_alert(self, msg):
        self.messages.append(msg)
        return self._succeed


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔離的監控環境：暫存 DB、暫存紀錄目錄、替身取數與推播。"""
    db_path = str(tmp_path / "test.db")
    log_dir = str(tmp_path / "alert_log")
    monkeypatch.setattr(m, "DB_PATH", db_path)
    m.init_sent_alerts_db(db_path)

    state = {"intraday": trending_intraday_frame(n_days=3)}
    monkeypatch.setattr(m, "fetch_stock_data",
                        lambda ticker, period, interval: state["intraday"].copy())

    class Env:
        db = db_path
        log = log_dir

        def seed_daily(self, n=300, base=FLAT_LEVEL, slope=0.0):
            df = daily_frame(n=n, base=base, slope=slope)
            table = table_name_for(equity_instrument(TICKER), "daily")
            assert safe_save_to_sqlite(df, table, db_path), "日線寫入失敗"
            return df

        def set_intraday(self, df):
            state["intraday"] = df

        def configure(self, *, tracking=True, ma_alerts=False, **tracking_kwargs):
            cfg = MaAlertConfig(ma_alerts_enabled=ma_alerts)
            cfg.outcome_tracking = OutcomeTrackingConfig(
                enabled=tracking, log_dir=log_dir, **tracking_kwargs)
            monkeypatch.setattr(m.cfg, "alerts", cfg)
            return cfg

        def run(self, succeed=True):
            mgr = CapturingAlertManager(succeed=succeed)
            m.check_new_signals(TICKER, mgr, instrument=None)
            return mgr

        def records(self):
            return ao.load_all(log_dir)

        def sent_rows(self):
            conn = sqlite3.connect(db_path)
            try:
                return conn.execute(
                    "SELECT ticker, bar_time, alert_type FROM sent_alerts "
                    "ORDER BY alert_type, bar_time"
                ).fetchall()
            finally:
                conn.close()

    return Env()


def _baseline():
    with open(BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# SC-001：開關關閉時，既有告警逐則相同且不產生任何紀錄
# ---------------------------------------------------------------------------

def _scenario_state(env, scenario_name):
    """依基準情境設定環境，回傳實際產出。"""
    if scenario_name == "structural_trending":
        env.set_intraday(trending_intraday_frame(n_days=3))
        env.configure(tracking=False, ma_alerts=False)
    elif scenario_name == "ma_cross_below":
        env.set_intraday(intraday_frame("cross_below", level=FLAT_LEVEL, n_days=2))
        env.seed_daily(n=300, base=FLAT_LEVEL, slope=0.0)
        env.configure(tracking=False, ma_alerts=True)
    else:
        raise AssertionError(f"未知情境 {scenario_name}")
    mgr = env.run()
    rows = [{"ticker": t, "bar_time": b, "alert_type": a} for t, b, a in env.sent_rows()]
    return rows, mgr.messages


@pytest.mark.parametrize("index", [0, 1])
def test_sc001_existing_alerts_identical_when_disabled(env, index):
    """
    開關關閉時，既有告警的去重列與**完整訊息字串**皆與實作前逐則相同。

    基準含兩條路徑：結構告警（5 分線）與均線告警（spec 014 日線路徑）。
    """
    expected = _baseline()["scenarios"][index]
    rows, messages = _scenario_state(env, expected["scenario"])
    assert rows == expected["sent_alerts"]
    assert messages == expected["messages"]


def test_sc001_disabled_creates_no_log_at_all(env):
    """關閉時**不建立目錄、不建立檔案**——連空目錄都不該出現。"""
    env.configure(tracking=False, ma_alerts=False)
    env.run()
    assert not os.path.exists(env.log)


def test_sc001_enabling_does_not_change_existing_alerts(env):
    """開啟後既有告警的產出仍與基準相同——新增的只有紀錄。"""
    expected = _baseline()["scenarios"][0]
    env.set_intraday(trending_intraday_frame(n_days=3))
    env.configure(tracking=True, ma_alerts=False)
    mgr = env.run()
    rows = [{"ticker": t, "bar_time": b, "alert_type": a} for t, b, a in env.sent_rows()]
    assert rows == expected["sent_alerts"]
    assert mgr.messages == expected["messages"]
    assert len(env.records()) == len(expected["sent_alerts"])


# ---------------------------------------------------------------------------
# SC-002：紀錄層故障不得阻斷推播
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", ["make_record", "upsert_records"])
def test_sc002_recorder_failure_does_not_block_alerts(env, monkeypatch, target):
    expected = _baseline()["scenarios"][0]
    env.set_intraday(trending_intraday_frame(n_days=3))
    env.configure(tracking=True, ma_alerts=False)

    def _boom(*args, **kwargs):
        raise RuntimeError("刻意注入的紀錄層故障")

    monkeypatch.setattr(ao, target, _boom)
    mgr = env.run()

    rows = [{"ticker": t, "bar_time": b, "alert_type": a} for t, b, a in env.sent_rows()]
    assert rows == expected["sent_alerts"], "紀錄層故障不得影響去重紀錄"
    assert mgr.messages == expected["messages"], "紀錄層故障不得影響推播"


def test_sc002_backfill_failure_does_not_block(env, monkeypatch):
    env.configure(tracking=True)
    monkeypatch.setattr(ao, "backfill",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert m.run_outcome_backfill() == 0        # 吞掉例外、回傳 0
    assert env.run().messages                    # 推播照常


# ---------------------------------------------------------------------------
# SC-003：偵測即記錄，不受推播成敗影響
# ---------------------------------------------------------------------------

def test_sc003_records_even_when_notification_fails(env):
    """
    推播失敗時訊號仍須被記錄，且標示 notified=false。

    這是 FR-001 的核心：若沿用 `mark_alert_as_sent` 的時機（只在推播成功時
    呼叫），LINE 掛掉那一輪就等於訊號沒發生過，樣本被通知管道汙染。
    """
    env.set_intraday(trending_intraday_frame(n_days=3))
    env.configure(tracking=True, ma_alerts=False)
    mgr = env.run(succeed=False)

    assert mgr.messages, "應有嘗試推播"
    assert env.sent_rows() == [], "推播失敗不應寫入去重表（既有語意）"
    records = env.records()
    assert len(records) == 1, "推播失敗仍必須留下紀錄"
    assert records[0]["notified"] is False
    assert records[0]["alert_type"] == "BULLISH_BOS"
    assert records[0]["close"] is not None


def test_sc003_records_carry_fingerprint_and_timeframe(env):
    env.set_intraday(trending_intraday_frame(n_days=3))
    env.configure(tracking=True, ma_alerts=False)
    env.run()
    rec = env.records()[0]
    assert rec["timeframe"] == "5m"
    assert rec["param_fingerprint"] == ao.build_fingerprint(
        **m.MONITOR_STRUCTURE_PARAMS,
        use_bos_volume=m.cfg.strategy.get_params_for_ticker(TICKER).use_bos_volume,
        bos_volume_mult=m.cfg.strategy.get_params_for_ticker(TICKER).bos_volume_mult,
        bos_volume_period=m.cfg.strategy.get_params_for_ticker(TICKER).bos_volume_period,
    )


def test_sc003_ma_alerts_are_recorded_as_daily(env):
    """均線告警走日線去重粒度 → timeframe 標 daily、bar_time 為交易日。"""
    env.set_intraday(intraday_frame("cross_below", level=FLAT_LEVEL, n_days=2))
    env.seed_daily(n=300, base=FLAT_LEVEL, slope=0.0)
    env.configure(tracking=True, ma_alerts=True)
    env.run()
    ma_records = [r for r in env.records() if r["alert_type"].startswith("MA_")]
    assert len(ma_records) == 4, "四條均線各應留下一列"
    for rec in ma_records:
        assert rec["timeframe"] == "daily"
        assert rec["direction"] == -1
        assert len(rec["bar_time"]) == 10, "bar_time 應為交易日（YYYY-MM-DD）"


# ---------------------------------------------------------------------------
# SC-004 / SC-005：冪等與 notified 不降級
# ---------------------------------------------------------------------------

def test_sc004_repeated_detection_yields_single_row(env):
    env.set_intraday(trending_intraday_frame(n_days=3))
    env.configure(tracking=True, ma_alerts=False)
    for _ in range(5):
        env.run()
    assert len(env.records()) == 1


def test_sc005_notified_not_downgraded_when_dedup_blocks(env):
    """
    首輪成功推播 → notified=true；次輪被去重擋下（不會執行推播）→ 仍為 true。

    若寫入邏輯以「本輪是否推播成功」無條件覆寫，這裡就會被改回 false。
    """
    env.set_intraday(trending_intraday_frame(n_days=3))
    env.configure(tracking=True, ma_alerts=False)

    env.run(succeed=True)
    assert env.records()[0]["notified"] is True

    env.run(succeed=True)      # 去重擋下，不再推播
    records = env.records()
    assert len(records) == 1
    assert records[0]["notified"] is True, "notified 不得由 True 降級"


def test_notified_upgrades_after_retry(env):
    """首輪推播失敗（false），次輪成功 → 升級為 true。"""
    env.set_intraday(trending_intraday_frame(n_days=3))
    env.configure(tracking=True, ma_alerts=False)

    env.run(succeed=False)
    assert env.records()[0]["notified"] is False

    env.run(succeed=True)
    records = env.records()
    assert len(records) == 1
    assert records[0]["notified"] is True


# ---------------------------------------------------------------------------
# SC-009：紀錄不隨工作資料庫消失
# ---------------------------------------------------------------------------

def test_sc009_records_survive_database_rebuild(env):
    """
    清空並重建 `trendpoint.db` 後紀錄仍完整。

    這在本設計下是**結構保證**而非巧合：紀錄根本不住在 DB 裡
    （research.md D1——排程環境的 DB 存活於 actions/cache，有逐出機制）。
    """
    env.set_intraday(trending_intraday_frame(n_days=3))
    env.configure(tracking=True, ma_alerts=False)
    env.run()
    before = env.records()
    assert before

    os.remove(env.db)
    m.init_sent_alerts_db(env.db)
    assert env.sent_rows() == [], "去重表確已清空"
    assert env.records() == before, "紀錄不應受工作資料庫重建影響"


# ---------------------------------------------------------------------------
# SC-010：無事發生的輪次不得產生任何變更
# ---------------------------------------------------------------------------

def test_sc010_idle_round_leaves_file_byte_identical(env):
    """
    既無新告警、亦無可回填視窗的輪次 → 檔案內容與 mtime 皆不變。

    要防的是無事發生的輪次產生雜訊 commit（FR-009，措辭見 research.md D8）。
    """
    env.set_intraday(trending_intraday_frame(n_days=3))
    env.configure(tracking=True, ma_alerts=False)
    env.run()

    shard = os.path.join(env.log, sorted(os.listdir(env.log))[0])
    content_before = open(shard, "rb").read()
    mtime_before = os.stat(shard).st_mtime_ns

    env.run()          # 同一根 K 線、同一告警 → 無新資訊
    m.run_outcome_backfill()   # 無日線表 → 無可回填視窗

    assert open(shard, "rb").read() == content_before
    assert os.stat(shard).st_mtime_ns == mtime_before


def test_backfill_fills_outcomes_from_daily_table(env):
    """回填走既有日線表；告警日之後的交易日陸續到期時填入。"""
    env.set_intraday(trending_intraday_frame(n_days=3))
    env.configure(tracking=True, ma_alerts=False)
    env.run()

    rec = env.records()[0]
    bar_day = rec["bar_time"][:10]
    # 灌入涵蓋告警日之後 10 個交易日的日線
    env.seed_daily(n=400, base=FLAT_LEVEL, slope=0.1)
    from tests.outcome_fixtures import daily_linear
    df = daily_linear(n=12, start=bar_day, base=200.0, step=1.0)
    safe_save_to_sqlite(df, table_name_for(equity_instrument(TICKER), "daily"), env.db)

    assert m.run_outcome_backfill() == 1
    filled = env.records()[0]["outcomes"]
    assert filled["t1"] is not None and filled["t5"] is not None
    assert m.run_outcome_backfill() == 0, "回填必須冪等"


def test_backfill_noop_when_tracking_disabled(env):
    env.configure(tracking=False)
    assert m.run_outcome_backfill() == 0
    assert not os.path.exists(env.log)
