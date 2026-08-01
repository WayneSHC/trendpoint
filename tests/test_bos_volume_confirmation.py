# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 012 — BOS 量能確認濾網（SC-002/003/005/007/008/009）。

全部以合成序列執行（A 段），不需 `trendpoint.db`。
對應 contracts/bos-volume-filter.md §1~§4。
"""

import numpy as np
import pandas as pd
import pytest

from backtester import BacktestEngine
from ladder_system import (
    PositionManager,
    build_indicator_frame,
    calculate_volume_confirmation,
)

from bos_volume_fixtures import (
    DEFAULT_MULT,
    DEFAULT_PERIOD,
    daily_klines,
    expected_volume_ok,
    mss_reversal_klines,
)

FIXTURE_DIR = __file__.rsplit("/", 1)[0] + "/fixtures"


def _frame(volumes, closes=None):
    """以指定量能序列建構最小合法 OHLCV（價格不影響量能判定）。"""
    n = len(volumes)
    idx = pd.bdate_range("2024-01-01", periods=n, name="datetime")
    close = pd.Series(closes if closes is not None else [100.0] * n, index=idx, dtype=float)
    return pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close,
         "volume": pd.Series([float(v) for v in volumes], index=idx)},
        index=idx,
    )


# ---------------------------------------------------------------- §1 契約（T003）

def test_volume_confirmation_uses_only_prior_bars_for_the_mean():
    """(a) 平均量只用**判定根之前**的 K 線（.shift(1)）——憲章原則 I 的落點。

    手算：period=3、mult=1.0。第 4 根（index 3）的均量 = 前三根 (10,10,10)/3 = 10，
    其自身量 40 不參與均量。故 40 > 10×1.0 成立。
    若少了 .shift(1)，均量會變成 (10,10,10,40)/4 = 17.5，判定式仍成立但**數值不同**——
    下一根的手算值可鑑別：index 4 量 10，正確均量 = (10,10,40)/3 = 20 → 10 > 20 為 False；
    無 shift 時均量 = (10,40,10)/3 = 20 → 亦 False。故本測試另以 index 5 釘死。
    """
    df = _frame([10, 10, 10, 40, 10, 25, 10])
    got = calculate_volume_confirmation(df, period=3, mult=1.0)

    # 逐根手算（均量一律為「前 3 根」）
    #  i=0,1,2 : rolling 未滿 → False
    #  i=3 : ma=(10+10+10)/3=10   , vol=40 > 10   → True
    #  i=4 : ma=(10+10+40)/3=20   , vol=10 > 20   → False
    #  i=5 : ma=(10+40+10)/3=20   , vol=25 > 20   → True
    #  i=6 : ma=(40+10+25)/3=25   , vol=10 > 25   → False
    assert list(got) == [False, False, False, True, False, True, False]


def test_volume_confirmation_warmup_is_false():
    """(b) 前 period 根恆為 False（rolling 未滿）。"""
    df = _frame([1000] * 30)
    got = calculate_volume_confirmation(df, period=DEFAULT_PERIOD, mult=DEFAULT_MULT)
    assert not got.iloc[:DEFAULT_PERIOD].any(), "暖機期不得有任何 True"


def test_volume_confirmation_zero_mean_is_false():
    """(c) vol_ma <= 0 時為 False——不得退化為「一律通過」。"""
    df = _frame([0] * 5 + [100] * 5)
    got = calculate_volume_confirmation(df, period=3, mult=1.5)
    # index 3、4 的均量為 0（前三根皆 0）→ 必須 False，即使自身量 > 0
    assert got.iloc[3] == False  # noqa: E712
    assert got.iloc[4] == False  # noqa: E712
    # index 5 自身量 100、均量 0 → 仍為 False（0 均量無從判定放大）
    assert got.iloc[5] == False  # noqa: E712


def test_volume_confirmation_is_strictly_greater():
    """(d) volume 恰等於門檻時為 False（嚴格大於）。"""
    df = _frame([10, 10, 10, 15])          # ma=10, mult=1.5 → 門檻恰為 15
    got = calculate_volume_confirmation(df, period=3, mult=1.5)
    assert got.iloc[3] == False, "恰等於門檻應為 False（嚴格大於）"  # noqa: E712

    df2 = _frame([10, 10, 10, 15.000001])
    assert calculate_volume_confirmation(df2, period=3, mult=1.5).iloc[3] == True  # noqa: E712


def test_volume_confirmation_has_no_nan_and_is_bool():
    """(e) 回傳無 NaN、dtype 為 bool、index 與輸入相同、不就地修改。"""
    df = daily_klines(120)
    before = df.copy()
    got = calculate_volume_confirmation(df)

    assert got.dtype == bool
    assert not got.isna().any()
    assert (got.index == df.index).all()
    pd.testing.assert_frame_equal(df, before), "不得就地修改輸入"


def test_volume_confirmation_matches_independent_reference():
    """與 fixture 中的獨立參考實作逐值相同（兩份實作互為對照）。"""
    df = daily_klines()
    got = calculate_volume_confirmation(df, DEFAULT_PERIOD, DEFAULT_MULT)
    assert (got.to_numpy() == expected_volume_ok(df).to_numpy()).all()


def test_volume_confirmation_only_depends_on_past_and_current_volume():
    """時序契約：第 i 值只依賴 volume.iloc[:i+1]——截斷序列的前綴須完全相同。"""
    df = daily_klines(200)
    full = calculate_volume_confirmation(df)
    for cut in (60, 120, 199):
        head = calculate_volume_confirmation(df.iloc[:cut])
        assert (head.to_numpy() == full.iloc[:cut].to_numpy()).all(), \
            f"截斷至 {cut} 根後前綴改變——存在看前偏誤"


# ---------------------------------------------------------------- §2 欄位集（T008）

def _frozen_indicator_columns():
    with open(f"{FIXTURE_DIR}/012_baseline_indicator_columns.txt", encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]


def test_indicator_frame_columns_unchanged_when_disabled():
    """FR-002：關閉時輸出欄位集與實作前**逐字相同**（欄名與順序）。"""
    df = daily_klines(200)
    frame = build_indicator_frame(df, structure_period=10, use_fvg=False)
    assert list(frame.columns) == _frozen_indicator_columns()
    assert "bos_volume_ok" not in frame.columns


def test_indicator_frame_adds_exactly_one_column_when_enabled():
    """啟用時恰多 bos_volume_ok 一欄，且位置不打亂既有欄序。"""
    df = daily_klines(200)
    off = build_indicator_frame(df, structure_period=10, use_fvg=False)
    on = build_indicator_frame(df, structure_period=10, use_fvg=False, use_bos_volume=True)

    assert set(on.columns) - set(off.columns) == {"bos_volume_ok"}
    assert [c for c in on.columns if c != "bos_volume_ok"] == list(off.columns)
    assert on["bos_volume_ok"].dtype == bool


def test_indicator_frame_column_matches_standalone_function():
    """欄值即 calculate_volume_confirmation 的輸出（參數確實被穿線）。"""
    df = daily_klines(200)
    on = build_indicator_frame(df, structure_period=10, use_fvg=False,
                               use_bos_volume=True, bos_volume_mult=2.0, bos_volume_period=10)
    assert (on["bos_volume_ok"].to_numpy()
            == calculate_volume_confirmation(df, period=10, mult=2.0).to_numpy()).all()


# ---------------------------------------------------------------- SC-002（T020）

def test_sc002_structure_signals_are_untouched_by_the_filter():
    """SC-002 / FR-004：訊號層未被污染——bos_signal 與 mss_signal 逐值相等。"""
    df = daily_klines()
    off = build_indicator_frame(df, structure_period=10, use_fvg=True)
    on = build_indicator_frame(df, structure_period=10, use_fvg=True,
                               use_bos_volume=True, bos_volume_mult=DEFAULT_MULT,
                               bos_volume_period=DEFAULT_PERIOD)

    for col in ("bos_signal", "mss_signal"):
        assert (off[col].to_numpy() == on[col].to_numpy()).all(), \
            f"{col} 被量能參數改變——濾網滲進了訊號層"

    # 其餘既有欄位亦逐值相同
    for col in off.columns:
        a, b = off[col].to_numpy(), on[col].to_numpy()
        if a.dtype.kind == "f":
            assert np.allclose(a, b, equal_nan=True), f"{col} 數值改變"
        else:
            assert (a == b).all(), f"{col} 改變"


# ---------------------------------------------------------------- §3 進場層（T009 / SC-003 / SC-008）

_ENTRY_KW = dict(
    close=101.0, open_val=100.0, daily_open=99.0, vwap=99.5, atr=1.0,
    candle_high=102.0, candle_low=99.0, structure_sig=1,
    global_filter_ok=True, is_daily=True,
)


def test_check_entry_signal_default_volume_ok_is_backward_compatible():
    """FR-002：不傳 volume_ok 時回傳值與實作前逐字相同（兩個 direction 皆是）。"""
    pm = PositionManager()
    assert pm.check_entry_signal(**_ENTRY_KW) is True
    assert pm.check_entry_signal(**{**_ENTRY_KW, "volume_ok": True}) is True

    short_kw = {**_ENTRY_KW, "close": 99.0, "open_val": 100.0, "daily_open": 101.0,
                "vwap": 100.5, "structure_sig": -1, "direction": -1}
    assert pm.check_entry_signal(**short_kw) is True
    assert pm.check_entry_signal(**{**short_kw, "volume_ok": True}) is True


def test_sc003_only_volume_differs_blocks_entry():
    """SC-003：四道確認皆通過、僅量能未達門檻 → 啟用時不進場、關閉時進場。"""
    pm = PositionManager()
    # 關閉（不傳 volume_ok）→ 進場
    assert pm.check_entry_signal(**_ENTRY_KW) is True
    # 啟用且量能不足 → 不進場；差異**僅**由量能造成
    assert pm.check_entry_signal(**{**_ENTRY_KW, "volume_ok": False}) is False


def test_sc009_volume_dimension_is_ablatable():
    """FR-008：'bos_volume' 在 disabled_filters 中時該維度視為通過。"""
    pm = PositionManager()
    blocked = {**_ENTRY_KW, "volume_ok": False}
    assert pm.check_entry_signal(**blocked) is False
    assert pm.check_entry_signal(**blocked,
                                 disabled_filters=frozenset({"bos_volume"})) is True


def test_sc004_volume_dimension_is_direction_neutral():
    """SC-004：volume_ok=False 時多空兩側皆不進場（濾網無方向性）。"""
    pm = PositionManager()
    long_kw = {**_ENTRY_KW, "volume_ok": False}
    short_kw = {**_ENTRY_KW, "close": 99.0, "open_val": 100.0, "daily_open": 101.0,
                "vwap": 100.5, "structure_sig": -1, "direction": -1, "volume_ok": False}
    assert pm.check_entry_signal(**long_kw) is False
    assert pm.check_entry_signal(**short_kw) is False
    # 量能足時兩側皆進場（對稱）
    assert pm.check_entry_signal(**{**long_kw, "volume_ok": True}) is True
    assert pm.check_entry_signal(**{**short_kw, "volume_ok": True}) is True


# ---------------------------------------------------------------- 引擎整合

def _run(df, **kw):
    return BacktestEngine(initial_capital=1_000_000.0).run_backtest(df, verbose=False, **kw)


def test_filter_reduces_entries_on_synthetic_series():
    """啟用濾網後進場數下降，且啟用後的進場其判定根量能皆達門檻。"""
    df = daily_klines()
    off = _run(df)
    on = _run(df, use_bos_volume=True)

    off_n = int((off["trades"]["action"] == "BUY").sum())
    on_n = 0 if on["trades"].empty else int((on["trades"]["action"] == "BUY").sum())
    assert off_n == 7, "fixture 漂移——基準進場數應為 7"
    assert on_n < off_n, "濾網啟用後進場數應下降（否則本 fixture 失去鑑別力）"

    vol_ok = expected_volume_ok(df)
    for ts in on["trades"][on["trades"]["action"] == "BUY"]["datetime"]:
        sig_pos = int(df.index.get_loc(ts)) - 1        # 判定根 = 成交根的前一根
        assert bool(vol_ok.iloc[sig_pos]), f"{ts} 的判定根量能未達門檻卻仍進場"


def test_sc005_no_entry_during_warmup_window():
    """SC-005：bos_volume_period 暖機區間內啟用濾網時不產生任何進場。"""
    df = daily_klines()
    period = 250                                        # 刻意大於任何可能的進場位置
    on = _run(df, use_bos_volume=True, bos_volume_period=period)

    if on["trades"].empty:
        return
    for ts in on["trades"][on["trades"]["action"] == "BUY"]["datetime"]:
        assert int(df.index.get_loc(ts)) - 1 >= period, "暖機期內不得進場"


def _mss_entry_bars(res, df):
    """MSS 反轉進場的判定根位置（成交根的前一根）。"""
    tr = res["trades"]
    if tr.empty:
        return []
    return [int(df.index.get_loc(r.datetime)) - 1 for r in tr.itertuples()
            if r.action in ("BUY", "SELL_SHORT") and "MSS 反轉" in r.event]


def test_sc008_mss_reversal_branch_is_unaffected():
    """SC-008 / FR-005：反轉（MSS）分支不套用本濾網，未雙重套用。

    直接證法：找出「判定根量能未達門檻」的 MSS 反轉進場，斷言它在濾網**啟用後
    仍然成立**。若 volume_ok 被誤傳進反轉分支，這些進場會消失。

    不比對「全部 MSS 進場逐筆相同」——擋掉一筆 BOS 進場會改變後續持倉狀態，
    原本被遮蔽的訊號因此得以成立，逐筆相同並非合理要求（spec 013 的同一課）。
    """
    df = mss_reversal_klines()
    off = _run(df, mss_reversal_entry=True)
    on = _run(df, mss_reversal_entry=True, use_bos_volume=True)

    off_mss = _mss_entry_bars(off, df)
    assert off_mss, "fixture 失去鑑別力：未啟用時本來就沒有 MSS 反轉進場"

    vol_ok = expected_volume_ok(df)
    blocked = [p for p in off_mss if not bool(vol_ok.iloc[p])]
    assert blocked, "fixture 失去鑑別力：沒有『量能不足的 MSS 反轉進場』可供驗證"

    on_mss = _mss_entry_bars(on, df)
    survived = [p for p in blocked if p in on_mss]
    assert survived == blocked, (
        f"量能不足的 MSS 反轉進場在濾網啟用後消失（{set(blocked) - set(on_mss)}）"
        "——濾網被誤套用到反轉分支")


# ---------------------------------------------------------------- SC-001（T019）

BASELINE_RTOL = 1e-9   # 理由同 spec 013：凍結檔跨機器不具位元可攜性（見該案 tasks.md）


def _read_frozen_trades():
    return pd.read_csv(f"{FIXTURE_DIR}/012_baseline_trades.csv", comment="#",
                       float_precision="round_trip")


def _read_frozen_summary():
    out = {}
    with open(f"{FIXTURE_DIR}/012_baseline_summary.txt", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.strip().split("=", 1)
            out[k] = v
    return out


def test_sc001_filter_off_matches_frozen_baseline():
    """SC-001：濾網關閉時逐筆進出場、股數、損益與權益終值皆與實作前相同。

    結構層（欄位、筆數、時點、action）完全相等；數值層採 BASELINE_RTOL 容差
    ——合成序列的價格由 `np.exp` 產生，其 SIMD 路徑依 CPU 而異，凍結檔跨機器
    不具位元可攜性（spec 013 已就同一問題排查並記錄）。真正的行為改變是
    1e-4 以上的量級，與容差相距五個數量級。
    """
    df = daily_klines()
    res = _run(df)

    expected = _read_frozen_trades()
    actual = res["trades"].reset_index(drop=True)
    assert list(actual.columns) == list(expected.columns)
    assert len(actual) == len(expected), "交易筆數改變——濾網關閉時不得有任何差異"

    for col in expected.columns:
        if col == "datetime":
            assert (pd.to_datetime(actual[col]).astype("int64").to_numpy()
                    == pd.to_datetime(expected[col]).astype("int64").to_numpy()).all(), \
                "交易時點改變"
        elif expected[col].dtype.kind in "fi":
            a, b = actual[col].to_numpy(float), expected[col].to_numpy(float)
            rel = np.abs(a - b) / np.maximum(np.abs(b), 1.0)
            assert rel.max() <= BASELINE_RTOL, \
                f"trades 欄 {col} 偏離基準（最大相對偏差 {rel.max():.3e}）"
        else:
            assert (actual[col].to_numpy() == expected[col].to_numpy()).all(), \
                f"trades 欄 {col} 改變"

    frozen = _read_frozen_summary()
    eq = res["equity_curve"]
    assert len(eq) == int(frozen["bars"])
    assert list(eq.columns) == frozen["equity_columns"].split(",")
    assert int(res["summary"]["total_trades"]) == int(frozen["total_trades"])
    for key, got in (("final_equity", float(eq["equity"].iloc[-1])),
                     ("total_return", float(res["summary"]["total_return"]))):
        want = float(frozen[key])
        assert abs(got - want) / max(abs(want), 1.0) <= BASELINE_RTOL, \
            f"{key} 偏離基準：{got!r} vs {want!r}"


# ---------------------------------------------------------------- SC-007（T016）

def test_sc007_ablation_target_exists_and_produces_metrics():
    """SC-007：消融清單含 'bos_volume'，且該列實跑後各項指標皆有值。"""
    from config.config import SingleStrategyParams, SystemConfig
    from run_ablation import ABLATION_TARGETS, OPT_IN_KEYS, run_ablation_for_ticker

    assert "bos_volume" in [k for _, k in ABLATION_TARGETS]
    assert OPT_IN_KEYS["bos_volume"] == "use_bos_volume"

    ticker = "0050.TW"
    cfg = SystemConfig()
    cfg.strategy.ticker_overrides[ticker] = SingleStrategyParams(use_bos_volume=True)
    engine = BacktestEngine(initial_capital=1_000_000.0)
    results = run_ablation_for_ticker(engine, cfg, ticker, daily_klines())

    row = next(r for r in results if r["label"] == "停用 BOS 量能確認")
    assert row["skipped"] is False
    assert row["is_risk_gate"] is False, "本濾網是訊號濾網，判讀方向與風控閘門不同"
    for key in ("total_trades", "expectancy", "profit_factor", "max_drawdown", "calmar"):
        value = row[key]
        assert value is not None and value == value, f"{key} 為 NaN"

    # 停用濾網 → 交易數回到基準水準（多於啟用時）
    baseline = next(r for r in results if r["label"] == "基準 (全濾網)")
    assert row["total_trades"] > baseline["total_trades"]


def test_sc007_ablation_row_skipped_when_filter_disabled():
    """濾網未啟用時該列須明示略過，不得靜默輸出與基準相同的數字。"""
    from config.config import SystemConfig
    from run_ablation import run_ablation_for_ticker

    ticker = "0050.TW"
    cfg = SystemConfig()
    engine = BacktestEngine(initial_capital=1_000_000.0)
    results = run_ablation_for_ticker(engine, cfg, ticker, daily_klines(300))

    row = next(r for r in results if r["label"] == "停用 BOS 量能確認")
    assert row["skipped"] is True
    assert "use_bos_volume=false" in row["note"]
    assert "total_return" not in row


# ---------------------------------------------------------------- SC-004 鏡像回測（T027）

def test_sc004_mirror_backtest_symmetry():
    """SC-004：對價格鏡像序列，量能條件對多空兩側進場的影響逐項對稱。

    鏡像只翻價格、**不動成交量**（量能無方向性），故兩側的 bos_volume_ok
    逐根相同；被擋掉的進場位置也必須逐項相同。
    """
    from config.config import FuturesCostConfig
    from instruments import ContractSpec
    from trading_costs import FuturesCostModel, FuturesSizer
    from test_short_side import mirror_klines
    from bos_volume_fixtures import futures_daily_klines

    txc = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)
    fcfg = FuturesCostConfig()

    def run_fut(frame, **kw):
        return BacktestEngine(initial_capital=10_000_000.0).run_backtest(
            frame, asset_class="futures",
            cost_model=FuturesCostModel(txc, fcfg), sizer=FuturesSizer(txc, fcfg),
            point_value=txc.point_value, verbose=False, **kw)

    df = futures_daily_klines()
    mirrored = mirror_klines(df)

    # 鏡像不動量能 → 兩側量能判定逐根相同（對稱的前提）
    assert (expected_volume_ok(df).to_numpy()
            == expected_volume_ok(mirrored).to_numpy()).all()

    def entry_bars(res, frame, action):
        tr = res["trades"]
        if tr.empty:
            return set()
        return {int(frame.index.get_loc(r.datetime)) for r in tr.itertuples()
                if r.action == action}

    long_off = entry_bars(run_fut(df), df, "BUY")
    long_on = entry_bars(run_fut(df, use_bos_volume=True), df, "BUY")
    short_off = entry_bars(run_fut(mirrored, enable_short=True), mirrored, "SELL_SHORT")
    short_on = entry_bars(run_fut(mirrored, enable_short=True, use_bos_volume=True),
                          mirrored, "SELL_SHORT")

    assert long_off, "鏡像對照失去鑑別力：多方無進場"
    assert short_off, "鏡像對照失去鑑別力：空方無進場"

    long_removed = long_off - long_on
    short_removed = short_off - short_on
    assert long_removed, "濾網對多方零影響——本 fixture 無鑑別力"
    assert long_removed == short_removed, \
        f"多空被擋除的位置不對稱：多方 {sorted(long_removed)} vs 空方 {sorted(short_removed)}"
