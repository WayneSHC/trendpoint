# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - spec 016 快照、累積與切分的驗收（T007/T009 + T037–T042）。

對應 SC-007（合併無重複無倒錯）、SC-008（窗口切分）、SC-009（鏈結中斷回報）。
全部離線、確定性，不觸網路。
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fixtures_016_intraday as fx  # noqa: E402
import intraday_snapshot as isnap  # noqa: E402
from intraday_snapshot import SnapshotError  # noqa: E402


# ---------------------------------------------------------------------------
# 正規化與指紋（T007）
# ---------------------------------------------------------------------------


def test_normalize_is_idempotent():
    df = fx.intraday_frame(5)
    once = isnap.normalize_frame(df)
    twice = isnap.normalize_frame(once)
    pd.testing.assert_frame_equal(once, twice)


def test_fingerprint_ignores_float_noise():
    """1e-13 級雜訊必須被正規化抹平——否則每週合併都會報出數百筆假衝突。"""
    df = fx.intraday_frame(5)
    noisy = df.copy()
    for col in ("open", "high", "low", "close"):
        noisy[col] = noisy[col] + 1e-13
    assert isnap.fingerprint(isnap.normalize_frame(df)) == isnap.fingerprint(
        isnap.normalize_frame(noisy)
    )


def test_fingerprint_ignores_column_order():
    df = fx.intraday_frame(5)
    shuffled = df[["volume", "close", "low", "high", "open"]]
    assert isnap.fingerprint(isnap.normalize_frame(df)) == isnap.fingerprint(
        isnap.normalize_frame(shuffled)
    )


def test_fingerprint_detects_real_change():
    """真正的資料修正必須改變指紋——否則指紋無法區分資料版本。"""
    df = fx.intraday_frame(5)
    changed = df.copy()
    changed.iloc[0, changed.columns.get_loc("close")] += 0.5
    assert isnap.fingerprint(isnap.normalize_frame(df)) != isnap.fingerprint(
        isnap.normalize_frame(changed)
    )


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda d: d.iloc[0:0], "空快照"),
        (lambda d: d.drop(columns=["volume"]), "缺少必要欄位"),
        (lambda d: d.assign(close=-1.0), "非正價格"),
        (lambda d: d.assign(high=d["low"] - 1.0), "high < low"),
    ],
)
def test_normalize_fails_fast(mutate, expected):
    df = fx.intraday_frame(3)
    with pytest.raises(SnapshotError) as exc:
        isnap.normalize_frame(mutate(df))
    assert expected in str(exc.value)


def test_duplicate_timestamps_keep_first():
    df = fx.intraday_frame(2)
    dup = pd.concat([df, df.iloc[:3]]).sort_index()
    out = isnap.normalize_frame(dup)
    assert not out.index.has_duplicates
    assert len(out) == len(df)


# ---------------------------------------------------------------------------
# CSV 契約（T009）
# ---------------------------------------------------------------------------


def test_csv_roundtrip_is_byte_identical(tmp_path):
    df = isnap.normalize_frame(fx.intraday_frame(5))
    state = str(tmp_path)
    isnap.write_history(state, "2330.TW", df)
    first = open(isnap.history_path(state, "2330.TW"), "rb").read()

    reread = isnap.read_history(state, "2330.TW")
    isnap.write_history(state, "2330.TW", reread)
    second = open(isnap.history_path(state, "2330.TW"), "rb").read()

    assert first == second, "write → read → write 非位元組相同，指紋會漂移"
    assert isnap.fingerprint(df) == isnap.fingerprint(reread)


def test_csv_has_fixed_decimals(tmp_path):
    df = isnap.normalize_frame(fx.intraday_frame(1))
    isnap.write_history(str(tmp_path), "2330.TW", df)
    text = open(isnap.history_path(str(tmp_path), "2330.TW"), encoding="utf-8").read()
    header, first_row = text.split("\n")[0], text.split("\n")[1]
    assert header == "datetime,open,high,low,close,volume"
    for field in first_row.split(",")[1:5]:
        assert len(field.split(".")[1]) == isnap.PRICE_DECIMALS


def test_read_history_rejects_disordered_index(tmp_path):
    df = isnap.normalize_frame(fx.intraday_frame(2))
    isnap.write_history(str(tmp_path), "2330.TW", df)
    path = isnap.history_path(str(tmp_path), "2330.TW")
    lines = open(path, encoding="utf-8").read().strip().split("\n")
    lines[1], lines[2] = lines[2], lines[1]      # 外部改壞：把兩列對調
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    with pytest.raises(SnapshotError):
        isnap.read_history(str(tmp_path), "2330.TW")


def test_read_history_missing_returns_none(tmp_path):
    assert isnap.read_history(str(tmp_path), "9999.TW") is None


def test_list_tickers_sorted(tmp_path):
    state = str(tmp_path)
    for t in ("2454.TW", "2330.TW", "0050.TW"):
        isnap.write_history(state, t, isnap.normalize_frame(fx.intraday_frame(1)))
    assert isnap.list_tickers(state) == ["0050.TW", "2330.TW", "2454.TW"]


