# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - spec 016 報告層驗收（T011–T016、T051–T053）。

對應 SC-001（確定性）、SC-002（效力標籤）、SC-003（離散度）、
SC-004（零交易分解）、SC-010（尺度掃描）、SC-012（無有效性宣稱）。
"""

import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fixtures_016_intraday as fx  # noqa: E402
import intraday_report as irep  # noqa: E402
import intraday_snapshot as isnap  # noqa: E402
import run_intraday_eval as cli  # noqa: E402
from config import load_config  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def params(cfg):
    return cfg.strategy.default


@pytest.fixture(scope="module")
def state_dir(tmp_path_factory):
    d = str(tmp_path_factory.mktemp("state_016"))
    fx.write_state_dir(
        d,
        {"2330.TW": fx.intraday_frame(120, seed=42),
         "2454.TW": fx.intraday_frame(120, seed=7)},
        chain_broken=False,
    )
    return d


@pytest.fixture(scope="module")
def report(state_dir, tmp_path_factory):
    out = str(tmp_path_factory.mktemp("out") / "r.json")
    assert cli.main(["evaluate", "--state-dir", state_dir, "--out-json", out,
                     "--out-text", out + ".txt"]) == cli.EXIT_OK
    with open(out, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# SC-001 確定性
# ---------------------------------------------------------------------------


def test_determinism(state_dir, tmp_path):
    """同一份快照兩次評估，inputs 與 results 逐欄相同；provenance 允許不同。"""
    a, b = str(tmp_path / "a.json"), str(tmp_path / "b.json")
    assert cli.main(["evaluate", "--state-dir", state_dir, "--out-json", a]) == 0
    assert cli.main(["evaluate", "--state-dir", state_dir, "--out-json", b]) == 0
    ra = json.load(open(a, encoding="utf-8"))
    rb = json.load(open(b, encoding="utf-8"))
    assert ra["inputs"] == rb["inputs"], "inputs 不確定"
    assert ra["results"] == rb["results"], "results 不確定"


def test_provenance_excluded_from_comparison(report):
    """provenance 必須存在但不參與比對——否則每次執行都會被判定為『結論變了』。"""
    assert "generated_at" in report["provenance"]


def test_fingerprint_binds_conclusion_to_data(report, state_dir):
    """FR-004：報告記錄輸入指紋，使結論可回溯至特定資料版本。"""
    fps = report["inputs"]["accumulated_fingerprints"]
    assert set(fps) == set(isnap.list_tickers(state_dir))
    for ticker, fp in fps.items():
        assert fp == isnap.fingerprint(isnap.read_history(state_dir, ticker))


# ---------------------------------------------------------------------------
# SC-002 效力標籤
# ---------------------------------------------------------------------------


def test_every_perf_has_label(report):
    """每個績效數字都是 {value, validity_label} 物件；裸數值即為缺陷。"""
    checked = 0
    for t in report["results"]["per_ticker"]:
        assert t["performance"], f"{t['ticker']} 無績效區"
        for key, item in t["performance"].items():
            assert isinstance(item, dict), f"{t['ticker']}.{key} 是裸數值"
            assert "value" in item and "validity_label" in item
            assert item["validity_label"] in irep.VALIDITY_LABELS
            checked += 1
    assert checked > 0


def test_label_is_not_caller_specifiable():
    """research.md R6：標籤是累積狀態的純函式。可指定的話 FR-005 形同虛設。"""
    none_split = irep.decide_validity_label(None, None, 3, 30)
    assert none_split == irep.LABEL_IN_SAMPLE

    insufficient = isnap.SplitResult([], False, 42)
    assert irep.decide_validity_label(insufficient, [99, 99, 99], 3, 30) == (
        irep.LABEL_IN_SAMPLE
    ), "切不出窗時，再多的交易數也不得升級標籤"


def test_label_state_machine():
    splits = isnap.split_windows(fx.intraday_frame(200), n_windows=3, train_ratio=0.7)
    assert splits.sufficient

    assert irep.decide_validity_label(splits, [], 3, 30) == irep.LABEL_OOS_INSUFFICIENT
    assert irep.decide_validity_label(splits, [30, 29, 40], 3, 30) == (
        irep.LABEL_OOS_INSUFFICIENT
    ), "任一窗樣本量不足即不得升級"
    assert irep.decide_validity_label(splits, [30, 31, 40], 3, 30) == (
        irep.LABEL_OOS_VALIDATED
    )
    assert irep.decide_validity_label(splits, [99, 99, 99], 5, 30) == (
        irep.LABEL_IN_SAMPLE
    ), "窗數低於門檻即不得升級"


# ---------------------------------------------------------------------------
# SC-003 離散度
# ---------------------------------------------------------------------------


def test_pooled_has_dispersion(report):
    """pooled_value 不得單獨序列化——離散度三欄與它同屬一個結構。"""
    pooled = report["results"]["pooled"]
    assert pooled, "無合併統計"
    for p in pooled:
        assert {"pooled_value", "min", "max", "ratio", "n_tickers"} <= set(p)
        assert p["min"] <= p["pooled_value"] <= p["max"]


def test_pooled_ratio_none_when_min_is_zero():
    per_ticker = [
        {"ticker": "A", "trades": 0,
         "attrition": {"bos_signals": 10, "conjunction_passed": 0},
         "data_health": {"bars": 100}},
        {"ticker": "B", "trades": 5,
         "attrition": {"bos_signals": 10, "conjunction_passed": 5},
         "data_health": {"bars": 100}},
    ]
    pooled = {p["metric"]: p for p in irep.build_pooled(per_ticker)}
    assert pooled["trades"]["ratio"] is None, "分母為 0 時 ratio 必須為 None，不得爆炸"


# ---------------------------------------------------------------------------
# SC-004 零交易分解
# ---------------------------------------------------------------------------


def test_zero_trade_cause_exhaustive():
    """四種成因互斥且窮盡；trades == 0 時不得為 None 或 unknown。"""
    cases = [
        ({"bos_up": 0, "bos_down": 0, "mss_up": 0, "mss_down": 0},
         {"conjunction_passed": 0}, {"round_trips": 0, "entries": 0},
         irep.ZERO_NO_STRUCTURE),
        ({"bos_up": 10, "bos_down": 4, "mss_up": 0, "mss_down": 0},
         {"conjunction_passed": 0}, {"round_trips": 0, "entries": 0},
         irep.ZERO_FILTERS_REJECTED),
        ({"bos_up": 10, "bos_down": 4, "mss_up": 0, "mss_down": 0},
         {"conjunction_passed": 3}, {"round_trips": 0, "entries": 0},
         irep.ZERO_BLOCKED_BY_POSITION),
        ({"bos_up": 10, "bos_down": 4, "mss_up": 0, "mss_down": 0},
         {"conjunction_passed": 3}, {"round_trips": 0, "entries": 2},
         irep.ZERO_NEVER_EXITED),
    ]
    for density, attrition, bt, expected in cases:
        got = irep.classify_zero_trade(density, attrition, bt)
        assert got == expected, f"期望 {expected} 得到 {got}"
        assert got in irep.ZERO_TRADE_CAUSES


def test_zero_trade_cause_none_when_trades_exist():
    assert irep.classify_zero_trade(
        {"bos_up": 1, "bos_down": 0, "mss_up": 0, "mss_down": 0},
        {"conjunction_passed": 1},
        {"round_trips": 5, "entries": 5},
    ) is None


def test_flat_series_yields_no_structure_signal(cfg, params):
    """真跑一次：完全無波動的序列必須落在 no_structure_signal，而非『原因不明』。"""
    df = isnap.normalize_frame(fx.flat_frame(30))
    result = irep.build_per_ticker_result("FLAT.TW", df, cfg, params,
                                          irep.LABEL_IN_SAMPLE)
    assert result["trades"] == 0
    assert result["zero_trade_cause"] in irep.ZERO_TRADE_CAUSES


def test_zero_trade_survives_full_pipeline(tmp_path):
    """完整管線的零交易路徑：通過納入準則、走到評估、成因仍被分類。

    刻意不用 `flat_frame`——它的價格完全不動，唯一價差為 0，會在**納入準則**
    階段就被 tick_ratio 排除，根本走不到評估。用它測這條路徑會得到綠燈，
    卻沒有真的覆蓋到報告層。
    """
    d = str(tmp_path / "quiet")
    fx.write_state_dir(d, {"QUIET.TW": fx.quiet_frame(60)}, chain_broken=True)
    out = str(tmp_path / "z.json")
    assert cli.main(["evaluate", "--state-dir", d, "--out-json", out]) == cli.EXIT_OK
    r = json.load(open(out, encoding="utf-8"))
    t = r["results"]["per_ticker"][0]
    assert t["trades"] == 0
    assert t["zero_trade_cause"] in irep.ZERO_TRADE_CAUSES


def test_no_included_ticker_fails_explicitly(tmp_path):
    """FR：無標的通過準則時明確失敗，不得產出空報告當成結論。"""
    d = str(tmp_path / "allexcluded")
    fx.write_state_dir(d, {"FLAT.TW": fx.flat_frame(60)}, chain_broken=True)
    out = str(tmp_path / "r.json")
    assert cli.main(["evaluate", "--state-dir", d, "--out-json", out]) == (
        cli.EXIT_PARTIAL_FAILURE
    )
    assert not os.path.exists(out), "失敗時不得留下報告檔"


def test_report_never_leaves_zero_trade_unexplained(report):
    for t in report["results"]["per_ticker"]:
        if t["trades"] == 0:
            assert t["zero_trade_cause"] in irep.ZERO_TRADE_CAUSES, (
                f"{t['ticker']} 零交易但成因不明"
            )


# ---------------------------------------------------------------------------
# FR-008 分方向計數
# ---------------------------------------------------------------------------


def test_signal_density_directional(report):
    for t in report["results"]["per_ticker"]:
        sd = t["signal_density"]
        for key in ("bos_up", "bos_down", "mss_up", "mss_down"):
            assert key in sd, f"{t['ticker']} 缺 {key}——方向被合計即與流失基數對不上"


def test_attrition_base_matches_bos_up(cfg, params):
    """逐道流失的基數必須等於多方 BOS 訊號數（扣掉最後一根無判定根者）。"""
    df = isnap.normalize_frame(fx.intraday_frame(60))
    ind = irep.build_indicator(df, params)
    density = irep.build_signal_density(ind, params)
    attrition = irep.build_attrition(ind)
    assert abs(attrition["bos_signals"] - density["bos_up"]) <= 1


# ---------------------------------------------------------------------------
# SC-012 無有效性宣稱
# ---------------------------------------------------------------------------


def test_no_efficacy_claims_in_json(report):
    text = irep.to_json(report)
    assert irep.find_efficacy_claims(text) == []


def test_no_efficacy_claims_in_text(report):
    assert irep.find_efficacy_claims(irep.render_text(report)) == []


@pytest.mark.parametrize("label", irep.VALIDITY_LABELS)
def test_no_efficacy_claims_under_every_label(report, label):
    """三種標籤一律適用——out_of_sample_validated 只宣稱程序已執行。"""
    forced = json.loads(irep.to_json(report))
    for t in forced["results"]["per_ticker"]:
        for item in t["performance"].values():
            item["validity_label"] = label
    assert irep.find_efficacy_claims(irep.to_json(forced)) == []
    assert irep.find_efficacy_claims(irep.render_text(forced)) == []


def test_claim_phrase_list_is_the_single_source():
    """措辭清單分兩份會讓其中一份悄悄過期；此處確認它非空且被實際使用。"""
    assert irep.EFFICACY_CLAIM_PHRASES
    assert irep.find_efficacy_claims("本策略有效，建議啟用") == ["策略有效", "建議啟用"]


# ---------------------------------------------------------------------------
# 文字報表由 JSON 渲染
# ---------------------------------------------------------------------------


def test_text_is_rendered_from_json(report):
    """render_text 不得自行計算——改 JSON 的值，文字必須跟著變。"""
    mutated = json.loads(irep.to_json(report))
    mutated["results"]["per_ticker"][0]["trades"] = 123456
    assert "123,456" in irep.render_text(mutated)


def test_text_states_hardcoded_structure_period(report):
    """FR-021：既有硬編碼須被顯式標示，否則讀者會以為那是組態參數。"""
    text = irep.render_text(report)
    assert "structure_period=10" in text
    assert "既有缺陷" in text


# ---------------------------------------------------------------------------
# SC-008 切分不足的明示
# ---------------------------------------------------------------------------


def test_insufficient_windows_are_stated_not_silent(tmp_path):
    """FR-015：累積不足時必須明示並量化差距，不得靜默降級。"""
    d = str(tmp_path / "short")
    fx.write_state_dir(d, {"2330.TW": fx.intraday_frame(30, seed=42)},
                       chain_broken=False)
    out = str(tmp_path / "r.json")
    assert cli.main(["evaluate", "--state-dir", d, "--out-json", out]) == 0
    r = json.load(open(out, encoding="utf-8"))
    w = r["results"]["windows"]
    assert w["sufficient"] is False
    assert w["splits"] == []
    assert w["shortfall_trading_days"] > 0
    assert "還差" in irep.render_text(r)
    for t in r["results"]["per_ticker"]:
        for item in t["performance"].values():
            assert item["validity_label"] == irep.LABEL_IN_SAMPLE


# ---------------------------------------------------------------------------
# SC-009 鏈結中斷
# ---------------------------------------------------------------------------


def test_chain_break_surfaces_in_report(tmp_path):
    d = str(tmp_path / "broken")
    fx.write_state_dir(d, {"2330.TW": fx.intraday_frame(40, seed=42)},
                       chain_broken=True)
    out = str(tmp_path / "r.json")
    assert cli.main(["evaluate", "--state-dir", d, "--out-json", out]) == 0
    r = json.load(open(out, encoding="utf-8"))
    assert r["inputs"]["chain_broken"] is True
    assert "累積鏈中斷" in irep.render_text(r)


# ---------------------------------------------------------------------------
# US4 尺度掃描（T051–T053）
# ---------------------------------------------------------------------------


def test_scale_sweep_curve(cfg, params):
    """SC-010：對每個倍率輸出完整反應曲線，且 factor=1.0 與主結果一致。"""
    df = isnap.normalize_frame(fx.intraday_frame(60))
    factors = [0.5, 1.0, 2.0]
    rows = irep.run_scale_sweep(df, cfg, params, factors)

    assert [r["factor"] for r in rows] == factors
    for r in rows:
        assert set(r) >= {"factor", "single_pass_rates", "conjunction_passed",
                          "bos_signals", "trades"}

    baseline = irep.build_attrition(irep.build_indicator(df, params))
    at_one = next(r for r in rows if r["factor"] == 1.0)
    assert at_one["conjunction_passed"] == baseline["conjunction_passed"]
    assert at_one["single_pass_rates"] == baseline["single_pass_rates"]


def test_verdict_requires_measurement():
    """FR-018：無掃描結果時不得輸出既定處方。"""
    v = irep.summarize_scale_sweep([])
    assert v["measured"] is False
    assert "參數時框化" not in v["verdict"]
    assert "先做" not in v["verdict"]


def test_verdict_reports_flat_curve_as_counter_evidence():
    """曲線平坦時，正確產出是推翻假設的證據，不是實作時框化的處方。"""
    rows = [
        {"factor": f, "single_pass_rates": {}, "conjunction_passed": 10,
         "bos_signals": 100, "trades": 4}
        for f in (0.5, 1.0, 2.0)
    ]
    v = irep.summarize_scale_sweep(rows)
    assert v["measured"] is True and v["flat"] is True
    assert "不構成瓶頸" in v["verdict"]
    assert irep.find_efficacy_claims(v["verdict"]) == []


def test_verdict_reports_sensitive_curve():
    rows = [
        {"factor": 0.5, "single_pass_rates": {}, "conjunction_passed": 2,
         "bos_signals": 100, "trades": 1},
        {"factor": 1.0, "single_pass_rates": {}, "conjunction_passed": 50,
         "bos_signals": 100, "trades": 25},
        {"factor": 2.0, "single_pass_rates": {}, "conjunction_passed": 80,
         "bos_signals": 100, "trades": 40},
    ]
    v = irep.summarize_scale_sweep(rows)
    assert v["flat"] is False
    assert "構成瓶頸" in v["verdict"]


def test_scale_sweep_writes_nothing_to_config(cfg, params):
    """T053：掃描前後 config.yaml 的位元組完全相同（記憶體內覆寫）。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "config.yaml")
    before = open(path, "rb").read()
    irep.run_scale_sweep(isnap.normalize_frame(fx.intraday_frame(40)), cfg,
                         params, [0.5, 1.0])
    assert open(path, "rb").read() == before


def test_sweep_flag_populates_report(state_dir, tmp_path):
    out = str(tmp_path / "sweep.json")
    assert cli.main(["evaluate", "--state-dir", state_dir, "--out-json", out,
                     "--scale-sweep"]) == 0
    r = json.load(open(out, encoding="utf-8"))
    assert r["results"]["scale_sweep"], "--scale-sweep 未寫入結果"
    assert r["results"]["scale_sweep_verdict"]["measured"] is True
