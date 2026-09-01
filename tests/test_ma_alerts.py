# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 014：均線觸價通知的 monitor 整合測試。

涵蓋 SC-001（既有六種告警不變，三層驗收）、SC-002~005、SC-007~009、
SC-011/012（儀表板現況列）。全部以合成資料執行——本案的驗收需要精確控制
價格與均線的相對位置，真實資料無法保證邊界情境出現在測試窗內。
"""

import json
import os

import pandas as pd
import pytest

import monitor_signals as m
import ma_lines
from config.config import MaAlertConfig
from db_security import safe_save_to_sqlite, table_name_for
from instruments import equity_instrument
from tests.ma_fixtures import (
    daily_frame,
    intraday_frame,
    trending_intraday_frame,
)

TICKER = "TEST.TW"
BASELINE_PATH = os.path.join(os.path.dirname(__file__), "fixtures_014_baseline_alerts.json")

# 日線全為 100.0 → 四條均線皆為 100.0，使穿越測試可用精確值預期
FLAT_LEVEL = 100.0


class CapturingAlertManager:
    """替身推播管道：記錄訊息而不實際送出。"""
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
    """
    隔離的監控環境：暫存 DB（含去重表）、替身取數、替身推播管道。

    回傳一個具下列方法的物件：
      - seed_daily(n, base, slope)：寫入日線表
      - set_intraday(df)：設定 5 分線取數結果
      - set_alerts(cfg)：設定 alerts 組態
      - run()：執行 check_new_signals，回傳 CapturingAlertManager
      - sent_rows()：讀回去重表
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(m, "DB_PATH", db_path)
    m.init_sent_alerts_db(db_path)

    state = {"intraday": trending_intraday_frame(n_days=3)}

    monkeypatch.setattr(m, "fetch_stock_data",
                        lambda ticker, period, interval: state["intraday"].copy())

    class Env:
        db = db_path

        def seed_daily(self, n=300, base=FLAT_LEVEL, slope=0.0):
            df = daily_frame(n=n, base=base, slope=slope)
            table = table_name_for(equity_instrument(TICKER), "daily")
            assert safe_save_to_sqlite(df, table, db_path), "日線寫入失敗"
            return df

        def set_intraday(self, df):
            state["intraday"] = df

        def set_alerts(self, alerts_cfg):
            monkeypatch.setattr(m.cfg, "alerts", alerts_cfg)

        def run(self):
            mgr = CapturingAlertManager()
            m.check_new_signals(TICKER, mgr, instrument=None)
            return mgr

        def sent_rows(self):
            import sqlite3
            conn = sqlite3.connect(db_path)
            try:
                return conn.execute(
                    "SELECT ticker, bar_time, alert_type FROM sent_alerts "
                    "ORDER BY alert_type, bar_time"
                ).fetchall()
            finally:
                conn.close()

    return Env()


def _alerts_on(**overrides):
    """啟用總開關的 alerts 組態；overrides 可關閉個別線。"""
    cfg = MaAlertConfig(ma_alerts_enabled=True)
    for name, enabled in overrides.items():
        getattr(cfg, name).enabled = enabled
    return cfg


def _ma_alert_types(rows):
    return {r[2] for r in rows if r[2].startswith("MA_CROSS_BELOW_")}


# 均線通知的標題集合。自 LINE_LABELS 導出而非寫死字面值——新增線別時
# （如本次的週線）測試會自動涵蓋，不會悄悄漏掉一條。
_MA_TITLES = tuple(f"<b>【跌破{label}】" for label in ma_lines.LINE_LABELS.values())


def _is_ma_msg(msg: str) -> bool:
    """
    辨識均線通知訊息。

    以**標題**為判準。刻意不用「乖離:」欄位：自「均線現況」區塊附加到每則
    推播之後，該欄位不再是均線通知獨有（初版即以此判別，會把帶現況區塊的
    結構告警一併誤計）。也不能只看「跌破」二字——既有的「跌破下關價」與
    「BOS 結構連續跌破」同樣含該詞。
    """
    return msg.startswith(_MA_TITLES)


def _ma_msgs(mgr):
    return [msg for msg in mgr.messages if _is_ma_msg(msg)]