# ---------------------------------------------------------------------------
# 合併（T037/T038）
# ---------------------------------------------------------------------------


def test_merge_no_dup_no_disorder():
    """SC-007：兩份重疊快照合併後重複列 0、時序倒錯 0。"""
    earlier, later = fx.overlapping_pair(total_days=30, overlap_days=20)
    merged, event = isnap.merge_history(earlier, later)

    assert not merged.index.has_duplicates
    assert merged.index.is_monotonic_increasing
    assert event.overlap_bars > 0, "此 fixture 應有重疊，否則測不到去重"
    assert event.bars_after == len(merged)
    assert event.bars_added == len(merged) - len(earlier)


def test_merge_first_writer_wins():
    """FR-014：重疊處保留既有值，新值被捨棄，且衝突被計數而非吞掉。"""
    earlier, later = fx.overlapping_pair(total_days=30, overlap_days=20)
    conflicting = fx.with_conflicts(later, n_conflicts=3, delta=0.5)

    merged, event = isnap.merge_history(earlier, conflicting)

    assert event.conflicts == 3, "衝突未被完整計數"
    assert event.conflict_first_ts is not None
    assert event.conflict_last_ts is not None

    # 重疊處必須是 earlier 的值，不是 conflicting 的值。
    overlap = earlier.index.intersection(conflicting.index)
    pd.testing.assert_frame_equal(
        merged.loc[overlap], isnap.normalize_frame(earlier).loc[overlap]
    )


def test_merge_into_empty_is_chain_start():
    df = fx.intraday_frame(5)
    merged, event = isnap.merge_history(None, df)
    assert event.bars_before == 0
    assert event.overlap_bars == 0
    assert event.conflicts == 0
    assert len(merged) == len(isnap.normalize_frame(df))


def test_merge_identical_snapshots_adds_nothing():
    df = fx.intraday_frame(5)
    merged, event = isnap.merge_history(df, df)
    assert event.bars_added == 0
    assert event.conflicts == 0
    assert event.overlap_bars == len(merged)


# ---------------------------------------------------------------------------
# 斷裂（T039/T042）
# ---------------------------------------------------------------------------


def test_gap_kind_enum_only():
    """T042：kind 僅取三個列舉值，下游不得依賴人類可讀字串。"""
    df = fx.with_gap(fx.intraday_frame(40), skip_days=10)
    gaps = isnap.detect_gaps(df)
    assert gaps, "此 fixture 應偵測到斷裂"
    for g in gaps:
        assert g.kind in isnap.GAP_KINDS


def test_weekend_is_not_a_gap():
    """週末不得被當成斷裂——否則每週都會把序列切碎。"""
    df = fx.intraday_frame(20)
    assert isnap.detect_gaps(df) == []


def test_schedule_lapse_detected():
    df = fx.with_gap(fx.intraday_frame(40), skip_days=10)
    gaps = isnap.detect_gaps(df)
    assert any(g.kind == isnap.GAP_SCHEDULE_LAPSE for g in gaps)
    assert all(g.missing_trading_days > 0 for g in gaps)


def test_chain_restart_gap_kind():
    g = isnap.chain_restart_gap("2026-01-05 09:00:00")
    assert g.kind == isnap.GAP_CHAIN_RESTART


def test_chain_state_roundtrip(tmp_path):
    """SC-009 的儲存層：鏈結中斷的事實必須能被寫出並讀回。"""
    state = str(tmp_path)
    isnap.write_chain_state(
        state,
        chain_origin="2026-01-05 09:00:00",
        chain_broken=True,
        tickers={"2330.TW": {"fingerprint": "abc", "bars": 10}},
    )
    loaded = isnap.read_chain_state(state)
    assert loaded["chain_broken"] is True
    assert loaded["chain_origin"] == "2026-01-05 09:00:00"
    assert loaded["tickers"]["2330.TW"]["bars"] == 10


