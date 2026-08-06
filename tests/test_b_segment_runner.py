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


def test_calibration_reports_contract_violation_without_killing_the_run():
    """逐筆報酬 <= -100%（分母語意壞掉）不可校準，但不得連坐其他標的。

    函式庫層（bootstrap_trades）硬失敗是對的；批次驅動層必須把它轉成
    「該檔不可校準 + 原因」，否則一檔壞資料會丟掉整輪已算完的結果。
    """
    out = bs.calibrate_dd_limit({"trade_returns": [0.02, -1.4, 0.03]}, n_sims=100)
    assert out["available"] is False
    assert "-100%" in out["reason"], "原因須原文轉載，否則現場無從判斷是哪類壞法"


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


# ------------------------------------------------------- 資料指紋（P0 修復）

def test_fingerprint_changes_when_values_change():
    """指紋必須對**數值**敏感——筆數相同但數值不同是實際發生過的情形。

    yfinance 對相同標的、相同期間會回傳筆數一致而數值有別的資料
    （auto_adjust 的還原價取決於當下的股利/分割歷史）。只比筆數抓不到。
    """
    df = daily_klines(200)
    a = bs.fingerprint(df, "stock_X_daily")

    tweaked = df.copy()
    tweaked.iloc[10, tweaked.columns.get_loc("close")] += 0.01
    b = bs.fingerprint(tweaked, "stock_X_daily")

    assert a["rows"] == b["rows"] == 200, "此 fixture 應維持筆數相同才有鑑別力"
    assert a["sha256"] != b["sha256"]


def test_fingerprint_is_stable_for_identical_data():
    df = daily_klines(200)
    assert bs.fingerprint(df, "t")["sha256"] == bs.fingerprint(df.copy(), "t")["sha256"]


def test_report_prints_fingerprint(capsys, cfg):
    """指紋必須與數字印在同一份報告——分開存放等於沒存。"""
    fp = {"table": "stock_0050_TW_daily", "rows": 2433,
          "start": "2016-08-01", "end": "2026-07-31", "sha256": "deadbeefdeadbeef"}
    bs.print_report("0050.TW", EQUITY, {"available": False, "reason": "測試"}, [], fingerprint=fp)
    out = capsys.readouterr().out
    assert "stock_0050_TW_daily" in out and "deadbeefdeadbeef" in out and "2433" in out


def test_load_frame_returns_fingerprint_of_what_was_read(tmp_path, cfg):
    """指紋須取自**實際餵進回測的資料表**，而非 data/*.csv。

    匯入失敗的標的不會產出 CSV，卻仍可能被讀到資料庫裡的舊表——
    對 CSV 取指紋恰好漏掉這個情形（run 30706957226 的 0050.TW）。
    """
    from db_security import table_name_for
    import sqlite3

    df = daily_klines(300)
    db = tmp_path / "t.db"
    table = table_name_for(EQUITY, "daily")
    conn = sqlite3.connect(str(db))
    try:
        df.to_sql(table, conn, if_exists="replace")
    finally:
        conn.close()

    loaded = bs.load_frame(EQUITY, str(db))
    assert loaded is not None
    got_df, fp = loaded
    assert fp["table"] == table
    assert fp["rows"] == len(got_df) == 300
    assert len(fp["sha256"]) == 16


# --------------------------------------------- 過度封鎖 = 停用策略（P0 修復）

def _row(label, kind, **kw):
    base = {"label": label, "kind": kind, "skipped": False, "total_return": 0.10,
            "max_drawdown": -0.10, "calmar": 1.0, "sharpe": 0.8, "total_trades": 40,
            "win_rate": 0.5, "profit_factor": 1.5, "expectancy": 0.003}
    base.update(kw)
    return base


def test_overblocking_gate_is_not_reported_as_risk_improvement(capsys):
    """封鎖絕大多數根數 → 判為「停用策略」，不得判為「風險調整後改善」。

    真實案例：TXF 在 dd_limit=0.20 下封鎖 6919/7000 根、只剩 1 筆交易，
    MDD 從 -98.5% 改善到 -22.8%。那不是風險管理的成果，是策略被關掉——
    且單標的回測中回撤閘門為單向閂鎖，空手後回撤不再回復，一旦觸發即永久。
    """
    rows = [
        _row("基準（三項皆關閉）", "baseline", max_drawdown=-0.985, total_trades=7),
        _row("啟用回撤閘門", "risk", max_drawdown=-0.228, total_trades=1,
             blocked_bars=6919, blocked_ratio=0.988),
    ]
    bs.print_report("TXF", EQUITY, {"available": False, "reason": "測試"}, rows)
    out = capsys.readouterr().out

    assert "實質停用策略" in out
    assert "風險調整後改善" not in out, "策略被關掉被誤報為風險改善"
    assert "單向閂鎖" in out


