# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 資料庫安全模組單元測試 (Database Security Tests)

本模組對 db_security.py 進行全面測試，驗證 SQL 注入防護與表名白名單正則的邊界防禦能力。
"""

import pytest
import pandas as pd
import tempfile
import os
from db_security import validate_table_name, safe_load_db_data, safe_save_to_sqlite

def test_validate_table_name_success():
    """
    測試合規的資料表名稱是否能順利通過驗證。
    """
    # 測試日常日線與 5 分鐘線的白名單格式
    validate_table_name("stock_2330_TW_daily")
    validate_table_name("stock_0050_TW_5m")
    validate_table_name("stock_US_TICKER_daily")
    validate_table_name("stock_a_daily")

def test_validate_table_name_injection_fail():
    """
    測試不合規的資料表名稱與潛在注入字串，驗證是否觸發 Fail-Fast 阻斷機制。
    """
    # 測試包含 SQL 注入攻擊特徵的惡意表名
    bad_names = [
        "stock_2330_daily; DROP TABLE stock_2330_daily;",
        "stock_2330_daily--",
        "stock_2330_daily OR 1=1",
        "stock_2330_daily UNION SELECT * FROM users",
        "stock_2330_daily_extra",
        "stock_2330_15m",  # 不在允許的 interval 內
        "stock_2330",      # 格式不完整
        "other_table_name",
        "",
    ]
    for name in bad_names:
        with pytest.raises(ValueError) as excinfo:
            validate_table_name(name)
        assert "資料庫安全性錯誤" in str(excinfo.value)

def test_safe_load_and_save_operations():
    """
    測試安全資料庫寫入與讀取介面，包含正常寫入與 SQL 注入表名防禦。
    """
    # 建立臨時 SQLite 資料庫檔案
    temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".db")
    os.close(temp_db_fd)
    
    try:
        # 準備測試資料
        df = pd.DataFrame(
            {"open": [100.0, 101.0], "close": [102.0, 103.0]},
            index=pd.to_datetime(["2026-06-01", "2026-06-02"])
        )
        df.index.name = "datetime"
        
        # 1. 正常寫入與載入
        table_name = "stock_2330_TW_daily"
        save_status = safe_save_to_sqlite(df, table_name, temp_db_path)
        assert save_status is True
        
        loaded_df = safe_load_db_data(temp_db_path, table_name)
        assert not loaded_df.empty
        assert len(loaded_df) == 2
        assert "close" in loaded_df.columns
        
        # 2. 使用非法表名嘗試寫入與讀取，驗證邊界防衛
        malicious_table_name = "stock_2330_daily; DROP TABLE stock_2330_daily;"
        
        with pytest.raises(ValueError):
            safe_save_to_sqlite(df, malicious_table_name, temp_db_path)
            
        with pytest.raises(ValueError):
            safe_load_db_data(temp_db_path, malicious_table_name)
            
    finally:
        # 清理臨時資料庫檔案
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)
