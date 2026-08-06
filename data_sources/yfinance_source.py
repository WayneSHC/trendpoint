# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - yfinance 資料來源 adapter (spec 008a)。

包裝現行 `fetch_stock_data`（內含 clean_kline_dataframe + validate_data_contract），
行為與 008a 前的現貨匯入路徑一致。
"""

from .base import DataSourceAdapter
from . import register_adapter
from data_ingestion import fetch_stock_data

# 時框 → interval。日線的 period 來自 config `data.equity_history_period`（憲章 V，
# 不硬編碼）；5 分線的 period 是**資料源上限**而非可調策略參數，故不入 config。
#
# **2026-08-06 更正**：本處原為 `"5d"`，註解宣稱「那是 yfinance 對 5m 的上限」——
# 該敘述不正確。Yahoo 對 5m 的回溯上限是 **60 天**；7 天是 `1m` 的限制。
# 此錯誤前提曾外溢：`docs/reviews/2026-07-30-tradingview-mcp-workflow-review.md`
# 第五節據「5m 只有約 270 根」判定盤中系統跑不出統計意義並予封存，
# 而 60 天約 2,160 根（40 交易日 × 54），是原估的 8 倍。
#
# 改此常數**不改變任何現行行為**：`equity_instrument` 的 timeframes 僅
# `["daily"]`（instruments.py:90），`run_ingestion.py:143` 只迴圈宣告的時框，
# 故 adapter 的 5m 分支目前無呼叫端。它供研究路徑（run_5m_evaluation.py）使用。
#
# **監控端不受影響也不應受影響**：`monitor_signals.py:167` 直接呼叫
# fetch_stock_data(period="5d")、不經本 adapter。盤中提示只需最近數日，
# 拉長 period 只會增加每 30 分鐘一次輪詢的傳輸量而無收益。
_INTERVAL = {"daily": "1d", "5m": "5m"}
_FIVE_MIN_PERIOD = "60d"


class YfinanceAdapter(DataSourceAdapter):
    source_key = "yfinance"

    def __init__(self, cfg=None):
        # 沿用 taifex_source 的惰性載入慣例：模組匯入時就讀 config 會讓
        # 測試無法注入，且 adapter 在 registry 中是單例。
        self._cfg = cfg

    def _period_for(self, timeframe: str) -> str:
        if timeframe == "5m":
            return _FIVE_MIN_PERIOD
        cfg = self._cfg
        if cfg is None:
            from config import load_config
            cfg = load_config().data
        return cfg.equity_history_period

    def fetch(self, instrument, timeframe: str):
        interval = _INTERVAL.get(timeframe, "1d")
        df = fetch_stock_data(ticker=instrument.id,
                              period=self._period_for(timeframe),
                              interval=interval)
        if df is None:
            raise RuntimeError(f"yfinance 取得 {instrument.id} [{timeframe}] 失敗")
        return df


register_adapter(YfinanceAdapter())
