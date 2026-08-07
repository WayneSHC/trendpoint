# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
微型臺指期貨（TMF）接入，並將小型臺指（MTX）由 mock 換為 TAIFEX 真源。

TX／MTX／TMF 追蹤同一個指數，訊號與回測邏輯與商品規模無關（既有測試已涵蓋）。
本檔只斷言**會因商品而異**的四處，也就是接錯就會靜默出錯的地方：

1. TAIFEX/FinMind 查詢代碼對照（接錯 → 抓到別的商品，資料看起來仍正常）
2. 逐商品回填起始日（設錯 → 只是變慢或截掉資料，不會變紅）
3. 契約規格導出的摩擦成本（設錯 → 績效數字全錯但仍收斂）
4. 推播文案的商品別（缺了 → 三則同源通知看起來像重複發送）
"""

from datetime import date

import pytest

from config import load_config
from config.config import DataConfig, FuturesCostConfig, FuturesDataSourceConfig
from data_sources.finmind_source import FinMindAdapter
from data_sources.taifex_source import TaifexAdapter
from db_security import raw_table_name_for, table_name_for
from instruments import AssetClass, Instrument
from trading_costs import FuturesCostModel

# 台指類三商品的 TAIFEX 權威值（每點價值、每口每邊定額費 = 經手費 + 結算費）
AUTHORITY = {
    "TXF": (200.0, 20.0, "TX"),
    "MTX": (50.0, 12.5, "MTX"),
    "TMF": (10.0, 8.0, "TMF"),
}


def _instrument(inst_id: str) -> Instrument:
    cfg = load_config()
    matches = [i for i in cfg.data.instruments if i.id == inst_id]
    assert matches, f"config 未宣告 instrument '{inst_id}'"
    return matches[0]


# ---------------------------------------------------------------------------
# 1. 組態宣告與契約規格
# ---------------------------------------------------------------------------

def test_config_declares_all_three_index_futures_on_real_source():
    """三個台指類商品皆已宣告，且皆走 TAIFEX 真源（MTX 不再是 mock）。"""
    cfg = load_config()
    futures = {i.id: i for i in cfg.data.instruments
               if i.asset_class == AssetClass.FUTURES}
    assert set(AUTHORITY) <= set(futures), f"缺少商品：{sorted(set(AUTHORITY) - set(futures))}"
    for inst_id in AUTHORITY:
        assert futures[inst_id].source == "taifex", (
            f"{inst_id} 的來源是 {futures[inst_id].source!r}——mock 資料一旦不再帶 "
            f"MOCK 標示就會被當成真資料使用"
        )


@pytest.mark.parametrize("inst_id", sorted(AUTHORITY))
def test_contract_spec_matches_taifex_authority(inst_id):
    point_value, fee, _ = AUTHORITY[inst_id]
    contract = _instrument(inst_id).contract
    assert contract is not None
    assert contract.point_value == pytest.approx(point_value)
    assert contract.exchange_fee_per_lot == pytest.approx(fee)
    assert contract.tick_size == pytest.approx(1.0)      # 台指類最小跳動 1 點


def test_micro_contract_is_one_twentieth_of_large():
    """微台 = 大台 1/20、小台 1/5——規模關係寫成斷言，改錯乘數會被抓到。"""
    tx = _instrument("TXF").contract.point_value
    mtx = _instrument("MTX").contract.point_value
    tmf = _instrument("TMF").contract.point_value
    assert tx == pytest.approx(tmf * 20)
    assert mtx == pytest.approx(tmf * 5)


# ---------------------------------------------------------------------------
# 2. 逐商品回填起始日
# ---------------------------------------------------------------------------

def test_backfill_start_falls_back_to_global_when_not_overridden():
    fs = FuturesDataSourceConfig(backfill_start="1998-07-21",
                                 backfill_start_overrides={"TMF": "2024-07-01"})
    assert fs.backfill_start_for("TXF") == "1998-07-21"
    assert fs.backfill_start_for("TMF") == "2024-07-01"


def test_config_overrides_cover_the_later_listed_products():
    """MTX（2001-04 上市）與 TMF（2024-07 上市）必須有覆寫。

    缺覆寫不會失敗，只會從 1998 年開始送出數百個必然落空的月請求——
    TMF 約 312 個 × 2 秒節流 ≈ 10 分鐘空轉。這種「只變慢不變紅」的錯誤
    最難察覺，故以測試釘住。
    """
    fs = load_config().data.futures_source
    assert fs.backfill_start_for("TXF") == fs.backfill_start
    assert fs.backfill_start_for("MTX") == "2001-04-01"
    assert fs.backfill_start_for("TMF") == "2024-07-01"


def test_override_for_undeclared_instrument_failfast():
    """打錯 id 會靜默退回全域預設，故載入組態時就要擋。"""
    with pytest.raises(ValueError, match="未宣告的 instrument id"):
        DataConfig(
            instruments=[_instrument("TMF")],
            futures_source=FuturesDataSourceConfig(
                backfill_start_overrides={"TFM": "2024-07-01"}),   # 手誤：TFM
        )


@pytest.mark.parametrize("kwargs", [
    {"backfill_start": "2024/07/01"},
    {"backfill_start_overrides": {"TMF": "not-a-date"}},
    {"backfill_start_overrides": {"TMF": "2024-13-01"}},
])
def test_unparseable_backfill_dates_failfast(kwargs):
    """壞日期要在載入時炸，而不是在回填跑了幾百個請求之後。"""
    with pytest.raises(ValueError, match="ISO 日期"):
        FuturesDataSourceConfig(**kwargs)


# ---------------------------------------------------------------------------
# 3. 查詢代碼對照與商品分流
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("inst_id", sorted(AUTHORITY))
def test_commodity_code_mapping(inst_id):
    """我方 id → TAIFEX/FinMind 查詢代碼。只有大台需要對照（TXF → TX）。"""
    expected = AUTHORITY[inst_id][2]
    inst = _instrument(inst_id)
    assert TaifexAdapter(cfg=FuturesDataSourceConfig())._commodity(inst) == expected
    assert FinMindAdapter._commodity(inst) == expected


def test_parse_keeps_only_the_requested_commodity():
    """同一份 TAIFEX 回應含多商品列——各商品只能取到自己的那幾列。

    這是最危險的一種靜默錯誤：代碼配錯時仍會回傳一份看起來完全正常的
    OHLC 序列，只是價位屬於別的商品。
    """
    ad = TaifexAdapter(cfg=FuturesDataSourceConfig())
    text = (
        "Date,Contract,ContractMonth(Week),Open,High,Low,Last,Volume,"
        "SettlementPrice,OpenInterest,TradingSession\n"
        "20260717,TX,202608,44250,44512,42527,42725,103520,42604,110864,一般\n"
        "20260717,MTX,202608,44251,44513,42528,42726,50000,42605,60000,一般\n"
        "20260717,TMF,202608,44252,44514,42529,42727,9000,42606,7000,一般\n"
    )
    for commodity, expected_close in [("TX", 42725.0), ("MTX", 42726.0), ("TMF", 42727.0)]:
        df = ad._parse_csv(text, commodity=commodity)
        assert len(df) == 1, f"{commodity} 應只取到自己的 1 列"
        assert df.iloc[0]["close"] == expected_close


class _RecordingSession:
    """記下每次請求的 payload，供斷言起始日確實逐商品生效。"""

    def __init__(self, content: bytes):
        self.content = content
        self.payloads = []

    def post(self, url, data=None, **kw):
        self.payloads.append(data)

        class _Resp:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                return None

        return _Resp(self.content)


_EMPTY_CSV = (
    "交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,成交量,"
    "結算價,未沖銷契約數,交易時段\n"
).encode("ms950")


def test_fetch_starts_from_per_instrument_backfill_date():
    """fetch() 的起始日取自逐商品覆寫，而非全域值。

    以請求 payload 的 queryStartDate 為證：微台若沿用 1998 全域起點，
    會多出數百個月請求。
    """
    fs = FuturesDataSourceConfig(
        throttle_seconds=0.0, backfill_start="1998-07-21",
        backfill_start_overrides={"TMF": "2024-07-01"},
    )
    session = _RecordingSession(_EMPTY_CSV)
    ad = TaifexAdapter(cfg=fs, session=session, sleeper=lambda _s: None)
    ad._today = lambda: date(2024, 8, 15)

    with pytest.raises(ValueError, match="空資料"):     # fixture 無資料列，僅驗請求範圍
        ad.fetch(_instrument("TMF"), "daily")

    assert session.payloads[0]["commodity_id"] == "TMF"
    assert session.payloads[0]["queryStartDate"] == "2024/07/01"
    assert len(session.payloads) == 2, "2024-07 與 2024-08 各一請求，不應回溯到 1998"


class _CountingGetSession:
    """記錄 GET 次數，供斷言當日端點在一輪內只打一次。"""

    def __init__(self, records):
        self.records = records
        self.gets = 0

    def get(self, url, **kw):
        self.gets += 1
        records = self.records

        class _Resp:
            def json(self):
                return records

            def raise_for_status(self):
                return None

        return _Resp()


_OPENAPI_ROWS = [
    {"Date": "20260807", "Contract": c, "ContractMonth(Week)": "202608",
     "Open": "24000", "High": "24100", "Low": "23900", "Last": close,
     "Volume": "1000", "SettlementPrice": close, "OpenInterest": "500",
     "TradingSession": "一般"}
    for c, close in [("TX", "24050"), ("MTX", "24051"), ("TMF", "24052")]
]


def test_daily_endpoint_is_fetched_once_per_polling_round():
    """三個期貨標的共用同一份當日端點回應——監控端「至多 1 請求/輪詢」。

    該端點一次回傳全市場，逐商品各打一次等於為同一份資料發三個請求。
    """
    fs = FuturesDataSourceConfig(latest_cache_seconds=15.0)
    session = _CountingGetSession(_OPENAPI_ROWS)
    ad = TaifexAdapter(cfg=fs, session=session, sleeper=lambda _s: None)
    clock = [1000.0]
    ad._clock = lambda: clock[0]

    closes = {}
    for inst_id in ("TXF", "MTX", "TMF"):
        clock[0] += 2.0                      # 同一輪內處理三個標的
        df = ad.fetch_latest(_instrument(inst_id))
        assert len(df) == 1
        closes[inst_id] = df.iloc[0]["close"]

    assert session.gets == 1, "同一輪內應只打一次當日端點"
    # 共用回應但仍逐商品過濾——不得因共用而串到別的商品
    assert closes == {"TXF": 24050.0, "MTX": 24051.0, "TMF": 24052.0}


def test_daily_endpoint_cache_expires_before_next_round():
    """快取不得把上一輪的資料帶進下一輪（預設 15 秒 << 60 秒輪詢間隔）。"""
    fs = FuturesDataSourceConfig(latest_cache_seconds=15.0)
    session = _CountingGetSession(_OPENAPI_ROWS)
    ad = TaifexAdapter(cfg=fs, session=session, sleeper=lambda _s: None)
    clock = [1000.0]
    ad._clock = lambda: clock[0]

    ad.fetch_latest(_instrument("TMF"))
    clock[0] += 60.0                          # 下一輪
    ad.fetch_latest(_instrument("TMF"))
    assert session.gets == 2


def test_daily_endpoint_cache_can_be_disabled():
    fs = FuturesDataSourceConfig(latest_cache_seconds=0.0)
    session = _CountingGetSession(_OPENAPI_ROWS)
    ad = TaifexAdapter(cfg=fs, session=session, sleeper=lambda _s: None)
    ad._clock = lambda: 1000.0
    ad.fetch_latest(_instrument("TXF"))
    ad.fetch_latest(_instrument("TMF"))
    assert session.gets == 2


# ---------------------------------------------------------------------------
# 4. 表名、成本、推播文案
# ---------------------------------------------------------------------------

def test_table_names_for_micro_pass_whitelist():
    tmf = _instrument("TMF")
    assert table_name_for(tmf, "daily") == "fut_TMF_daily"
    assert raw_table_name_for(tmf, "daily") == "fut_TMF_raw_daily"


def test_micro_costs_scale_from_declared_contract():
    """微台 1 口 @20,000：定額 8.0 + 期交稅 20,000×10×0.00002 = 4.0 → 12.0 NT$／邊。

    同點數下大台單邊為 100 NT$，但名目值也是 20 倍——成本**佔比**才是微台
    與大台的真實差異，這裡釘住的是絕對值與乘數的一致性。
    """
    cost_cfg = FuturesCostConfig()
    tmf = FuturesCostModel(_instrument("TMF").contract, cost_cfg).entry_costs(20000.0, 1.0)
    assert tmf.commission == pytest.approx(8.0)
    assert tmf.tax == pytest.approx(4.0)
    assert tmf.total == pytest.approx(12.0)

    txf = FuturesCostModel(_instrument("TXF").contract, cost_cfg).entry_costs(20000.0, 1.0)
    # 成本佔名目值的比率：微台較高（定額費未按乘數等比縮小）
    assert tmf.total / (20000.0 * 10) > txf.total / (20000.0 * 200)


def test_alert_label_distinguishes_the_three_index_futures():
    """三商品訊號幾乎相同，通知必須看得出是大台、小台還是微台。"""
    import monitor_signals

    labels = {i: monitor_signals.instrument_label(i, _instrument(i)) for i in AUTHORITY}
    assert labels["TXF"] == "臺股期貨（TXF）"
    assert labels["MTX"] == "小型臺指期貨（MTX）"
    assert labels["TMF"] == "微型臺指期貨（TMF）"
    assert len(set(labels.values())) == 3


def test_alert_label_for_equity_is_unchanged():
    """現貨的 display_name 恆等於 id，故文案與引入顯示名之前逐字相同。"""
    import monitor_signals
    from instruments import equity_instrument

    assert monitor_signals.instrument_label("2330.TW", equity_instrument("2330.TW")) == "2330.TW"
    assert monitor_signals.instrument_label("2330.TW", None) == "2330.TW"


def test_pushed_message_names_the_micro_product(tmp_path, monkeypatch):
    """端到端：實際推播出去的那串文字必須含商品名，而非只有代碼 TMF。"""
    import numpy as np
    import pandas as pd

    import monitor_signals
    from data_ingestion import save_to_sqlite

    db = str(tmp_path / "monitor_micro.db")
    monkeypatch.setattr(monitor_signals, "DB_PATH", db)
    monitor_signals.init_sent_alerts_db(db)

    # 末根重挫 → 觸發空頭 BOS
    idx = pd.date_range("2024-08-01", periods=60, freq="D")
    close = np.full(60, 100.0)
    close[-1] = 80.0
    open_ = np.full(60, 100.0)
    frame = pd.DataFrame({"open": open_,
                          "high": np.maximum(open_, close) + 0.5,
                          "low": np.minimum(open_, close) - 0.5,
                          "close": close,
                          "volume": np.full(60, 1000.0)}, index=idx)

    tmf = _instrument("TMF")
    save_to_sqlite(frame, table_name_for(tmf, "daily"), db)

    class _NoLatest:
        def fetch_latest(self, instrument):
            return pd.DataFrame()

        def fetch(self, *a, **kw):
            raise AssertionError("監控不得呼叫重量 fetch()")

    monkeypatch.setattr(monitor_signals, "get_adapter", lambda key: _NoLatest())

    sent = []

    class _Spy:
        def send_alert(self, msg):
            sent.append(msg)
            return True

    monitor_signals.check_new_signals("TMF", _Spy(), instrument=tmf)

    assert sent, "空頭 BOS 應觸發推播"
    assert all("微型臺指期貨（TMF）" in m for m in sent)
    assert all("MOCK" not in m for m in sent), "真源訊息不得帶 MOCK 前綴"


# ---------------------------------------------------------------------------
# 交叉驗證哨兵的涵蓋範圍
# ---------------------------------------------------------------------------

def test_cross_verification_covers_every_taifex_instrument():
    """哨兵必須逐商品驗證。

    早期只驗第一個 instrument；接入 MTX/TMF 後那個寫法會在只看過大台的
    情況下印出「全數通過」——未驗到的商品被說成驗過了，比沒有哨兵更糟。
    """
    from verify_futures_data import taifex_instruments

    assert {i.id for i in taifex_instruments()} >= set(AUTHORITY)