def test_moderate_blocking_still_gets_a_verdict(capsys):
    """封鎖比例在合理範圍內時，仍應以 MDD／Calmar 給出判定。"""
    rows = [
        _row("基準（三項皆關閉）", "baseline", max_drawdown=-0.20, calmar=0.5),
        _row("啟用回撤閘門", "risk", max_drawdown=-0.12, calmar=0.9, total_trades=30,
             blocked_bars=120, blocked_ratio=0.06),
    ]
    bs.print_report("TEST", EQUITY, {"available": False, "reason": "測試"}, rows)
    out = capsys.readouterr().out

    assert "風險調整後改善" in out
    assert "實質停用策略" not in out


# ------------------------------------------------- 保證金死亡 / 槓桿（P1）

def test_report_surfaces_effective_leverage(capsys):
    """期貨報告必須印出名目槓桿——它由組態直接決定，且參數名稱會誤導。

    `margin_utilization: 0.5` 讀起來像「只動用一半資金」，實際是
    0.5 / 0.055 = 9.09× 名目槓桿，指數反向 11% 即歸零。
    """
    bs.print_report("TXF", TXF, {"available": False, "reason": "測試"}, [], leverage=9.09)
    out = capsys.readouterr().out
    assert "9.09×" in out
    assert "11.0%" in out, "須換算成『反向多少即歸零』——倍數本身對讀者不夠具體"
    assert "槓桿設定" in out, "高槓桿下須明示結果反映的是槓桿而非策略"


def test_low_leverage_does_not_trigger_the_warning(capsys):
    bs.print_report("TXF", TXF, {"available": False, "reason": "測試"}, [], leverage=2.0)
    out = capsys.readouterr().out
    assert "2.00×" in out
    assert "槓桿設定" not in out


def test_margin_dead_baseline_refuses_to_judge(capsys):
    """基準已保證金死亡時不得判讀——交易筆數反映的是「帳戶何時沒錢」。

    TXF 實測：權益剩 2.3%，而 TXF@20,000 每口保證金 220,000，連一口都下不起。
    此時「啟用某功能後交易數 -6」講的是帳戶餘額，不是濾網效果。
    """
    rows = [
        _row("基準（三項皆關閉）", "baseline", total_return=-0.9769,
             max_drawdown=-0.985, total_trades=7, margin_dead=True),
        _row("啟用 BOS 量能確認", "signal", total_return=-0.9220,
             max_drawdown=-0.9473, total_trades=5, expectancy=-0.131),
    ]
    bs.print_report("TXF", TXF, {"available": False, "reason": "測試"}, rows)
    out = capsys.readouterr().out

    assert "保證金死亡" in out
    assert "不進行判讀" in out
    assert "期望值改善" not in out, "基準已死仍給出濾網判定"


def test_margin_dead_is_flagged_even_when_baseline_survives(capsys):
    """非基準列死亡時仍須標示，但判讀照常進行。"""
    rows = [
        _row("基準（三項皆關閉）", "baseline"),
        _row("啟用回撤閘門", "risk", total_trades=1, blocked_bars=50,
             blocked_ratio=0.02, margin_dead=True, max_drawdown=-0.05, calmar=1.4),
    ]
    bs.print_report("TEST", TXF, {"available": False, "reason": "測試"}, rows)
    out = capsys.readouterr().out
    assert "保證金死亡" in out and "啟用回撤閘門" in out
    assert "不進行判讀" not in out


def test_margin_dead_never_set_for_equity(cfg):
    """現貨無保證金概念，此旗標恆為 False。"""
    rows = bs.run_scenarios(daily_klines(400), EQUITY, cfg)
    assert all(r.get("margin_dead") is False for r in rows if not r.get("skipped"))


def test_futures_capital_can_afford_a_lot_at_configured_leverage():
    """期貨資本與槓桿被合約規格綁死，組態必須讓至少 1 口下得起。

    大台一口名目值 = 指數 × 200；指數 25,000 時為 500 萬。以現貨的 100 萬資本，
    1× 槓桿（margin_utilization = margin_rate）連一口都下不起——實測 0 筆交易，
    而報表只會顯示一張空表，不會有任何錯誤。

    這條測試把「降槓桿必須同時檢查資本」這個非直觀的耦合釘死。
    """
    from config import load_config
    from instruments import InstrumentRegistry
    from trading_costs import for_asset_class

    cfg = load_config()
    reg = InstrumentRegistry.from_config(cfg.data.tickers, cfg.data.instruments)
    txf = reg.resolve("TXF")
    _, sizer = for_asset_class(txf, cfg)

    # 台指史上高點量級；能在此價位下單即全歷史可交易
    assert sizer.size(cfg.backtest.futures_init_capital, 25000.0) >= 1.0, (
        "期貨資本不足以在高價位下 1 口——低槓桿下會得到 0 筆交易的空表。"
        "調降 margin_utilization 時必須同步檢查 futures_init_capital。"
    )


def test_futures_uses_its_own_capital(cfg):
    """期貨路徑須採 futures_init_capital，不得沿用現貨的 init_capital。"""
    assert cfg.backtest.futures_init_capital != cfg.backtest.init_capital
    rows = bs.run_scenarios(futures_daily_klines(400), TXF, cfg)
    base = next(r for r in rows if r["kind"] == "baseline")
    assert base["total_trades"] > 0, "期貨基準 0 筆交易——資本可能沿用了現貨的 100 萬"
