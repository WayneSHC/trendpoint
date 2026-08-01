# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
匯入範圍的兩項行為鎖定：

1. **現貨不再匯入 5 分線**——`stock_*_5m` 表從未被任何程式讀取，監控端的
   5 分線一律現抓。這裡把「不再產生該表」釘死，避免日後有人「順手補回來」。
2. **`--equity-only` 確實跳過期貨**——排程監控用它預熱日線表；若這個開關失效，
   每 30 分鐘一次的排程會觸發 TAIFEX 自 1998 年起的全歷史回填。

兩項都是**負向保證**（某件事不該發生），故必須有測試——正向路徑的測試抓不到。
"""

import os

import pandas as pd
import pytest

import run_ingestion
from instruments import AssetClass, equity_instrument


def test_equity_instrument_declares_daily_only():
    """現貨時框只有 daily：ingestion 迭代時框，宣告即是唯一的產生依據。"""
    inst = equity_instrument("2330.TW")
    assert inst.timeframes == ["daily"], \
        "現貨若再宣告 5m，run_ingestion 會重新產生沒人讀的 stock_*_5m 表"
    assert inst.asset_class == AssetClass.EQUITY


def test_table_name_for_5m_still_resolvable():
    """表名函式與時框宣告無關——既有 DB 的舊 5m 表仍須能被指名刪除。"""
    from db_security import table_name_for
    assert table_name_for(equity_instrument("2330.TW"), "5m") == "stock_2330_TW_5m"


def _fake_frame(n=30):
    idx = pd.bdate_range("2024-01-01", periods=n, name="datetime")
    close = pd.Series([100.0 + i * 0.1 for i in range(n)], index=idx)
    return pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close,
         "volume": 1000.0},
        index=idx,
    )


@pytest.fixture
def ingestion_env(tmp_path, monkeypatch):
    """把匯入導向暫存 DB 與替身 adapter，記錄每次寫表。"""
    monkeypatch.chdir(tmp_path)
    db_path = str(tmp_path / "t.db")

    written = []

    class FakeAdapter:
        def fetch(self, instrument, timeframe):
            return _fake_frame()

    monkeypatch.setattr(run_ingestion, "get_adapter", lambda source: FakeAdapter())
    monkeypatch.setattr(run_ingestion, "save_to_csv", lambda df, path: True)
    monkeypatch.setattr(run_ingestion, "validate_data_contract",
                        lambda df, quality, asset_class: None)
    monkeypatch.setattr(run_ingestion, "save_to_sqlite",
                        lambda df, table, path: written.append(table) or True)

    taifex_calls = []
    monkeypatch.setattr(run_ingestion, "_ingest_taifex",
                        lambda inst, tf, cfg, path, adapter: taifex_calls.append(inst.id))

    class Env:
        tables = written
        taifex = taifex_calls

        def set_config(self, tickers, instruments):
            cfg = run_ingestion.load_config()
            cfg.data.database_path = db_path
            cfg.data.tickers = tickers
            cfg.data.instruments = instruments
            monkeypatch.setattr(run_ingestion, "load_config", lambda: cfg)
            return cfg

    return Env()


def test_ingestion_writes_no_5m_table(ingestion_env):
    """現貨匯入只產生日線表，不再產生 5 分線表。"""
    ingestion_env.set_config(["2330.TW", "0050.TW"], [])
    run_ingestion.run()

    assert ingestion_env.tables == ["stock_2330_TW_daily", "stock_0050_TW_daily"]
    assert not any(t.endswith("_5m") for t in ingestion_env.tables), \
        "又開始寫沒人讀的 5 分線表了"


def test_equity_only_skips_futures(ingestion_env):
    """--equity-only 跳過期貨——排程監控靠這個避免觸發 TAIFEX 全歷史回填。"""
    from instruments import ContractSpec, Instrument

    txf = Instrument(id="TXF", asset_class=AssetClass.FUTURES, source="taifex",
                     timeframes=["daily"],
                     contract=ContractSpec(point_value=200.0, tick_size=1.0,
                                           exchange_fee_per_lot=20.0))
    ingestion_env.set_config(["2330.TW"], [txf])

    run_ingestion.run(equity_only=True)
    assert ingestion_env.taifex == [], "--equity-only 仍呼叫了 TAIFEX 匯入（會全歷史回填）"
    assert ingestion_env.tables == ["stock_2330_TW_daily"]


def test_default_run_still_includes_futures(ingestion_env):
    """預設（不帶旗標）行為不變：期貨照常匯入。"""
    from instruments import ContractSpec, Instrument

    txf = Instrument(id="TXF", asset_class=AssetClass.FUTURES, source="taifex",
                     timeframes=["daily"],
                     contract=ContractSpec(point_value=200.0, tick_size=1.0,
                                           exchange_fee_per_lot=20.0))
    ingestion_env.set_config(["2330.TW"], [txf])

    run_ingestion.run()
    assert ingestion_env.taifex == ["TXF"], "預設匯入不得跳過期貨"


def test_alert_workflow_warms_the_daily_table():
    """排程工作流程必須在監控之前預熱日線表，且必須帶 --equity-only。

    這條測試守的是一個**環境前提**而非程式邏輯：少了預熱步驟，所有讀庫的
    監控功能（spec 014 均線通知）在排程環境永遠靜默無效；少了 --equity-only，
    每 30 分鐘的排程會撞上 TAIFEX 全歷史回填。
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(repo_root, ".github", "workflows", "alert_scheduler.yml")
    with open(path, encoding="utf-8") as fh:
        content = fh.read()

    assert "run_ingestion.py --equity-only" in content, \
        "排程工作流程缺少日線預熱步驟（或漏了 --equity-only）"
    assert content.index("run_ingestion.py --equity-only") < content.index("monitor_signals.py"), \
        "預熱步驟必須在監控之前"
