# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
濾網累加階梯（`run_ablation.build_cumulative_ladder`）。

階梯的價值全在**軌跡可讀**：交易數與期望值逐層怎麼走。只要單調性或順序被
改壞，軌跡就不再代表「逐層加回」，而讀者不會察覺——表格看起來一模一樣。
本檔即釘住那些不變量。
"""

import pytest

import run_ablation as ra
from backtester import BacktestEngine
from acceptance_fixtures import make_klines


def _sets():
    return [d for _, d in ra.build_cumulative_ladder()]


def test_ladder_starts_bare_and_ends_at_full_stack():
    sets_ = _sets()
    assert sets_[0] == ra._LADDER_KEYS, "第一列必須停用全部階梯濾網（裸訊號）"
    assert sets_[-1] == frozenset(), "最後一列必須是全濾網（＝基準）"


def test_ladder_is_strictly_monotone():
    """每往下一列，停用集合必須**嚴格縮小**——這就是「逐層加回」的定義。"""
    sets_ = _sets()
    for prev, cur in zip(sets_, sets_[1:]):
        assert cur < prev, f"{cur} 不是 {prev} 的真子集——階梯不再單調"


def test_ladder_has_one_row_per_filter_plus_bare():
    labels = [lbl for lbl, _ in ra.build_cumulative_ladder()]
    assert len(labels) == len(ra._LADDER_ORDER) + 1
    assert len(set(labels)) == len(labels), "標籤重複會讓兩列在報表中無法區分"


def test_global_comes_before_regime():
    """順序不可對調，理由是進場端的短路邏輯。

    `global_ok = global_filter_ok or ('global' in disabled)`，而
    `global_filter_ok = (close > mid_price) and regime_ok`。只要 `global` 還在
    停用集合裡，`regime_ok` 對進場**毫無影響**——把 regime 排在 global 之前，
    那一階會是純空轉，並讓讀者誤以為市況濾網沒有作用。
    """
    order = [k for k, _ in ra._LADDER_ORDER]
    assert order.index("global") < order.index("regime")


def test_ladder_excludes_structure_and_bos_volume():
    """structure 不在階梯內（停用它＝無條件進場，那不是裸訊號）；
    bos_volume 不在階梯內（組態預設關閉，加進來是與前一列相同的空列）。"""
    assert "structure" not in ra._LADDER_KEYS
    assert "bos_volume" not in ra._LADDER_KEYS


def test_bare_signal_trades_at_least_as_much_as_full_stack():
    """濾網只能減少交易，不能增加——這是「濾網不創造邊際」的可執行形式。

    以合成序列實跑階梯首末兩列。若哪天有人把某道「濾網」實作成會**放行**
    原本不成立的進場，這條會轉紅。
    """
    from config import load_config

    cfg = load_config()
    df = make_klines(600, freq="5min")
    engine = BacktestEngine(config=cfg)
    rows = ra.run_ladder_for_ticker(engine, cfg, "0050.TW", df)

    bare, full = rows[0], rows[-1]
    assert bare["total_trades"] >= full["total_trades"], (
        f"裸訊號 {bare['total_trades']} 筆 < 全濾網 {full['total_trades']} 筆"
        "——某道濾網放行了原本不成立的進場"
    )


def test_ladder_trade_counts_are_monotone_non_increasing():
    """逐層加回濾網，交易數必須單調不增。"""
    from config import load_config

    cfg = load_config()
    df = make_klines(600, freq="5min")
    engine = BacktestEngine(config=cfg)
    counts = [r["total_trades"] for r in
              ra.run_ladder_for_ticker(engine, cfg, "0050.TW", df)]
    for prev, cur in zip(counts, counts[1:]):
        assert cur <= prev, f"交易數在階梯中上升：{counts}"
