# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 013 — 進場閘門的引擎整合驗收（SC-001/002/003/006/007/008/009/010/012）。

本檔全部以合成序列執行（A 段），不需 `trendpoint.db`。若本檔任何測試因缺資料
而 skip，即代表 A 段設計失敗——見 tasks.md T031。
"""

import warnings

import pandas as pd
import pytest

from backtester import BacktestEngine
from config.config import FuturesCostConfig
from instruments import ContractSpec
from risk_gates import settlement_days
from trading_costs import FuturesCostModel, FuturesSizer

from gate_fixtures import (
    blocked_with_open_position_klines,
    futures_daily_frame,
    losing_then_recovering_klines,
    third_wednesday,
)
from test_short_side import mirror_klines

FIXTURE_DIR = __file__.rsplit("/", 1)[0] + "/fixtures"
EXIT_ACTIONS = {"SELL_HALF", "SELL_ALL", "COVER_HALF", "COVER_ALL"}
ENTRY_ACTIONS = {"BUY", "SELL_SHORT"}

TXC = ContractSpec(point_value=200.0, tick_size=1.0, exchange_fee_per_lot=20.0)


# ------------------------------------------------------------------ helpers

def run_equity(df, **kw):
    """現貨單標的回測（本案基準路徑，初始資金與 T002 凍結時一致）。"""
    return BacktestEngine(initial_capital=1_000_000.0).run_backtest(df, verbose=False, **kw)


def run_futures(df, **kw):
    cfg = FuturesCostConfig()
    return BacktestEngine(initial_capital=10_000_000.0).run_backtest(
        df, asset_class="futures",
        cost_model=FuturesCostModel(TXC, cfg), sizer=FuturesSizer(TXC, cfg),
        point_value=TXC.point_value, verbose=False, **kw)


def bar_positions(res, actions):
    """取指定 action 發生的『第幾根』（以 equity_curve 的位置為準）。"""
    tr = res["trades"]
    if tr.empty:
        return []
    idx = res["equity_curve"].index
    return [int(idx.get_loc(r.datetime)) for r in tr.itertuples() if r.action in actions]


def transitions(res):
    """block_reason 的狀態轉折：[(根索引, 新狀態), ...]，含起始根。"""
    br = res["equity_curve"]["block_reason"]
    changed = br.ne(br.shift()).to_numpy().nonzero()[0]
    return [(int(i), br.iloc[i]) for i in changed]


# ------------------------------------------------------------------ SC-001（T020）

def _read_frozen(name, **kw):
    """讀取凍結基準。

    `float_precision="round_trip"` 是必要的：pandas 預設的 C 浮點剖析器
    （xstrtod）不保證正確捨入，讀回來會差 1 ulp——本測試要求**位元相同**，
    容忍 1 ulp 等於放棄了「逐根不變」這條保證。凍結端已用 %.17g 寫出。
    """
    return pd.read_csv(f"{FIXTURE_DIR}/013_baseline_{name}.csv", comment="#",
                       float_precision="round_trip", **kw)


def test_sc001_gates_off_is_bit_identical_to_frozen_baseline():
    """SC-001 三層回歸：閘門關閉時逐筆 trades、逐根 equity、**欄位集**皆與實作前相同。

    第三層（欄位集）是本案比 spec 012 多的一層：若 `block_reason` 被誤實作成
    無條件輸出，前兩層仍會全綠——多一個恆為空字串的欄位不改變任何數值。
    """
    res = run_equity(losing_then_recovering_klines())

    # (a) 逐筆 trades
    expected_trades = _read_frozen("trades")
    actual_trades = res["trades"].reset_index(drop=True)
    assert list(actual_trades.columns) == list(expected_trades.columns)
    assert len(actual_trades) == len(expected_trades)
    for col in expected_trades.columns:
        if col == "datetime":
            assert (pd.to_datetime(actual_trades[col]).astype("int64").to_numpy()
                    == pd.to_datetime(expected_trades[col]).astype("int64").to_numpy()).all()
        elif expected_trades[col].dtype.kind in "fi":
            assert actual_trades[col].to_numpy() == pytest.approx(
                expected_trades[col].to_numpy(), rel=0, abs=0), f"trades 欄 {col} 已偏移"
        else:
            assert (actual_trades[col].to_numpy() == expected_trades[col].to_numpy()).all()

    # (b) equity_curve 逐根數值
    expected_eq = _read_frozen("equity", index_col="datetime", parse_dates=["datetime"])
    actual_eq = res["equity_curve"]
    assert len(actual_eq) == len(expected_eq)
    for col in ("capital", "position_value", "equity"):
        diffs = (actual_eq[col].to_numpy() != expected_eq[col].to_numpy()).sum()
        assert diffs == 0, f"equity_curve 欄 {col} 有 {diffs} 根與凍結基準不同"

    # (c) 欄位集：閘門關閉時不得出現 block_reason
    with open(f"{FIXTURE_DIR}/013_baseline_equity_columns.txt", encoding="utf-8") as fh:
        frozen_cols = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    assert list(actual_eq.columns) == frozen_cols
    assert "block_reason" not in actual_eq.columns


def test_sc001_block_reason_column_appears_only_when_gate_effective():
    """條件輸出欄的存在條件（FR-010）。"""
    df = losing_then_recovering_klines(600)
    assert "block_reason" not in run_equity(df)["equity_curve"].columns
    assert "block_reason" in run_equity(df, use_dd_gate=True)["equity_curve"].columns

    # 現貨 + 結算日閘門 = 不生效 → 不輸出該欄（見 SC-008；一欄全空字串會誤導）
    assert "block_reason" not in run_equity(df, use_settlement_gate=True)["equity_curve"].columns
    fut = futures_daily_frame(n=300)
    assert "block_reason" in run_futures(fut, use_settlement_gate=True)["equity_curve"].columns


# ------------------------------------------------------------------ SC-002（T014）

# 下列根索引為實作時以合成序列離線確認之值（gate_fixtures 變體 a）。
# 它們是「轉折可被指出」這條驗收標準的具體形式——若 fixture 或引擎行為改變，
# 這些數字**應該**變動並被重新確認，不要為了讓測試變綠而放寬成範圍比較。
A_BLOCK_1, A_RESUME_1 = 1280, 1285
A_BLOCK_2, A_RESUME_2 = 1519, 1526
A_BLOCK_FINAL = 2450


def test_sc002_block_and_resume_turning_points_are_exact():
    """SC-002：回撤跨過封鎖門檻後不再進場，回升至恢復門檻後恢復進場。"""
    df = losing_then_recovering_klines()
    off = run_equity(df)
    on = run_equity(df, use_dd_gate=True, dd_limit_pct=0.04, dd_resume_pct=0.03)

    assert transitions(on) == [
        (0, ""),
        (A_BLOCK_1, "drawdown"), (A_RESUME_1, ""),
        (A_BLOCK_2, "drawdown"), (A_RESUME_2, ""),
        (A_BLOCK_FINAL, "drawdown"),
    ]

    on_entries = bar_positions(on, ENTRY_ACTIONS)
    off_entries = bar_positions(off, ENTRY_ACTIONS)

    # 封鎖只會移除進場，不會憑空製造
    assert set(on_entries) <= set(off_entries)

    # 最終封鎖之後不再有任何新進場（本案的封鎖效果）
    assert [e for e in on_entries if e >= A_BLOCK_FINAL] == []
    suppressed = [e for e in off_entries if e >= A_BLOCK_FINAL]
    assert suppressed, "fixture 失去鑑別力：未啟用閘門時封鎖期後本就沒有進場"

    # 解除之後確實恢復進場（否則「恢復」只是狀態欄上的字）
    assert [e for e in on_entries if e > A_RESUME_2], "解除封鎖後應恢復進場"


def test_sc002_blocked_windows_contain_no_entry():
    """封鎖期間（block_reason 非空）不得出現任何新進場——與轉折索引互為冗餘檢查。"""
    df = losing_then_recovering_klines()
    on = run_equity(df, use_dd_gate=True, dd_limit_pct=0.04, dd_resume_pct=0.03)
    br = on["equity_curve"]["block_reason"]
    for pos in bar_positions(on, ENTRY_ACTIONS):
        assert br.iloc[pos] == "", f"第 {pos} 根處於封鎖狀態卻仍進場"


def test_sc002_tight_threshold_latches_and_suppresses_most_entries():
    """較緊的門檻下封鎖後鎖死：進場數大幅下降（鑑別力最強的一組）。"""
    df = losing_then_recovering_klines()
    off = run_equity(df)
    on = run_equity(df, use_dd_gate=True, dd_limit_pct=0.02, dd_resume_pct=0.005)
    assert len(bar_positions(off, ENTRY_ACTIONS)) == 10
    assert len(bar_positions(on, ENTRY_ACTIONS)) == 1


# ------------------------------------------------------------------ SC-003（T013，最高風險守門）

def test_sc003_exits_still_execute_while_gate_is_blocking():
    """SC-003【最高風險守門】封鎖期間所有出場路徑仍正常執行，權益曲線無斷點。

    鑑別力**已實測確認**（實作期間暫時把接線改成迴圈開頭
    `if not gate_ok: continue` 後重跑）：該反模式下封鎖期間不再有任何出場，
    本測試第 (1) 項斷言即失敗（「封鎖期間沒有任何出場可供驗證」）。
    反模式未入版控，此處僅記錄結論。
    """
    df = blocked_with_open_position_klines()
    on = run_equity(df, use_dd_gate=True, dd_limit_pct=0.01, dd_resume_pct=0.005)
    eq = on["equity_curve"]
    br = eq["block_reason"]

    # (1) 確有「封鎖狀態下發生的出場」
    blocked_exits = [p for p in bar_positions(on, EXIT_ACTIONS) if br.iloc[p] != ""]
    assert blocked_exits, "fixture 失去鑑別力：封鎖期間沒有任何出場可供驗證"
    exit_events = {r.event for r in on["trades"].itertuples()
                   if r.action in EXIT_ACTIONS
                   and br.iloc[int(eq.index.get_loc(r.datetime))] != ""}
    assert any("止損" in e for e in exit_events), f"封鎖期間應仍能觸發停損，實得 {exit_events}"

    # (2) 權益曲線每根皆有值、根數完整（未爆倉故不截斷）
    assert len(eq) == len(df) - 1
    assert not eq["equity"].isna().any()
    assert not on["summary"].get("blown_up", False)

    # (3) 出場確實結算了現金（封鎖期間權益仍會變動）
    first_block = transitions(on)[1][0]
    assert eq["equity"].iloc[first_block:].nunique() > 1


# ------------------------------------------------------------------ SC-012（T016）

def test_sc012_block_reason_values_and_domain():
    """SC-012：封鎖原因可辨識，且值域限於契約列舉。"""
    df = futures_daily_frame()
    settle = settlement_days(df.index)

    both = run_futures(df, use_dd_gate=True, dd_limit_pct=0.02, dd_resume_pct=0.005,
                       use_settlement_gate=True)
    br = both["equity_curve"]["block_reason"]
    assert set(br.unique()) <= {"", "drawdown", "settlement", "drawdown+settlement"}

    # 結算日當根必含 settlement；非結算日必不含
    for pos, ts in enumerate(both["equity_curve"].index):
        if ts.date() in settle:
            assert "settlement" in br.iloc[pos], f"{ts.date()} 為結算日卻未標記"
        else:
            assert "settlement" not in br.iloc[pos]

    # 未封鎖根為空字串（非 NaN、非 None）
    assert br.map(lambda v: isinstance(v, str)).all()
    assert (br == "").any()

    # 兩道皆封鎖時以 "drawdown+settlement" 表示（順序固定，便於比對）
    assert not br.str.contains("settlement\\+drawdown").any()


# ------------------------------------------------------------------ SC-009（T021）

def test_sc009_gates_are_independent():
    """SC-009：僅啟用其一時，另一道對結果零影響。"""
    df = futures_daily_frame()

    dd_only = run_futures(df, use_dd_gate=True, dd_limit_pct=0.02, dd_resume_pct=0.005)
    settle_only = run_futures(df, use_settlement_gate=True)
    both = run_futures(df, use_dd_gate=True, dd_limit_pct=0.02, dd_resume_pct=0.005,
                       use_settlement_gate=True)

    # 只開回撤閘門 → block_reason 不含 settlement
    assert not dd_only["equity_curve"]["block_reason"].str.contains("settlement").any()
    # 只開結算日閘門 → 不含 drawdown
    assert not settle_only["equity_curve"]["block_reason"].str.contains("drawdown").any()

    # 兩道各自封鎖的根，在雙開時仍被封鎖（合成為聯集，非互相取消）
    dd_blocked = set((dd_only["equity_curve"]["block_reason"] != "").to_numpy().nonzero()[0])
    st_blocked = set((settle_only["equity_curve"]["block_reason"] != "").to_numpy().nonzero()[0])
    both_blocked = set((both["equity_curve"]["block_reason"] != "").to_numpy().nonzero()[0])
    assert st_blocked <= both_blocked
    assert dd_blocked <= both_blocked

    # 結算日閘門不改變回撤閘門的封鎖起點（獨立性的數值面）
    assert transitions(dd_only)[1][0] == min(
        p for p, r in enumerate(both["equity_curve"]["block_reason"]) if "drawdown" in r)


# ------------------------------------------------------------------ SC-010（T017）

def test_sc010_gate_is_direction_neutral():
    """SC-010：閘門對空方進場的影響與多方逐項對稱（閘門無方向性）。"""
    df = futures_daily_frame()
    mirrored = mirror_klines(df)

    long_off = run_futures(df)
    long_on = run_futures(df, use_settlement_gate=True)
    short_off = run_futures(mirrored, enable_short=True)
    short_on = run_futures(mirrored, enable_short=True, use_settlement_gate=True)

    long_removed = set(bar_positions(long_off, {"BUY"})) - set(bar_positions(long_on, {"BUY"}))
    short_removed = (set(bar_positions(short_off, {"SELL_SHORT"}))
                     - set(bar_positions(short_on, {"SELL_SHORT"})))

    assert long_removed, "鏡像對照失去鑑別力：多方本來就沒有結算日進場"
    assert short_removed, "空方應同樣被結算日閘門擋下"
    assert long_removed == short_removed, "閘門對多空的擋除位置必須逐項相同"

    # 封鎖狀態軌跡本身與方向無關（同一索引 → 同一結算日集合）
    assert (long_on["equity_curve"]["block_reason"].to_numpy()
            == short_on["equity_curve"]["block_reason"].to_numpy()).all()


# ------------------------------------------------------------------ SC-006/007/008（T025-T027）

def test_sc006_no_entry_on_settlement_day():
    """SC-006：結算日不出現新進場；非結算日之進場與未啟用時相同。"""
    df = futures_daily_frame()
    settle = settlement_days(df.index)

    off = run_futures(df)
    on = run_futures(df, use_settlement_gate=True)

    off_idx, on_idx = off["equity_curve"].index, on["equity_curve"].index
    off_entries = bar_positions(off, ENTRY_ACTIONS)
    on_entries = bar_positions(on, ENTRY_ACTIONS)

    blocked_offs = [p for p in off_entries if off_idx[p].date() in settle]
    assert blocked_offs, "fixture 失去鑑別力：未啟用時本來就沒有結算日進場"

    # (1) 啟用後沒有任何進場落在結算日
    for pos in on_entries:
        assert on_idx[pos].date() not in settle, f"{on_idx[pos].date()} 為結算日仍進場"

    # (2) **第一次被擋之前**逐筆相同——閘門只影響它擋掉的那一刻起的路徑。
    # 之後不可能逐筆相同，且這不是缺陷：少做一筆交易會改變後續的持倉狀態與資金，
    # 原本被「還在持倉」遮蔽的訊號因此得以成立。把「擋掉一筆後其餘不變」寫成
    # 驗收條件是對回測的誤解——那只有在交易彼此獨立時才成立。
    first_block = blocked_offs[0]
    assert ([p for p in on_entries if p < first_block]
            == [p for p in off_entries if p < first_block])


def test_sc007_settlement_rolls_to_next_trading_day_when_absent():
    """SC-007：當月第三個週三為非交易日時，封鎖日落在其後第一個交易日。"""
    df = futures_daily_frame(drop_settlement="2024-06")
    missing = third_wednesday(2024, 6)
    assert missing not in df.index

    on = run_futures(df, use_settlement_gate=True)
    eq = on["equity_curve"]
    br = eq["block_reason"]

    following = eq.index[eq.index > missing][0]
    pos = int(eq.index.get_loc(following))
    assert "settlement" in br.iloc[pos], f"應封鎖後推日 {following.date()}"

    # 前一個交易日（第三個週三之前）不得被封鎖
    prev_pos = pos - 1
    assert "settlement" not in br.iloc[prev_pos]


def test_sc008_settlement_gate_is_inert_for_equity():
    """SC-008：對現貨標的啟用結算日閘門，結果與未啟用完全相同且不報錯。"""
    df = losing_then_recovering_klines(1200)
    off = run_equity(df)
    on = run_equity(df, use_settlement_gate=True)

    pd.testing.assert_frame_equal(off["equity_curve"], on["equity_curve"])
    pd.testing.assert_frame_equal(off["trades"], on["trades"])
    assert off["summary"]["total_return"] == on["summary"]["total_return"]


# ------------------------------------------------------------------ 組合路徑（T023）

def test_portfolio_path_signals_unsupported_when_gates_enabled():
    """組合路徑不支援閘門時 MUST 可觀察（research.md D7：沉默忽略會讓使用者
    誤以為風控在保護他）。"""
    from config.config import SingleStrategyParams
    from portfolio_backtester import PortfolioBacktester, warn_if_entry_gates_enabled

    cfg_like = type("C", (), {})()
    cfg_like.default = SingleStrategyParams(use_dd_gate=True)
    cfg_like.ticker_overrides = {}

    with pytest.warns(UserWarning, match="組合回測路徑尚未支援進場閘門"):
        warn_if_entry_gates_enabled(cfg_like)

    # 閘門關閉時不得發出警示（否則警示會被雜訊淹沒）
    quiet = type("C", (), {})()
    quiet.default = SingleStrategyParams()
    quiet.ticker_overrides = {"0050.TW": SingleStrategyParams()}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        warn_if_entry_gates_enabled(quiet)

    assert callable(PortfolioBacktester.run_portfolio_backtest)