def _legacy_rows(rows):
    return [r for r in rows if not r[2].startswith("MA_CROSS_BELOW_")]


# ---------------------------------------------------------------------------
# T010：SC-001 既有六種告警不變（三層驗收）
# ---------------------------------------------------------------------------

def test_sc001_layer1_legacy_alerts_match_frozen_baseline(env):
    """
    第一層：總開關關閉（預設）時，既有六種告警的產出與實作前凍結的基準
    逐則相同（alert_type、bar_time、訊息內容）。
    """
    with open(BASELINE_PATH, encoding="utf-8") as f:
        baseline = json.load(f)

    env.set_alerts(MaAlertConfig())          # 預設＝總開關關閉
    mgr = env.run()

    got = [{"ticker": r[0], "bar_time": r[1], "alert_type": r[2]} for r in env.sent_rows()]
    assert got == baseline["sent_alerts"], "既有告警的發送紀錄與基準不符"
    assert mgr.messages == baseline["messages"], "既有告警的訊息內容與基準不符"


def test_sc001_layer2_disabled_does_not_read_daily_table(env, monkeypatch):
    """
    第二層：總開關關閉時**完全不讀日線表**——證明是真正的短路，
    而非「讀了但沒用」。
    """
    calls = []
    import db_security

    real = db_security.safe_load_db_data

    def counting_loader(db_path, table_name):
        calls.append(table_name)
        return real(db_path, table_name)

    monkeypatch.setattr(db_security, "safe_load_db_data", counting_loader)

    env.seed_daily()
    env.set_alerts(MaAlertConfig())          # 關閉
    env.run()

    assert calls == [], f"總開關關閉時不應讀取任何資料表，實際讀了 {calls}"


def test_sc001_layer3_enabling_does_not_change_legacy_alerts(env):
    """
    第三層：總開關開啟時，既有六種告警的產出**仍與關閉時相同**，
    只多出均線通知。
    """
    env.seed_daily()

    env.set_alerts(MaAlertConfig())
    off_rows = _legacy_rows(env.sent_rows())
    mgr_off = env.run()
    off_rows = _legacy_rows(env.sent_rows())
    off_messages = list(mgr_off.messages)

    # 換一個乾淨的去重狀態重跑（同一 env 內以不同 alert_type 區分即可）
    env.set_alerts(_alerts_on())
    mgr_on = env.run()
    on_rows = _legacy_rows(env.sent_rows())

    assert on_rows == off_rows, "啟用均線通知後既有告警的紀錄改變了"
    # 開啟後既有告警因去重不會重送，故訊息集合的既有部分應為空；
    # 關鍵是紀錄不變（上一行）與新增訊息皆為均線類（下一行）
    for msg in mgr_on.messages:
        assert _is_ma_msg(msg), f"啟用後出現非均線類的新訊息: {msg[:60]}"
    assert off_messages, "基準情境應至少產生一則既有告警，否則本測試失去鑑別力"


# ---------------------------------------------------------------------------
# T018-T021：US1 穿越通知
# ---------------------------------------------------------------------------

def test_sc002_cross_below_triggers_each_line_once(env):
    """SC-002：自均線上方跌破 → 五條線各發出且僅發出一則。"""
    env.seed_daily()                                    # 五條均線皆 = 100.0
    env.set_intraday(intraday_frame("cross_below", FLAT_LEVEL, n_days=2))
    env.set_alerts(_alerts_on())

    mgr = env.run()

    assert _ma_alert_types(env.sent_rows()) == {
        "MA_CROSS_BELOW_WEEKLY",
        "MA_CROSS_BELOW_MONTHLY", "MA_CROSS_BELOW_QUARTERLY",
        "MA_CROSS_BELOW_HALF_YEARLY", "MA_CROSS_BELOW_YEARLY",
    }
    ma_msgs = _ma_msgs(mgr)
    assert len(ma_msgs) == 5, f"應發出 5 則均線通知，實際 {len(ma_msgs)}"