def test_chain_state_absent_returns_none(tmp_path):
    """取不回前次狀態 = 鏈結起點。回傳 None 讓呼叫端**必須**顯式處理。"""
    assert isnap.read_chain_state(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# 窗口切分（T040/T041）
# ---------------------------------------------------------------------------


def test_window_insufficient_reports_shortfall():
    """SC-008 前半：長度不足時不得回傳部分切分。"""
    df = fx.intraday_frame(20)
    res = isnap.split_windows(df, n_windows=3, train_ratio=0.7)
    assert res.sufficient is False
    assert res.splits == [], "長度不足時回傳半套結果會讓下游以為切分成功"
    assert res.shortfall_trading_days > 0


def test_window_splits_disjoint_when_sufficient():
    """SC-008 後半：測試窗兩兩不重疊、訓練嚴格早於測試。"""
    df = fx.intraday_frame(200)
    res = isnap.split_windows(df, n_windows=3, train_ratio=0.7)
    assert res.sufficient is True
    assert len(res.splits) == 3
    for a, b in zip(res.splits, res.splits[1:]):
        assert b.test_start > a.test_end, "測試窗重疊"
    for s in res.splits:
        assert s.train_end < s.test_start
        assert s.test_bars > 0


def test_window_does_not_cross_gap():
    """FR-016：窗口不得跨越非假日型斷裂。

    在一段長序列中段挖掉 30 個交易日；切分只能落在較長的那一段內，
    故所有窗口的邊界都必須位於該段的日期範圍內。
    """
    df = fx.with_gap(fx.intraday_frame(260), skip_days=30)
    gaps = isnap.detect_gaps(df)
    assert gaps, "此 fixture 應有斷裂"
    res = isnap.split_windows(df, n_windows=3, train_ratio=0.7)
    if not res.sufficient:
        pytest.skip("此組態下最長連續段不足以切分")
    gap_start = pd.Timestamp(gaps[0].start_ts).date()
    gap_end = pd.Timestamp(gaps[0].end_ts).date()
    for s in res.splits:
        for boundary in (s.train_start, s.train_end, s.test_start, s.test_end):
            d = pd.Timestamp(boundary).date()
            assert not (gap_start < d < gap_end), f"窗口邊界 {boundary} 落在斷裂內"


def test_split_rejects_bad_params():
    df = fx.intraday_frame(100)
    with pytest.raises(SnapshotError):
        isnap.split_windows(df, n_windows=0, train_ratio=0.7)
    with pytest.raises(SnapshotError):
        isnap.split_windows(df, n_windows=3, train_ratio=1.0)


# ---------------------------------------------------------------------------
# accumulate 子命令（T048）—— 跨執行的累積在離線模式下的行為
# ---------------------------------------------------------------------------


def _write_offline_csv(csv_dir, ticker, df):
    os.makedirs(csv_dir, exist_ok=True)
    path = os.path.join(csv_dir, f"{ticker.replace('.', '_')}_5m.csv")
    isnap.normalize_frame(df).to_csv(path)
    return path


def test_accumulate_offline_builds_chain(tmp_path):
    """兩次 accumulate：第一次標記鏈結起點，第二次續接且根數增加。"""
    import run_intraday_eval as cli

    state = str(tmp_path / "state")
    csv_dir = str(tmp_path / "csv")
    earlier, later = fx.overlapping_pair(total_days=30, overlap_days=20)

    _write_offline_csv(csv_dir, "2330.TW", earlier)
    assert cli.main(["accumulate", "--tickers", "2330.TW",
                     "--state-dir", state, "--offline-csv-dir", csv_dir]) == 0
    state1 = isnap.read_chain_state(state)
    assert state1["chain_broken"] is True, "首次執行必須標記鏈結起點"
    bars1 = state1["tickers"]["2330.TW"]["bars"]
    assert any(g["kind"] == isnap.GAP_CHAIN_RESTART
               for g in state1["tickers"]["2330.TW"]["gaps"])

    _write_offline_csv(csv_dir, "2330.TW", later)
    assert cli.main(["accumulate", "--tickers", "2330.TW",
                     "--state-dir", state, "--offline-csv-dir", csv_dir]) == 0
    state2 = isnap.read_chain_state(state)
    assert state2["chain_broken"] is False, "取回前次後鏈結不應再標記中斷"
    assert state2["chain_origin"] == state1["chain_origin"], "起算點必須沿用"
    assert state2["tickers"]["2330.TW"]["bars"] > bars1, "第二次未併入新資料"
    assert len(state2["tickers"]["2330.TW"]["merge_events"]) == 2, "合併記錄未累積"


def test_accumulate_records_conflicts(tmp_path):
    """資料源事後修正必須被計數，而非被「先到者為準」靜默吞掉。"""
    import run_intraday_eval as cli

    state = str(tmp_path / "state")
    csv_dir = str(tmp_path / "csv")
    earlier, later = fx.overlapping_pair(total_days=30, overlap_days=20)

    _write_offline_csv(csv_dir, "2330.TW", earlier)
    cli.main(["accumulate", "--tickers", "2330.TW",
              "--state-dir", state, "--offline-csv-dir", csv_dir])

    _write_offline_csv(csv_dir, "2330.TW", fx.with_conflicts(later, 3, delta=0.5))
    cli.main(["accumulate", "--tickers", "2330.TW",
              "--state-dir", state, "--offline-csv-dir", csv_dir])

    events = isnap.read_chain_state(state)["tickers"]["2330.TW"]["merge_events"]
    assert events[-1]["conflicts"] == 3


def test_accumulate_partial_failure_keeps_others(tmp_path):
    """一檔失敗不影響其他檔，但退出碼須反映有失敗。"""
    import run_intraday_eval as cli

    state = str(tmp_path / "state")
    csv_dir = str(tmp_path / "csv")
    _write_offline_csv(csv_dir, "2330.TW", fx.intraday_frame(10))

    rc = cli.main(["accumulate", "--tickers", "2330.TW MISSING.TW",
                   "--state-dir", state, "--offline-csv-dir", csv_dir])
    assert rc == cli.EXIT_PARTIAL_FAILURE
    assert isnap.read_history(state, "2330.TW") is not None
