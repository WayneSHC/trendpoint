"""
Range Navigator - 系統設定模組單元測試 (Configuration Model Tests)

本模組對 config/config.py 的 load_config 與 save_config 功能進行單元測試，
驗證配置檔讀寫、tickers 增刪更新與強型別校驗是否正常。
"""

import pytest
import tempfile
import os
from config.config import load_config, save_config, SystemConfig, SingleStrategyParams

def test_config_load_and_save_flow():
    """
    測試 SystemConfig 的載入、修改 tickers 與儲存回寫 YAML 的完整流程。
    """
    # 建立臨時 YAML 設定檔路徑
    temp_yaml_fd, temp_yaml_path = tempfile.mkstemp(suffix=".yaml")
    os.close(temp_yaml_fd)
    
    try:
        # 1. 建立預設 SystemConfig 物件
        cfg = SystemConfig()
        assert "2330.TW" in cfg.data.tickers
        
        # 2. 新增 ticker 標的並設定參數覆蓋
        new_ticker = "2454.TW"
        cfg.data.tickers.append(new_ticker)
        cfg.strategy.ticker_overrides[new_ticker] = SingleStrategyParams(
            atr_period=12,
            ladder_k=1.8
        )
        
        # 3. 呼叫 save_config 回寫至臨時檔案
        save_status = save_config(cfg, temp_yaml_path)
        assert save_status is True
        
        # 4. 重新 load_config 載入該臨時檔案，驗證持久化資料正確性
        loaded_cfg = load_config(temp_yaml_path)
        assert new_ticker in loaded_cfg.data.tickers
        assert "2330.TW" in loaded_cfg.data.tickers
        
        # 驗證新標的參數 overrides 確實寫入
        ticker_params = loaded_cfg.strategy.get_params_for_ticker(new_ticker)
        assert ticker_params.atr_period == 12
        assert ticker_params.ladder_k == 1.8
        
        # 5. 測試刪除標的
        loaded_cfg.data.tickers.remove(new_ticker)
        del loaded_cfg.strategy.ticker_overrides[new_ticker]
        
        # 回寫臨時檔案
        save_status_del = save_config(loaded_cfg, temp_yaml_path)
        assert save_status_del is True
        
        # 再次重新載入驗證刪除結果
        reloaded_cfg = load_config(temp_yaml_path)
        assert new_ticker not in reloaded_cfg.data.tickers
        assert len(reloaded_cfg.data.tickers) == len(cfg.data.tickers) - 1
        
    finally:
        # 清理臨時 YAML 檔案
        if os.path.exists(temp_yaml_path):
            os.remove(temp_yaml_path)