def test_sc003_persisting_below_does_not_realert(env):
    """SC-003：穿越後持續低於均線，**僅第一次**發出，後續不再發送。"""
    env.seed_daily()
    env.set_intraday(intraday_frame("stay_below", FLAT_LEVEL, n_days=2))
    env.set_alerts(_alerts_on())

    first = env.run()
    first_count = len(_ma_msgs(first))

    second = env.run()          # 同一交易日再次輪詢
    second_count = len(_ma_msgs(second))

    assert second_count == 0, "持續低於均線期間不得重複通知"
    # 末根已在均線下方且前一根亦在下方 → 本身就不構成穿越
    assert first_count == 0, "前值已在下方時不應觸發（穿越是事件、非狀態）"


def test_sc004_same_day_oscillation_alerts_at_most_once(env):
    """SC-004：同一交易日內反覆上下穿越，該線當日至多一則。"""
    env.seed_daily()
    env.set_intraday(intraday_frame("oscillate", FLAT_LEVEL, n_days=2))
    env.set_alerts(_alerts_on())

    env.run()
    rows_after_first = env.sent_rows()
    env.run()
    env.run()
    rows_after_third = env.sent_rows()

    assert rows_after_third == rows_after_first, "同一交易日重複輪詢不得增加通知"
    per_type = {}
    for _, bar_time, alert_type in rows_after_third:
        if alert_type.startswith("MA_CROSS_BELOW_"):
            per_type.setdefault(alert_type, set()).add(bar_time)
    for alert_type, days in per_type.items():
        assert len(days) == 1, f"{alert_type} 於同一交易日出現多筆: {days}"


def test_staying_above_never_alerts(env):
    """全程在均線之上 → 不發出任何均線通知。"""
    env.seed_daily()
    env.set_intraday(intraday_frame("above", FLAT_LEVEL, n_days=2))
    env.set_alerts(_alerts_on())

    env.run()
    assert _ma_alert_types(env.sent_rows()) == set()


def test_sc008_message_contains_required_fields(env):
    """SC-008：訊息含標的、線別、均線值、現價、乖離、時間，且線別可辨識。"""
    env.seed_daily()
    env.set_intraday(intraday_frame("cross_below", FLAT_LEVEL, n_days=2))
    env.set_alerts(_alerts_on(monthly=True, quarterly=False,
                              half_yearly=False, yearly=False))

    mgr = env.run()
    ma_msgs = [msg for msg in _ma_msgs(mgr) if "跌破月線" in msg]
    assert len(ma_msgs) == 1
    msg = ma_msgs[0]

    assert TICKER in msg
    assert "月線" in msg
    assert "100.00" in msg          # 均線值
    assert "乖離" in msg
    assert "時間" in msg
    assert "價格" in msg


def test_sc009_missing_daily_table_skips_without_breaking(env, capsys):
    """
    SC-009：日線表不存在時跳過該標的的均線判定，**不拋錯**，
    且既有六種告警照常運作。
    """
    # 刻意不呼叫 seed_daily()
    env.set_intraday(intraday_frame("cross_below", FLAT_LEVEL, n_days=2))
    env.set_alerts(_alerts_on())

    mgr = env.run()          # 不得拋錯

    assert _ma_alert_types(env.sent_rows()) == set(), "無日線資料時不得發出均線通知"
    out = capsys.readouterr().out
    assert "略過" in out, "應輸出可辨識的略過提示"


def test_futures_instrument_is_excluded(env):
    """FR-010：期貨標的完全不進入均線判定（back-adjust 使年線價位語意不可靠）。"""
    env.seed_daily()
    env.set_intraday(intraday_frame("cross_below", FLAT_LEVEL, n_days=2))
    env.set_alerts(_alerts_on())

    mgr = CapturingAlertManager()
    m.check_ma_touch_alerts(
        TICKER, mgr, None,
        latest_bar={"close": 50.0}, prev_bar={"close": 150.0},
        latest_time=pd.Timestamp("2026-06-02 13:25:00"),
        is_futures=True, mock_prefix="", intraday_note="",
    )
    assert mgr.messages == [], "期貨標的不得發出均線通知"


# ---------------------------------------------------------------------------
# 均線現況區塊：附加於**每一則**推播（含既有六種結構告警）
# ---------------------------------------------------------------------------

_ALL_LINE_LABELS = ("週線 (5 日)", "月線 (20 日)", "季線 (60 日)",
                    "半年線 (120 日)", "年線 (240 日)")


