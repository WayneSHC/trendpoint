# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 015：事後表現追蹤的純函式、儲存層、組態與靜態檢查測試。

涵蓋 SC-006~008、SC-011~021。monitor 整合與既有行為回歸見
`tests/test_alert_outcomes_monitor.py`。

全部以合成資料執行——本案的驗收需要精確控制「假日缺口」「T+5 未到期」
「資料缺漏」等邊界，真實資料無法保證這些出現在測試窗內。
"""

import json
import os
import re
import subprocess
import sys

import pandas as pd
import pytest

import alert_outcomes as ao
from tests.outcome_fixtures import (
    daily_linear,
    daily_with_gaps,
    make_record,
    records_for_summary,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# SC-006：欄位白名單恆等
# ---------------------------------------------------------------------------

def test_sc006_record_fields_exactly_match_whitelist():
    """紀錄的欄位集合必須**恆等於**白名單——多一欄或少一欄皆為契約違反。"""
    bar = pd.Series({"close": 100.0, "ladder": 98.0, "upper_price": 101.0,
                     "lower_price": 97.0, "atr": 2.0})
    rec = ao.make_record(ticker="T.TW", bar_time=pd.Timestamp("2026-08-05 09:30"),
                         alert_type="BULLISH_MSS", timeframe="5m", bar=bar,
                         param_fingerprint="fp")
    assert tuple(rec.keys()) == ao.RECORD_FIELDS


def test_sc006_missing_indicator_is_none_not_zero():
    """
    缺值填 None、**不得填 0.0**。

    0.0 是合法的指標值；用它表示「沒有」會讓下游無法區分，
    且分布統計會混入假零。
    """
    rec = ao.make_record(ticker="T.TW", bar_time=pd.Timestamp("2026-08-05 09:30"),
                         alert_type="BULLISH_MSS", timeframe="5m",
                         bar=pd.Series({"close": 100.0}), param_fingerprint="fp")
    assert rec["ladder"] is None and rec["atr"] is None
    assert rec["close"] == 100.0


def test_unknown_alert_type_fails_fast():
    """未登記的 alert_type 必須 fail-fast——靜默回傳方向 0 會讓整群樣本的
    方向調整後報酬恆為 0，看起來「沒有資訊量」，真因卻是型別漏登記。"""
    with pytest.raises(ValueError, match="未知的 alert_type"):
        ao.direction_for("SOMETHING_NEW")


def test_all_monitor_alert_types_are_registered():
    """monitor 實際會產出的七類告警必須全數登記方向。"""
    import ma_lines
    expected = {"BULLISH_MSS", "BEARISH_MSS", "BULLISH_BOS", "BEARISH_BOS",
                "BREAK_UPPER_BAND", "BREAK_LOWER_BAND"}
    for name in ("monthly", "quarterly", "half_yearly", "yearly"):
        expected.add(ma_lines.alert_type_for(name))
    for alert_type in expected:
        assert ao.direction_for(alert_type) in (1, -1)


# ---------------------------------------------------------------------------
# SC-007：參數識別值
# ---------------------------------------------------------------------------

_FP_KWARGS = dict(structure_period=10, use_fvg=True, fvg_lookback=3, swing_n=2,
                  volume_mult=1.5, use_bos_volume=False, bos_volume_mult=1.5,
                  bos_volume_period=20)


def test_sc007_different_params_give_different_fingerprint():
    base = ao.build_fingerprint(**_FP_KWARGS)
    for field, other in (("structure_period", 20), ("use_fvg", False),
                         ("fvg_lookback", 5), ("swing_n", 3),
                         ("volume_mult", 2.0), ("use_bos_volume", True),
                         ("bos_volume_mult", 2.0), ("bos_volume_period", 30)):
        changed = ao.build_fingerprint(**{**_FP_KWARGS, field: other})
        assert changed != base, f"{field} 改變後識別值未變——分群會把兩批混算"


def test_sc007_same_params_stable_across_processes():
    """
    **跨行程**穩定性。

    這一條必須開新行程驗證：內建 `hash()` 對 str 有 per-process 隨機化，
    若實作誤用它，**同一行程內的測試會全部誤過**，只有跨行程才抓得到。
    """
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import alert_outcomes as ao;"
        "print(ao.build_fingerprint(**%r))" % (REPO_ROOT, _FP_KWARGS)
    )
    env = dict(os.environ, PYTHONHASHSEED="0")
    first = subprocess.check_output([sys.executable, "-c", code], env=env).decode().strip()
    env["PYTHONHASHSEED"] = "12345"
    second = subprocess.check_output([sys.executable, "-c", code], env=env).decode().strip()
    assert first == second == ao.build_fingerprint(**_FP_KWARGS)


def test_fingerprint_float_formatting_is_canonical():
    """1.5 與 1.50 必須產生同一字串——否則同一組參數會被分成兩群。"""
    assert (ao.build_fingerprint(**{**_FP_KWARGS, "volume_mult": 1.50})
            == ao.build_fingerprint(**{**_FP_KWARGS, "volume_mult": 1.5}))


# ---------------------------------------------------------------------------
# SC-011 / SC-014 / SC-015：前瞻報酬
# ---------------------------------------------------------------------------

def test_sc011_horizons_count_trading_days_not_calendar_days():
    """
    T+N 取「表中實際存在的列」——自動略過假日與停牌。

    08-05 告警，日線缺 08-08／08-09（週末）→ T+3 應為 **08-10**，
    而非日曆意義的 08-08。
    """
    daily = daily_with_gaps(
        closes=[100., 101., 102., 103., 104., 105.],
        dates=["2026-08-05", "2026-08-06", "2026-08-07",
               "2026-08-10", "2026-08-11", "2026-08-12"],
    )
    rec = make_record(bar_time="2026-08-05 09:30:00", close=100.0)
    out = ao.compute_outcomes(rec, daily, [1, 3, 5])
    assert out["t1"]["date"] == "2026-08-06"
    assert out["t3"]["date"] == "2026-08-10"
    assert out["t5"]["date"] == "2026-08-12"


def test_sc011_baseline_is_record_close_not_daily_close():
    """
    報酬基準是**紀錄的 close**（告警當下），不是告警日的日線收盤。

    這是刻意的跨時基設計（spec Assumptions A-3）：基準價要忠實反映
    「告警發出時使用者看到的價格」。
    """
    daily = daily_with_gaps(closes=[999., 110.],
                            dates=["2026-08-05", "2026-08-06"])
    rec = make_record(bar_time="2026-08-05 09:30:00", close=100.0)
    out = ao.compute_outcomes(rec, daily, [1])
    assert out["t1"]["ret"] == pytest.approx(0.10)   # 110/100-1，與 999 無關


def test_sc014_three_states_are_distinguishable():
    """未到期／不足／資料缺漏 → 未回填；且與「已回填且報酬為零」可區分。"""
    rec = make_record(bar_time="2026-08-05 09:30:00", close=100.0)

    # (a) 前瞻僅 2 根 → t3/t5 未回填
    short = daily_linear(n=3, start="2026-08-05", base=100.0, step=1.0)
    out = ao.compute_outcomes(rec, short, [1, 3, 5])
    assert out.get("t1") is not None
    assert out.get("t3") is None and out.get("t5") is None

    # (b) 資料缺漏 → 全部未回填
    assert ao.compute_outcomes(rec, None, [1, 3, 5]) == {}
    assert ao.compute_outcomes(rec, pd.DataFrame(), [1, 3, 5]) == {}

    # (c) 報酬確為 0.0 時是**已回填**，與未回填不同
    flat = daily_with_gaps(closes=[100.0, 100.0],
                           dates=["2026-08-05", "2026-08-06"])
    zero = ao.compute_outcomes(rec, flat, [1])
    assert zero["t1"]["ret"] == 0.0 and zero["t1"] is not None


def test_sc014_states_survive_json_roundtrip(tmp_path):
    """三態在序列化後仍可區分——這是選 JSONL 而非 CSV 的理由之一。"""
    rec = make_record(bar_time="2026-08-05 09:30:00", close=100.0)
    flat = daily_with_gaps(closes=[100.0, 100.0],
                           dates=["2026-08-05", "2026-08-06"])
    rec["outcomes"] = ao.compute_outcomes(rec, flat, [1, 3])
    ao.upsert_records(str(tmp_path), [rec])
    back = ao.load_all(str(tmp_path))[0]
    assert back["outcomes"]["t1"]["ret"] == 0.0
    assert back["outcomes"].get("t3") is None


def test_sc015_direction_adjustment_is_symmetric():
    """空方告警的下跌 → ret_adj 為正；與多方逐項對稱。"""
    up = daily_with_gaps(closes=[100., 110.], dates=["2026-08-05", "2026-08-06"])
    down = daily_with_gaps(closes=[100., 90.], dates=["2026-08-05", "2026-08-06"])

    long_rec = make_record(alert_type="BULLISH_MSS", bar_time="2026-08-05 09:30:00")
    short_rec = make_record(alert_type="BEARISH_MSS", bar_time="2026-08-05 09:30:00")

    long_up = ao.compute_outcomes(long_rec, up, [1])["t1"]
    short_down = ao.compute_outcomes(short_rec, down, [1])["t1"]

    assert long_up["ret_adj"] == pytest.approx(0.10)
    assert short_down["ret_adj"] == pytest.approx(0.10)      # 跌 10% 對空方是對的
    assert ao.compute_outcomes(short_rec, up, [1])["t1"]["ret_adj"] == pytest.approx(-0.10)


# ---------------------------------------------------------------------------
# SC-013：冪等
# ---------------------------------------------------------------------------

def test_sc013_backfill_is_idempotent():
    daily = daily_linear(n=10, start="2026-08-05")
    rec = make_record(bar_time="2026-08-05 09:30:00", close=100.0)
    first = ao.compute_outcomes(rec, daily, [1, 3, 5])
    rec2 = dict(rec, outcomes=first)
    for _ in range(3):
        again = ao.compute_outcomes(rec2, daily, [1, 3, 5])
        assert again == first
        rec2 = dict(rec2, outcomes=again)


def test_sc013_filled_outcome_never_recomputed():
    """已回填者即使日線改變也不重算——避免資料修訂造成樣本悄悄漂移。"""
    rec = make_record(bar_time="2026-08-05 09:30:00", close=100.0)
    rec["outcomes"] = {"t1": {"date": "2026-08-06", "close": 110.0,
                              "ret": 0.10, "ret_adj": 0.10}}
    different = daily_with_gaps(closes=[100., 200.],
                                dates=["2026-08-05", "2026-08-06"])
    assert ao.compute_outcomes(rec, different, [1])["t1"]["ret"] == 0.10


def test_merge_record_is_idempotent_and_notified_only_upgrades():
    a = make_record(notified=True)
    b = make_record(notified=False)
    merged = ao.merge_record(a, b)
    assert merged["notified"] is True, "notified 不得由 True 降級為 False"
    assert ao.merge_record(merged, b) == merged

    c = ao.merge_record(make_record(notified=False), make_record(notified=True))
    assert c["notified"] is True, "notified 應可由 False 升級為 True"


def test_merge_preserves_immutable_fields():
    existing = make_record(close=100.0)
    incoming = make_record(close=999.0)
    assert ao.merge_record(existing, incoming)["close"] == 100.0


# ---------------------------------------------------------------------------
# 儲存層：分片、排序、原子性、零變更即零寫入
# ---------------------------------------------------------------------------

def test_upsert_shards_by_bar_time_month(tmp_path):
    ao.upsert_records(str(tmp_path), [
        make_record(bar_time="2026-08-31 13:00:00"),
        make_record(bar_time="2026-09-01 09:05:00"),
    ])
    assert sorted(os.listdir(tmp_path)) == ["2026-08.jsonl", "2026-09.jsonl"]


def test_upsert_output_is_sorted_for_stable_diff(tmp_path):
    ao.upsert_records(str(tmp_path), [
        make_record(bar_time="2026-08-05 11:00:00", alert_type="BULLISH_BOS"),
        make_record(bar_time="2026-08-05 09:30:00", alert_type="BULLISH_MSS"),
    ])
    first = (tmp_path / "2026-08.jsonl").read_text(encoding="utf-8")
    # 反序再寫一次相同內容 → 檔案內容必須逐位元相同（否則 diff 會出現假變更）
    ao.upsert_records(str(tmp_path), [
        make_record(bar_time="2026-08-05 09:30:00", alert_type="BULLISH_MSS"),
        make_record(bar_time="2026-08-05 11:00:00", alert_type="BULLISH_BOS"),
    ])
    assert (tmp_path / "2026-08.jsonl").read_text(encoding="utf-8") == first


def test_upsert_no_change_means_no_write(tmp_path):
    """零變更即零寫入——連 mtime 都不得改動（FR-009／SC-010 的儲存層落點）。"""
    rec = make_record()
    assert ao.upsert_records(str(tmp_path), [rec]) == 1
    path = tmp_path / "2026-08.jsonl"
    before = path.stat().st_mtime_ns
    assert ao.upsert_records(str(tmp_path), [rec]) == 0
    assert path.stat().st_mtime_ns == before


def test_upsert_same_key_stays_single_row(tmp_path):
    for _ in range(5):
        ao.upsert_records(str(tmp_path), [make_record()])
    assert len(ao.load_all(str(tmp_path))) == 1


def test_load_month_missing_file_returns_empty(tmp_path):
    assert ao.load_month(str(tmp_path), "2026-08") == []
    assert ao.load_all(str(tmp_path / "nope")) == []


# ---------------------------------------------------------------------------
# SC-012：回填不對外請求
# ---------------------------------------------------------------------------

def test_sc012_backfill_makes_no_outbound_request(tmp_path, monkeypatch):
    """
    回填只讀既有日線；任何對外請求即測試失敗。

    以 socket 層封鎖驗證——比 mock 特定函式更嚴格，繞道也抓得到。
    """
    import socket

    ao.upsert_records(str(tmp_path), [make_record(bar_time="2026-08-05 09:30:00")])

    def _blocked(*args, **kwargs):
        raise AssertionError("回填過程發出了對外連線——違反 FR-012")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    daily = daily_linear(n=10, start="2026-08-05")
    changed = ao.backfill(str(tmp_path), [1, 3, 5], lambda t, tf: daily)
    assert changed == 1
    assert ao.load_all(str(tmp_path))[0]["outcomes"]["t5"] is not None


def test_backfill_missing_data_does_not_block_others(tmp_path):
    """某標的資料缺漏時，其餘標的仍須完成回填。"""
    ao.upsert_records(str(tmp_path), [
        make_record(ticker="GOOD.TW", bar_time="2026-08-05 09:30:00"),
        make_record(ticker="BAD.TW", bar_time="2026-08-05 09:30:00"),
    ])
    daily = daily_linear(n=10, start="2026-08-05")

    def loader(ticker, timeframe):
        if ticker == "BAD.TW":
            raise RuntimeError("日線表不存在")
        return daily

    ao.backfill(str(tmp_path), [1], loader)
    by_ticker = {r["ticker"]: r for r in ao.load_all(str(tmp_path))}
    assert by_ticker["GOOD.TW"]["outcomes"]["t1"] is not None
    assert by_ticker["BAD.TW"]["outcomes"].get("t1") is None


# ---------------------------------------------------------------------------
# SC-008 / SC-017：分群統計
# ---------------------------------------------------------------------------

def test_sc008_groups_by_alert_type_and_timeframe(tmp_path):
    records = (records_for_summary(3, alert_type="BULLISH_MSS", timeframe="5m")
               + records_for_summary(2, alert_type="BULLISH_MSS", timeframe="daily"))
    summary = ao.summarize(records, min_samples=1, horizons=[1])
    pairs = set(zip(summary["alert_type"], summary["timeframe"]))
    assert pairs == {("BULLISH_MSS", "5m"), ("BULLISH_MSS", "daily")}
    counts = dict(zip(summary["timeframe"], summary["n_alerts"]))
    assert counts["5m"] == 3 and counts["daily"] == 2


def test_sc008_filters_by_timeframe_and_fingerprint():
    records = (records_for_summary(3, timeframe="5m")
               + records_for_summary(2, timeframe="daily"))
    only_5m = ao.summarize(records, min_samples=1, horizons=[1], timeframe="5m")
    assert set(only_5m["timeframe"]) == {"5m"}

    tagged = records_for_summary(2)
    for r in tagged:
        r["param_fingerprint"] = "sp20_fvg0_fl3_sn2_vm1.5_bv0_bvm1.5_bvp20"
    merged = records + tagged
    picked = ao.summarize(merged, min_samples=1, horizons=[1],
                          param_fingerprint="sp20_fvg0_fl3_sn2_vm1.5_bv0_bvm1.5_bvp20")
    assert int(picked["n_alerts"].sum()) == 2


def test_sc017_insufficient_sample_is_flagged_but_still_listed():
    """樣本不足的群**仍須列出**，但不得顯示統計量。"""
    summary = ao.summarize(records_for_summary(3), min_samples=20, horizons=[1])
    assert len(summary) == 1, "樣本不足不代表該群消失"
    row = summary.iloc[0]
    assert row["t1_n"] == 3
    assert bool(row["t1_sufficient"]) is False
    assert row["t1_median_adj"] is None and row["t1_win_rate"] is None


def test_sufficient_sample_reports_statistics():
    summary = ao.summarize(records_for_summary(20), min_samples=20, horizons=[1])
    row = summary.iloc[0]
    assert bool(row["t1_sufficient"]) is True
    assert row["t1_median_adj"] is not None
    assert row["t1_win_rate"] == pytest.approx(0.5)   # 正負交錯


def test_summarize_empty_input_returns_empty_frame():
    assert ao.summarize([], min_samples=1, horizons=[1]).empty


# ---------------------------------------------------------------------------
# SC-018：組態集中與 schema 驗證
# ---------------------------------------------------------------------------

def _ot(**kwargs):
    from config.config import OutcomeTrackingConfig
    return OutcomeTrackingConfig(**kwargs)


def test_sc018_defaults_are_off():
    assert _ot().enabled is False, "總開關必須預設關閉"


@pytest.mark.parametrize("kwargs, match", [
    ({"horizons": []}, "不得為空"),
    ({"horizons": [0, 1]}, "正整數"),
    ({"horizons": [3, 1]}, "遞增"),
    ({"horizons": [1, 1, 3]}, "遞增"),
    ({"min_samples": 0}, None),
    ({"log_dir": ""}, "不得為空"),
    ({"log_dir": "data"}, "data/"),
    ({"log_dir": "data/alerts"}, "data/"),
    ({"log_dir": "./data/alerts"}, "data/"),
])
def test_sc018_invalid_config_fails_fast(kwargs, match):
    with pytest.raises(Exception) as exc:
        _ot(**kwargs)
    if match:
        assert match in str(exc.value)


def test_sc018_log_dir_outside_data_is_accepted():
    assert _ot(log_dir="alert_log").log_dir == "alert_log"


def test_sc018_yaml_carries_the_block():
    """組態必須真的存在於 config.yaml，而非只靠 Pydantic 預設值。"""
    import yaml
    with open(os.path.join(REPO_ROOT, "config", "config.yaml"), encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    block = raw["alerts"]["outcome_tracking"]
    assert block["enabled"] is False
    assert not str(block["log_dir"]).startswith("data")


def test_sc018_no_hardcoded_constants_in_monitor():
    """回填視窗與門檻不得硬編碼於程式碼——必須走組態。"""
    src = open(os.path.join(REPO_ROOT, "monitor_signals.py"), encoding="utf-8").read()
    assert "conf.horizons" in src and "conf.log_dir" in src
    assert "[1, 3, 5]" not in src, "回填視窗不得硬編碼於 monitor"


# ---------------------------------------------------------------------------
# SC-019：靜態零引用（未來函數的入口必須焊死）
# ---------------------------------------------------------------------------

#: 訊號判定與回測路徑——**任何一個**引用本案紀錄即為未來函數的入口。
_FORBIDDEN_CONSUMERS = [
    "ladder_system.py", "backtester.py", "portfolio_backtester.py",
    "walk_forward.py", "optimizer.py", "monte_carlo.py", "performance.py",
    "trading_costs.py", "risk_gates.py",
    "run_backtest.py", "run_portfolio_backtest.py", "run_walk_forward.py",
    "run_optimization.py", "run_ablation.py", "run_b_segment.py",
]


@pytest.mark.parametrize("filename", _FORBIDDEN_CONSUMERS)
def test_sc019_signal_and_backtest_paths_never_reference_outcomes(filename):
    path = os.path.join(REPO_ROOT, filename)
    if not os.path.exists(path):
        pytest.skip(f"{filename} 不存在")
    src = open(path, encoding="utf-8").read()
    assert "alert_outcomes" not in src, (
        f"{filename} 引用了 alert_outcomes——該模組持有告警**發生之後**的價格，"
        f"進入訊號鏈即為未來函數的入口（spec 015 FR-021）"
    )
    assert "alert_log" not in src, f"{filename} 引用了 alert_log 紀錄路徑"


def test_sc019_module_does_not_import_signal_or_backtest_modules():
    """反向也不許：`alert_outcomes` 不得 import 訊號／回測模組。"""
    src = open(os.path.join(REPO_ROOT, "alert_outcomes.py"), encoding="utf-8").read()
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, re.MULTILINE)
    banned = {"monitor_signals", "backtester", "ladder_system",
              "portfolio_backtester", "walk_forward", "optimizer", "risk_gates"}
    assert not (set(imports) & banned), f"不當 import：{set(imports) & banned}"


# ---------------------------------------------------------------------------
# SC-020：安全（參數化查詢、無憑證入庫）
# ---------------------------------------------------------------------------

def test_sc020_no_string_concatenated_sql():
    """本案不新增 SQLite 表；回填僅經既有 safe_load_db_data。"""
    src = open(os.path.join(REPO_ROOT, "alert_outcomes.py"), encoding="utf-8").read()
    for keyword in ("SELECT", "INSERT", "CREATE TABLE", "sqlite3"):
        assert keyword not in src, f"alert_outcomes 不應含 SQL／DB 存取（{keyword}）"


def test_sc020_table_name_pattern_untouched():
    """
    本案**不新增 SQLite 表** → `TABLE_NAME_PATTERN` 不得為本案放寬。

    出現放寬的念頭即代表偏離 research.md D1 的設計。
    """
    import db_security
    assert db_security.TABLE_NAME_PATTERN.pattern == \
        r"^(stock|fut)_[a-zA-Z0-9_]+_(daily|5m)$"


def test_sc020_record_carries_no_credentials():
    forbidden = ("token", "secret", "password", "chat_id", "line_to",
                 "access_token", "credential")
    for field in ao.RECORD_FIELDS:
        assert not any(f in field.lower() for f in forbidden), \
            f"欄位 '{field}' 疑似憑證／收件識別（FR-023）"


def test_sc020_persisted_record_has_no_extra_fields(tmp_path):
    """落地的 JSON 也不得夾帶白名單外的鍵。"""
    ao.upsert_records(str(tmp_path), [make_record()])
    line = (tmp_path / "2026-08.jsonl").read_text(encoding="utf-8").strip()
    assert set(json.loads(line).keys()) == set(ao.RECORD_FIELDS)


# ---------------------------------------------------------------------------
# SC-016：呈現層標示（非策略績效、不與回測 KPI 並列）
# ---------------------------------------------------------------------------

def _outcome_tab_source() -> str:
    src = open(os.path.join(REPO_ROOT, "app.py"), encoding="utf-8").read()
    marker = "with tab_outcome:"
    assert marker in src, "app.py 缺少事後表現分頁"
    return src[src.index(marker):]


def test_sc016_tab_declares_not_strategy_performance():
    block = _outcome_tab_source()
    assert "不是策略績效" in block
    for token in ("手續費", "滑價", "出場規則", "樣本外"):
        assert token in block, f"標示缺少「{token}」的說明"


def test_sc016_tab_shows_no_backtest_kpi():
    """與回測 KPI 並列本身就是一種宣稱——本頁不得出現任何 KPI 欄位。"""
    block = _outcome_tab_source()
    for kpi in ("Sharpe", "Sortino", "Calmar", "CAGR", "Profit Factor",
                "最大回撤", "總報酬", "淨值"):
        assert kpi not in block, f"事後表現分頁不得呈現回測 KPI：{kpi}"


def test_sc016_tab_delegates_computation_to_module():
    """UI 不得內嵌演算法邏輯（CLAUDE.md 規則）——統計一律走 summarize()。"""
    block = _outcome_tab_source()
    assert "alert_outcomes.summarize(" in block
    assert "alert_outcomes.load_all(" in block


# ---------------------------------------------------------------------------
# 排程接線（T018）：權限、僅在有變更時提交、[skip ci]、不阻斷推播
# ---------------------------------------------------------------------------

def _scheduler_yaml():
    import yaml
    path = os.path.join(REPO_ROOT, ".github", "workflows", "alert_scheduler.yml")
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh), fh


def test_scheduler_declares_contents_write():
    """提交紀錄需寫入權限；預設為唯讀，故必須顯式宣告（Assumptions A-9）。"""
    data, _ = _scheduler_yaml()
    assert data.get("permissions", {}).get("contents") == "write"


def test_scheduler_commit_step_is_guarded_and_non_blocking():
    path = os.path.join(REPO_ROOT, ".github", "workflows", "alert_scheduler.yml")
    src = open(path, encoding="utf-8").read()
    step = src[src.index("提交事後表現紀錄"):]
    assert "git status --porcelain alert_log/" in step, "必須僅在有變更時提交"
    assert "[skip ci]" in step, "缺 [skip ci] 會讓每則告警都觸發 tests.yml"
    assert "continue-on-error: true" in step, "提交失敗不得阻斷推播（FR-010）"
    assert "--rebase" in step, "push 前須 rebase 以處理競態"


def test_scheduler_commit_step_runs_after_alerting():
    """提交步驟必須在推播之後——順序反了會讓提交失敗阻斷該輪通知。"""
    data, _ = _scheduler_yaml()
    names = [s.get("name", "") for s in data["jobs"]["run-monitor"]["steps"]]
    assert names.index("執行實時訊號檢測與推播") < names.index("提交事後表現紀錄")


def test_log_dir_is_not_gitignored():
    """
    紀錄目錄若被 .gitignore 涵蓋，提交會靜默不發生、樣本無聲消失。

    這正是 log_dir 不得置於 data/ 之下的理由（該目錄整體被忽略）。
    """
    import subprocess
    probe = os.path.join(REPO_ROOT, "alert_log", "__ignore_probe__.jsonl")
    os.makedirs(os.path.dirname(probe), exist_ok=True)
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    try:
        result = subprocess.run(["git", "check-ignore", probe],
                                cwd=REPO_ROOT, capture_output=True)
        assert result.returncode != 0, "alert_log/ 被 .gitignore 涵蓋——紀錄不會進版本庫"
    finally:
        os.remove(probe)
        try:
            os.rmdir(os.path.dirname(probe))
        except OSError:
            pass
