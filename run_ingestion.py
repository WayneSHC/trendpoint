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
import sqlite3
from datetime import date, timedelta

import pandas as pd

from config import load_config
from instruments import InstrumentRegistry
from data_sources import get_adapter
from data_ingestion import save_to_csv, save_to_sqlite, validate_data_contract
from db_security import raw_table_name_for, safe_load_db_data, table_name_for, validate_table_name


def _write_raw_table(raw: pd.DataFrame, table: str, db_path: str) -> None:
    """raw 層寫入：date 以 ISO 字串存欄位（非索引——(date×contract) 有重複日期）。
    表名經白名單驗證；值由 pandas 參數化寫入（鐵律 5）。整表覆蓋 = 冪等。"""
    validate_table_name(table)
    out = raw.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    conn = sqlite3.connect(db_path)
    try:
        out.to_sql(table, conn, if_exists="replace", index=False)
    finally:
        conn.close()


def _ingest_taifex(inst, tf: str, sys_cfg, db_path: str, adapter) -> None:
    """spec 010 真源分流：raw 冪等入庫（表空→全歷史回填、非空→增量補到今日）
    → rollover 重建連續序列 → 放寬版品質契約 → 整表覆蓋既有期貨表。"""
    from data_sources.rollover import (build_continuous, compute_roll_events,
                                       select_front_month)

    fs_cfg = sys_cfg.data.futures_source
    raw_table = raw_table_name_for(inst, tf)

    try:
        existing = safe_load_db_data(db_path, raw_table)
    except Exception:
        existing = pd.DataFrame()
    if not existing.empty:
        existing["date"] = pd.to_datetime(existing["date"])
        start = (existing["date"].max() + timedelta(days=1)).date()
        mode = f"增量（自 {start}）"
    else:
        start = date.fromisoformat(fs_cfg.backfill_start)
        mode = f"回填（{start} 起，全歷史；約 {max(1, (date.today()-start).days//30)} 請求、"
        mode += f"節流 {fs_cfg.throttle_seconds}s/請求）"
    end = date.today()
    print(f"    - [taifex] {mode}")

    if start <= end:
        new_raw = adapter.fetch_raw(inst, tf, start, end)
        combined = (pd.concat([existing, new_raw], ignore_index=True)
                    if not existing.empty else new_raw)
    else:
        combined = existing
    if combined.empty:
        print("    * [警告] 無 raw 資料可用，跳過。")
        return
    combined["date"] = pd.to_datetime(combined["date"])
    combined = (combined.drop_duplicates(subset=["date", "contract"], keep="last")
                        .sort_values(["date", "contract"]).reset_index(drop=True))

    _write_raw_table(combined, raw_table, db_path)
    print(f"    - raw 入庫：{raw_table}（{len(combined)} 列、"
          f"{combined['date'].min().date()} ~ {combined['date'].max().date()}）")

    # 連續序列重建（每次全量重算——back-adjust 語意，008a 整表覆蓋預留）
    front = select_front_month(combined)
    events = compute_roll_events(combined, front)
    cont = build_continuous(combined, front, events)
    validate_data_contract(cont, quality=sys_cfg.data_quality,
                           asset_class=inst.asset_class,
                           allow_nonpositive_prices=True)

    clean = inst.id.replace(".", "_").replace("/", "_")
    save_to_csv(cont, f"data/{clean}_{tf}.csv")
    save_to_sqlite(cont, table_name_for(inst, tf), db_path)
    print(f"    - 連續序列入庫：{table_name_for(inst, tf)}（{len(cont)} 根、"
          f"轉倉事件 {len(events)} 次）")


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
            # spec 010：TAIFEX 真源走 raw 分層 + 增量分流（不走重量 fetch()）
            if inst.source == "taifex":
                try:
                    _ingest_taifex(inst, tf, sys_cfg, db_path, adapter)
                except Exception as e:
                    print(f"  * [錯誤]：taifex 匯入 {inst.id} [{tf}] 失敗：{e}，跳過。")
                continue
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