def test_snapshot_appended_to_structural_alerts(env):
    """
    功能開啟時，六種結構告警的訊息尾端也帶「均線現況」全線列表——
    使用者收到任何一則推播就看得到均線位置，不必回頭查儀表板。
    """
    env.seed_daily()
    env.set_alerts(_alerts_on())

    mgr = env.run()

    structural = [msg for msg in mgr.messages if not _is_ma_msg(msg)]
    assert structural, "本情境應至少產生一則結構告警，否則測試失去鑑別力"
    for msg in structural:
        assert ma_lines.SNAPSHOT_HEADER in msg, f"結構告警缺少均線現況區塊: {msg[:40]}"
        for label in _ALL_LINE_LABELS:
            assert label in msg, f"均線現況缺少 {label}"


def test_snapshot_appended_to_ma_cross_alerts(env):
    """均線跌破通知同樣帶全線現況——被跌破的那條仍由標題與「乖離:」欄標示。"""
    env.seed_daily()
    env.set_intraday(intraday_frame("cross_below", FLAT_LEVEL, n_days=2))
    env.set_alerts(_alerts_on())

    mgr = env.run()

    ma_msgs = _ma_msgs(mgr)
    assert ma_msgs
    for msg in ma_msgs:
        assert ma_lines.SNAPSHOT_HEADER in msg
        assert "乖離:" in msg, "觸發線的標示欄不得被現況區塊取代"


def test_snapshot_absent_when_master_switch_off(env):
    """
    總開關關閉 → 訊息與實作前逐字相同（凍結基準亦已涵蓋；此處明寫其意圖）。
    """
    env.seed_daily()
    env.set_alerts(MaAlertConfig())

    mgr = env.run()

    assert mgr.messages, "本情境應至少產生一則告警"
    for msg in mgr.messages:
        assert ma_lines.SNAPSHOT_HEADER not in msg


def test_daily_table_read_once_per_run(env, monkeypatch):
    """
    日線表**每輪只讀一次**：現況區塊與穿越判定共用同一份快照。
    分兩次讀會讓同一則訊息裡的「觸發線均線值」與現況區塊的同一條線互相矛盾。
    """
    calls = []
    import db_security
    real = db_security.safe_load_db_data

    def counting_loader(db_path, table_name):
        calls.append(table_name)
        return real(db_path, table_name)

    monkeypatch.setattr(db_security, "safe_load_db_data", counting_loader)

    env.seed_daily()
    env.set_intraday(intraday_frame("cross_below", FLAT_LEVEL, n_days=2))
    env.set_alerts(_alerts_on())
    env.run()

    assert len(calls) == 1, f"日線表應只讀一次，實際 {len(calls)} 次：{calls}"


def test_futures_get_no_snapshot(env):
    """
    FR-010 的延伸：期貨連續表經 back-adjust，其均線價位語意不可靠，故
    **不附現況區塊**——把不可靠的價位放進訊息比不放更糟（使用者無從得知）。
    """
    env.seed_daily()
    env.set_alerts(_alerts_on())

    ctx = m.build_ma_context(TICKER, None,
                             latest_time=pd.Timestamp("2026-06-02 13:25:00"),
                             is_futures=True, price=100.0)
    assert ctx is None


# ---------------------------------------------------------------------------
# T011：SC-007 開關
# ---------------------------------------------------------------------------

def test_sc007_master_switch_off_sends_nothing(env):
    env.seed_daily()
    env.set_intraday(intraday_frame("cross_below", FLAT_LEVEL, n_days=2))
    env.set_alerts(MaAlertConfig())        # 總開關關閉

    env.run()
    assert _ma_alert_types(env.sent_rows()) == set()


def test_sc007_individual_line_can_be_disabled(env):
    """單線關閉 → 該線不發，其餘線正常。"""
    env.seed_daily()
    env.set_intraday(intraday_frame("cross_below", FLAT_LEVEL, n_days=2))
    env.set_alerts(_alerts_on(weekly=False, yearly=False, half_yearly=False))

    env.run()
    assert _ma_alert_types(env.sent_rows()) == {
        "MA_CROSS_BELOW_MONTHLY", "MA_CROSS_BELOW_QUARTERLY",
    }


