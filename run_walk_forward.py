# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - Walk-Forward 驗證執行腳本 (Run Walk-Forward)

對設定檔中的每個標的執行滾動式樣本內尋優 / 樣本外驗證，
並對樣本外交易序列執行蒙地卡羅重抽，輸出完整報告。

用法:
    python3 run_walk_forward.py            # 跑設定檔所有標的
    python3 run_walk_forward.py 0050.TW    # 只跑指定標的
"""

import os
import sys

from backtester import BacktestEngine
from config import load_config
from db_security import safe_load_db_data, table_name_for
from instruments import equity_instrument
from walk_forward import WalkForwardAnalyzer, format_walk_forward_report


def run(target_ticker: str = None):
    cfg = load_config()
    db_path = cfg.data.database_path

    if not os.path.exists(db_path):
        print(f"錯誤：找不到資料庫 {db_path}，請先執行 run_ingestion.py 下載數據。")
        return

    tickers = [target_ticker] if target_ticker else cfg.data.tickers

    engine = BacktestEngine(config=cfg)
    analyzer = WalkForwardAnalyzer(engine=engine)

    for ticker in tickers:
        table_name = table_name_for(equity_instrument(ticker), "daily")

        df = safe_load_db_data(db_path, table_name)
        if df is None or df.empty:
            print(f"略過 {ticker}：無數據。")
            continue

        print(f"\n################ {ticker} (共 {len(df)} 根日 K) ################")

        try:
            result = analyzer.run(df, n_folds=4)
        except ValueError as e:
            print(f"略過 {ticker}：{e}")
            continue

        print(format_walk_forward_report(result))

        if not result["folds"]:
            print("⚠ 無有效折數，數據可能過短。")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run(target)
