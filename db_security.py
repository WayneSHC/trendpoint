# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 生產級資料庫安全與防注入模組 (Database Security Module)

本模組落實安全設計原則 (Security-by-Design)：
1. 針對 SQLite 的動態資料表名稱 (Table Name) 進行嚴格的白名單正則校驗。
2. 封裝安全的資料庫讀寫介面，阻斷潛在的 SQL 注入攻擊通道。
"""

import re
import sqlite3
import pandas as pd

# 嚴格的資料表名稱白名單正則表達式。
# spec 008a：放寬命名空間以支援期貨（fut_*）；equity 維持 stock_* 逐字元不變。
TABLE_NAME_PATTERN = re.compile(r"^(stock|fut)_[a-zA-Z0-9_]+_(daily|5m)$")

def validate_table_name(table_name: str) -> None:
    """
    驗證資料表名稱是否符合安全白名單格式，若不符則立即拋出 ValueError 實施 Fail-Fast 防護。
    """
    if not TABLE_NAME_PATTERN.match(table_name):
        raise ValueError(
            f"資料庫安全性錯誤：拒絕存取不合規的資料表名稱 '{table_name}'。"
            f"資料表名稱必須完全符合 '(stock|fut)_<代號>_<interval>' 格式且不含特殊字元。"
        )

def _clean_id(instrument_id: str) -> str:
    """識別碼轉為安全表名片段（與現行 run_ingestion 一致）。"""
    return instrument_id.replace(".", "_").replace("/", "_")

def table_name_for(instrument, timeframe: str) -> str:
    """
    由 Instrument + 時框導出 SQLite 表名，作為**唯一導出點**（spec 008a）。
    equity → `stock_{clean_id}_{tf}`（與現行逐字元相同，不需重抓）；
    futures → `fut_{clean_id}_{tf}`。導出後即過白名單驗證，fail-fast。
    """
    # duck-typed：AssetClass(str, Enum) 之成員 == "futures"，故直接比較字串即可
    is_futures = getattr(instrument, "asset_class", "equity") == "futures"
    prefix = "fut" if is_futures else "stock"
    name = f"{prefix}_{_clean_id(instrument.id)}_{timeframe}"
    validate_table_name(name)
    return name

def safe_load_db_data(db_path: str, table_name: str) -> pd.DataFrame:
    """
    具備 SQL 注入防護的 SQLite 資料載入函數。
    """
    validate_table_name(table_name)
    
    conn = sqlite3.connect(db_path)
    try:
        # 經正則白名單驗證後的安全表名，方能進行 SQL 拼接
        query = f"SELECT * FROM {table_name}"
        df = pd.read_sql_query(query, conn)
        
        if not df.empty and 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])
            df = df.set_index('datetime')
        return df
    except Exception as e:
        print(f"資料庫安全載入失敗: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def safe_save_to_sqlite(df: pd.DataFrame, table_name: str, db_path: str) -> bool:
    """
    具備 SQL 注入防護的 SQLite 資料寫入與取代函數。
    """
    validate_table_name(table_name)
    
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        # 使用 Pandas 內建編譯器寫入，強化欄位與型態校驗
        df.to_sql(
            name=table_name, 
            con=conn, 
            if_exists="replace", 
            index=True, 
            index_label="datetime"
        )
        return True
    except Exception as e:
        print(f"資料庫安全寫入失敗: {e}")
        return False
    finally:
        if conn:
            conn.close()