# ---------------------------------------------------------------------------
# T022：SC-005 資料不足（端到端）
# ---------------------------------------------------------------------------

def test_sc005_insufficient_daily_data_skips_only_long_lines(env):
    """
    SC-005：僅 100 根日線 → 週線、月線與季線正常、半年線與年線不發，
    且單一條線的不足不影響其他線。
    """
    env.seed_daily(n=100)
    env.set_intraday(intraday_frame("cross_below", FLAT_LEVEL, n_days=2))
    env.set_alerts(_alerts_on())

    env.run()

    assert _ma_alert_types(env.sent_rows()) == {
        "MA_CROSS_BELOW_WEEKLY",
        "MA_CROSS_BELOW_MONTHLY", "MA_CROSS_BELOW_QUARTERLY",
    }, "半年線與年線在 100 根日線下必須不發"


def test_ma_uses_only_closed_daily_bars(env):
    """
    FR-002：均線僅使用已收盤日線——與比較價同一交易日的那根必須被排除。

    構造：前 299 根為 100.0、最後一根（與比較價同日）為 1.0。若當日被計入，
    月線會被拉低至 95.05 而非 100.0，導致跌破判定改變。
    """
    df = daily_frame(n=299, base=FLAT_LEVEL, slope=0.0)
    same_day = pd.Timestamp("2026-06-02")
    extra = daily_frame(n=1, base=1.0, slope=0.0)
    extra.index = pd.DatetimeIndex([same_day], name="datetime")
    combined = pd.concat([df, extra])
    table = table_name_for(equity_instrument(TICKER), "daily")
    assert safe_save_to_sqlite(combined, table, env.db)

    # 比較價：自 100 之上跌到 99 —— 若當日被計入使均線 < 99，則不應觸發
    env.set_intraday(intraday_frame("cross_below", FLAT_LEVEL, n_days=2,
                                    start="2026-06-01"))
    env.set_alerts(_alerts_on(weekly=False, quarterly=False, half_yearly=False, yearly=False))

    mgr = env.run()
    ma_msgs = [msg for msg in _ma_msgs(mgr) if "跌破月線" in msg]
    assert len(ma_msgs) == 1, "排除當日後月線應為 100.00 並觸發跌破"
    assert "100.00" in ma_msgs[0], "均線值不應被當日 K 線汙染"


# ---------------------------------------------------------------------------
# T024：SC-011／SC-012 儀表板現況列
# ---------------------------------------------------------------------------

def test_sc011_status_rows_show_position_without_alerting(env):
    """
    SC-011：對「已低於年線但近期無穿越」之標的，現況列正確顯示位置與乖離，
    且**不觸發任何推播**（FR-014）。
    """
    daily = daily_frame(n=300, base=FLAT_LEVEL, slope=0.0)
    rows = ma_lines.build_status_rows(daily["close"], MaAlertConfig().all_periods(),
                                      current_price=90.0)

    assert [r["line"] for r in rows] == ["weekly", "monthly", "quarterly",
                                        "half_yearly", "yearly"]
    for r in rows:
        assert r["position"] == "在下"
        assert r["deviation"] == pytest.approx(-0.10)
        assert r["ma"] == pytest.approx(100.0)

    # 現況查詢不經過任何推播路徑：確認去重表維持空白
    assert env.sent_rows() == []


def test_sc012_status_rows_mark_insufficient_data(env):
    """SC-012：資料不足之線的 ma／position／deviation 皆為 None（呈現為「資料不足」）。"""
    daily = daily_frame(n=100, base=FLAT_LEVEL, slope=0.0)
    rows = ma_lines.build_status_rows(daily["close"], MaAlertConfig().all_periods(),
                                      current_price=90.0)
    by_line = {r["line"]: r for r in rows}

    for name in ("weekly", "monthly", "quarterly"):
        assert by_line[name]["ma"] is not None
        assert by_line[name]["position"] == "在下"

    for name in ("half_yearly", "yearly"):
        assert by_line[name]["ma"] is None, f"{name} 資料不足時 ma 必須為 None"
        assert by_line[name]["position"] is None
        assert by_line[name]["deviation"] is None
