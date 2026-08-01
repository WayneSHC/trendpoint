# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
B 段驅動腳本的離線驗收。

B 段本身需要真實資料，但**驅動腳本的正確性不需要**——情境矩陣是否完整、
現貨是否正確略過結算日閘門、期貨是否含空方、校準是否在對照之前、
判讀是否用對指標，全部可以用合成序列釘死。

這一層測試的價值在於：真的拿到真實資料時，跑出來的數字若有問題，
可以先排除「腳本本身寫錯」這個可能。
"""

import pandas as pd
import pytest

import run_b_segment as bs
from config.config import SystemConfig
from monte_carlo import bootstrap_trades
from instruments import AssetClass, ContractSpec, Instrument, equity_instrument

from bos_volume_fixtures import daily_klines, futures_daily_klines

TXF = Instrument(id="TXF", asset_class=AssetClass.FUTURES, source="mock",
                 timeframes=["daily"],
                 contract=ContractSpec(point_value=200.0, tick_size=1.0,
                                       exchange_fee_per_lot=20.0))
EQUITY = equity_instrument("0050.TW")


@pytest.fixture
def cfg():
    return SystemConfig()


# ---------------------------------------------------------------- 情境矩陣

def test_scenario_matrix_covers_all_three_features():
    labels = [s[0] for s in bs.SCENARIOS]
    assert labels[0].startswith("基準"), "第一列必須是基準，判讀以它為比較對象"
    joined = " ".join(labels)
    for feature in ("量能", "回撤閘門", "結算日閘門", "三項全開"):
        assert feature in joined, f"情境矩陣缺少 {feature}"


def test_equity_skips_settlement_gate_row(cfg):
    """現貨標的：結算日閘門列須明示略過，不得混進可比較的數字。"""
    rows = bs.run_scenarios(daily_klines(400), EQUITY, cfg)
    settle = next(r for r in rows if "結算日" in r["label"])
    assert settle["skipped"] is True
    assert "僅期貨適用" in settle["note"]
    assert "max_drawdown" not in settle, "略過的列不得帶著看似可比的指標"


def test_futures_runs_settlement_gate_row(cfg):
    """期貨標的：結算日閘門列實跑並產出指標。"""
    rows = bs.run_scenarios(futures_daily_klines(400), TXF, cfg)
    settle = next(r for r in rows if "結算日" in r["label"])
    assert settle["skipped"] is False
    for key in ("total_return", "max_drawdown", "calmar", "expectancy", "total_trades"):
        assert key in settle and settle[key] == settle[key], f"{key} 為 NaN"


def test_all_active_rows_carry_the_decision_metrics(cfg):
    """每一列都必須帶齊兩把尺所需的指標（缺一項就無法判讀）。"""
    rows = bs.run_scenarios(futures_daily_klines(400), TXF, cfg)
    for r in rows:
        if r.get("skipped"):
            continue
        for key in ("max_drawdown", "calmar", "expectancy", "profit_factor", "total_trades"):
            assert key in r, f"{r['label']} 缺少 {key}"


# ---------------------------------------------------------------- 覆寫不落地

def test_overrides_do_not_touch_config(cfg):
    """組態覆寫只在記憶體內——跑完後 config 物件必須原封不動。

    這條守的是一個真實風險：若腳本為了跑對照而寫回 config.yaml，
    使用者的預設值會被實驗值污染，而且是靜默的。
    """
    before = (cfg.strategy.default.use_bos_volume,
              cfg.strategy.default.use_dd_gate,
              cfg.strategy.default.use_settlement_gate,
              cfg.strategy.default.dd_limit_pct)

    bs.run_scenarios(daily_klines(400), EQUITY, cfg, dd_limit_pct=0.07)

    after = (cfg.strategy.default.use_bos_volume,
             cfg.strategy.default.use_dd_gate,
             cfg.strategy.default.use_settlement_gate,
             cfg.strategy.default.dd_limit_pct)
    assert before == after == (False, False, False, 0.20)


def test_scenarios_actually_change_behaviour(cfg):
    """啟用 BOS 量能確認的那一列必須與基準不同，否則覆寫沒有真的傳進引擎。"""
    rows = bs.run_scenarios(daily_klines(), EQUITY, cfg)
    base = rows[0]
    vol = next(r for r in rows if "量能" in r["label"])
    assert vol["total_trades"] < base["total_trades"], \
        "啟用量能確認後交易數未下降——覆寫可能沒傳進引擎"


def test_dd_limit_calibration_is_applied(cfg):
    """校準出的門檻須真的用於回撤閘門那一列（不同門檻 → 不同結果）。"""
    df = daily_klines()
    loose = bs.run_scenarios(df, EQUITY, cfg, dd_limit_pct=0.90)
    tight = bs.run_scenarios(df, EQUITY, cfg, dd_limit_pct=0.01)

    loose_gate = next(r for r in loose if r["label"] == "啟用回撤閘門")
    tight_gate = next(r for r in tight if r["label"] == "啟用回撤閘門")
    assert tight_gate["total_trades"] < loose_gate["total_trades"], \
        "門檻收緊後交易數未下降——dd_limit_pct 沒有被套用"


def test_resume_threshold_stays_below_limit(cfg):
    """恢復門檻須嚴格小於封鎖門檻，否則 schema 會拒絕（本腳本自行推導該值）。"""
    # 直接以極端門檻執行，若推導錯誤會在引擎層拋 ValueError
    rows = bs.run_scenarios(daily_klines(400), EQUITY, cfg, dd_limit_pct=0.02)
    gate = next(r for r in rows if r["label"] == "啟用回撤閘門")
    assert gate["skipped"] is False


# ---------------------------------------------------------------- SC-015 校準

def test_calibration_reports_unavailable_without_trades():
    out = bs.calibrate_dd_limit({"trade_returns": []})
    assert out["available"] is False


def test_calibration_returns_positive_threshold_from_the_drawdown_tail():
    """回撤為負值，門檻取其絕對值。"""
    returns = [0.05, -0.03, 0.02, -0.08, 0.01, -0.02] * 10
    out = bs.calibrate_dd_limit({"trade_returns": returns}, n_sims=500)
    assert out["available"] is True
    assert out["deep_max_drawdown"] <= 0.0
    assert out["suggested_dd_limit_pct"] == abs(out["deep_max_drawdown"])
    assert 0.0 < out["suggested_dd_limit_pct"] < 1.0


def test_calibration_takes_the_deep_tail_not_the_shallow_one():
    """門檻必須取回撤分布的**深尾**（帶號分布第 5 百分位）。

    這條釘的是一個真的犯過、而且通過了 CI 的錯：`bootstrap_trades` 的
    `max_drawdown` 是**帶號負值**，所以「回撤幅度的第 95 百分位」對應到
    帶號分布的**第 5** 百分位。初版取了 95，於是每檔標的都校準出 0.00%
    的門檻——形同閘門永不觸發，而報表看起來一切正常。

    舊測試用的是虧損偏多的序列，兩端都明顯為負，取錯端也照樣通過。
    這裡改用**虧損稀有**的序列才有鑑別力：48 抽 1 的虧損率下，約 36%
    的重抽路徑一次都沒抽中虧損，其回撤恰為 0，淺尾因此被釘在 0；
    深尾則是連續抽中虧損的路徑，明顯為負。兩端不可能混淆。

    虧損稀有正是真實資料的樣貌——B 段首跑各標的僅 6~7 筆交易、
    勝率 67~100%，淺尾自然是 0.00%。
    """
    returns = [0.03] * 47 + [-0.15]
    out = bs.calibrate_dd_limit({"trade_returns": returns}, n_sims=2000)
    mc = bootstrap_trades(returns, n_sims=2000, seed=42)

    assert out["deep_max_drawdown"] == mc["max_drawdown"][5]
    assert out["suggested_dd_limit_pct"] == abs(mc["max_drawdown"][5])

    # 鑑別力：淺尾在此分布下趨近 0，取錯端會得到不可用的門檻
    assert abs(mc["max_drawdown"][95]) < 0.01, "淺尾不夠接近 0，此 fixture 失去鑑別力"
    assert out["suggested_dd_limit_pct"] > 0.02


def test_calibration_warns_on_small_sample():
    """樣本 < 30 筆時必須帶 warning——那不是蒙地卡羅能補救的問題。"""
    out = bs.calibrate_dd_limit({"trade_returns": [0.01, -0.02, 0.03]}, n_sims=200)
    assert out["available"] is True
    assert out.get("warning"), "小樣本必須示警，否則會被當成可用的門檻"


# ---------------------------------------------------------------- 報表判讀

def test_report_uses_different_yardsticks(capsys, cfg):
    """風控閘門列不得以總報酬判定；訊號濾網列以期望值判定。"""
    rows = [
        {"label": "基準（三項皆關閉）", "kind": "baseline", "skipped": False, "total_return": 0.10,
         "max_drawdown": -0.10, "calmar": 1.0, "sharpe": 0.8, "total_trades": 40,
         "win_rate": 0.5, "profit_factor": 1.5, "expectancy": 0.003},
        # 風控：報酬下降但 MDD 改善 → 應判為改善（閘門確實有封鎖，故判定成立）
        {"label": "啟用回撤閘門", "kind": "risk", "skipped": False, "total_return": 0.04,
         "max_drawdown": -0.05, "calmar": 1.4, "sharpe": 0.9, "total_trades": 25,
         "win_rate": 0.52, "profit_factor": 1.6, "expectancy": 0.002, "blocked_bars": 31},
        # 訊號濾網：交易數下降且期望值未改善
        {"label": "啟用 BOS 量能確認", "kind": "signal", "skipped": False, "total_return": 0.06,
         "max_drawdown": -0.11, "calmar": 0.9, "sharpe": 0.7, "total_trades": 18,
         "win_rate": 0.55, "profit_factor": 1.4, "expectancy": 0.001},
    ]
    bs.print_report("TEST", EQUITY, {"available": False, "reason": "測試"}, rows)
    out = capsys.readouterr().out

    assert "風險調整後改善" in out, "風控列應以 MDD/Calmar 判定為改善"
    assert "不作為判準" in out, "風控列必須標明總報酬不作為判準"
    assert "期望值未改善" in out, "訊號濾網列應以期望值判定"
    assert "run_walk_forward" in out, "必須提醒單次對照不足以支撐採用決定"


def test_untriggered_gate_reports_no_data_not_no_improvement(capsys):
    """閘門一根都沒封鎖時，必須說「未觸發」而非「未改善」。

    這條守的是一個實際發生過、而且比計算錯誤更危險的判讀缺陷：閘門未觸發時
    所有指標差必然為 0，舊版報表據此印出「風險調整後未改善」——把**沒有證據**
    呈現成**有證據說沒用**。這兩者在「是否改為預設啟用」的決策上完全相反。

    真實情境並不罕見：SC-015 以重抽分布深尾校準的門檻（實測 8.9%~17.6%）
    按定義就比單一歷史路徑的 MDD（實測 5.4%~8.7%）深，故在對照中極少觸發。
    """
    rows = [
        {"label": "基準（三項皆關閉）", "kind": "baseline", "skipped": False, "total_return": 0.10,
         "max_drawdown": -0.10, "calmar": 1.0, "sharpe": 0.8, "total_trades": 40,
         "win_rate": 0.5, "profit_factor": 1.5, "expectancy": 0.003},
        # 逐欄與基準相同 + 封鎖 0 根 = 閘門從未啟動
        {"label": "啟用回撤閘門", "kind": "risk", "skipped": False, "total_return": 0.10,
         "max_drawdown": -0.10, "calmar": 1.0, "sharpe": 0.8, "total_trades": 40,
         "win_rate": 0.5, "profit_factor": 1.5, "expectancy": 0.003, "blocked_bars": 0},
    ]
    bs.print_report("TEST", EQUITY, {"available": False, "reason": "測試"}, rows)
    out = capsys.readouterr().out

    assert "未觸發" in out and "無對照數據" in out
    assert "未改善" not in out, "未觸發被誤報為未改善——這會導向相反的決策"
    assert "不得據此判定" in out


def test_blocked_bars_counts_only_blocked_rows():
    """`block_reason` 非空的根數即封鎖數；欄位不存在（閘門未啟用）時為 0。"""
    with_gate = {"equity_curve": [
        {"equity": 1.0, "block_reason": ""},
        {"equity": 1.0, "block_reason": "drawdown"},
        {"equity": 1.0, "block_reason": "settlement"},
        {"equity": 1.0, "block_reason": "drawdown+settlement"},
    ]}
    assert bs._blocked_bars(with_gate) == 3

    # 條件輸出欄：閘門未啟用時 equity_curve 根本沒有 block_reason
    assert bs._blocked_bars({"equity_curve": [{"equity": 1.0}, {"equity": 1.0}]}) == 0
    assert bs._blocked_bars({}) == 0


def test_run_scenarios_reports_blocked_bars(cfg):
    """實跑產生的列必須帶 blocked_bars，否則報表無從區分未觸發與未改善。"""
    rows = bs.run_scenarios(daily_klines(400), EQUITY, cfg, dd_limit_pct=0.02)
    gate = next(r for r in rows if r["label"] == "啟用回撤閘門")
    assert "blocked_bars" in gate
    # 門檻收到 2% 時閘門必然觸發，否則這條測試永遠為綠
    assert gate["blocked_bars"] > 0


def test_report_flags_small_sample_calibration(capsys):
    bs.print_report("TEST", EQUITY,
                    {"available": True, "n_source_trades": 5,
                     "deep_max_drawdown": -0.12, "suggested_dd_limit_pct": 0.12,
                     "warning": "交易樣本僅 5 筆 (<30)，任何統計推論都不可靠。"},
                    [])
    out = capsys.readouterr().out
    assert "不可用於定案" in out


# ---------------------------------------------------------------- CLI 前置

def test_run_reports_missing_database(tmp_path, monkeypatch, capsys):
    """無資料庫時給出可操作的訊息並回非零碼（供 CI 判斷）。"""
    cfg = SystemConfig()
    cfg.data.database_path = str(tmp_path / "nope.db")
    monkeypatch.setattr(bs, "load_config", lambda: cfg)

    assert bs.run() == 1
    assert "run_ingestion" in capsys.readouterr().out


def test_every_scenario_declares_its_yardstick():
    """每個情境都必須宣告 kind——判讀方向不可取決於顯示名稱的字串比對。"""
    kinds = {s[2] for s in bs.SCENARIOS}
    assert kinds <= {"baseline", "signal", "risk", "combined"}
    assert bs.SCENARIOS[0][2] == "baseline"
    # 三項全開同時含訊號濾網與風控閘門，必須標為 combined
    combined = [s for s in bs.SCENARIOS if s[2] == "combined"]
    assert len(combined) == 1
    ov = combined[0][1]
    assert ov.get("use_bos_volume") and ov.get("use_dd_gate"), \
        "combined 情境必須同時含兩類機制，否則不該標為 combined"


def test_combined_scenario_refuses_to_attribute(capsys):
    """混合情境必須兩把尺都列出，且明說無法歸因——不得給單一結論。"""
    rows = [
        {"label": "基準（三項皆關閉）", "kind": "baseline", "skipped": False,
         "total_return": 0.10, "max_drawdown": -0.10, "calmar": 1.0, "sharpe": 0.8,
         "total_trades": 40, "win_rate": 0.5, "profit_factor": 1.5, "expectancy": 0.003},
        {"label": "三項全開", "kind": "combined", "skipped": False,
         "total_return": 0.05, "max_drawdown": -0.06, "calmar": 1.3, "sharpe": 0.9,
         "total_trades": 15, "win_rate": 0.6, "profit_factor": 1.8, "expectancy": 0.004,
         "blocked_bars": 12},
    ]
    bs.print_report("TEST", EQUITY, {"available": False, "reason": "測試"}, rows)
    out = capsys.readouterr().out
    assert "訊號面" in out and "風控面" in out, "混合情境必須兩把尺都列"
    assert "無法歸因" in out


def test_run_scenarios_rows_carry_kind(cfg):
    """實跑產生的列也必須帶 kind（報表判讀靠它）。"""
    rows = bs.run_scenarios(daily_klines(400), EQUITY, cfg)
    assert all("kind" in r for r in rows)
