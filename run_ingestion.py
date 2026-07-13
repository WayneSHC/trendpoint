# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 數據下載與持久化腳本 (Run Ingestion)

spec 008a：改為 registry 驅動——迭代 InstrumentRegistry，依 instrument.source
分派資料來源 adapter，以 table_name_for 集中命名存表。現貨（yfinance）維持
日線 + 5 分線；期貨（mock/csv）依其宣告時框。資產類別無關的資料契約驗證吃
per-asset-class 離群門檻。
"""

import os

from config import load_config
from instruments import InstrumentRegistry
from data_sources import get_adapter
from data_ingestion import save_to_csv, save_to_sqlite, validate_data_contract
from db_security import table_name_for


def run():
    print("=" * 60)
    print("開始執行 TrendPoint 數據抓取任務...")
    print("=" * 60)

    sys_cfg = load_config()
    db_path = sys_cfg.data.database_path
    registry = InstrumentRegistry.from_config(sys_cfg.data.tickers, sys_cfg.data.instruments)
    os.makedirs("data", exist_ok=True)

    for inst in registry.all():
        print(f"\n[處理標的：{inst.name}｜id={inst.id}｜{inst.asset_class.value}｜source={inst.source}]")
        try:
            adapter = get_adapter(inst.source)
        except ValueError as e:
            print(f"  * [錯誤]：{e}，跳過此標的。")
            continue

        for tf in inst.timeframes:
            print(f"- [{tf}] 經 {inst.source} adapter 取得數據:")
            try:
                df = adapter.fetch(inst, tf)
            except Exception as e:
                print(f"  * [錯誤]：取得 {inst.id} [{tf}] 失敗：{e}，跳過。")
                continue

            print(f"    - 資料筆數: {len(df)}；日期範圍: {df.index.min()} ~ {df.index.max()}")

            # 資料契約驗證（asset-aware 離群門檻，憲章 VI）
            validate_data_contract(df, quality=sys_cfg.data_quality, asset_class=inst.asset_class)

            # 儲存：CSV（沿用檔名慣例）+ SQLite（集中表命名，equity 維持 stock_*）
            clean = inst.id.replace(".", "_").replace("/", "_")
            save_to_csv(df, f"data/{clean}_{tf}.csv")
            save_to_sqlite(df, table_name_for(inst, tf), db_path)

    print("\n" + "=" * 60)
    print("數據抓取與持久化任務執行完畢！")
    print("=" * 60)


if __name__ == "__main__":
    run()
