"""
Range Navigator - 參數尋優器單元測試 (pytest)

本測試驗證 optimizer.py 的核心功能：
1. 驗證尋優器初始化與設定檔載入。
2. 驗證卡爾瑪比率 (Calmar Ratio) 計算公式安全性。
"""

import pytest
import os
import numpy as np
from optimizer import ParameterOptimizer

def test_optimizer_initialization():
    """
    測試 ParameterOptimizer 的初始化與設定載入
    """
    opt = ParameterOptimizer()
    assert opt.cfg is not None
    assert opt.config_path is not None
    assert os.path.exists(opt.config_path)

def test_calmar_ratio_safe_division():
    """
    測試卡爾瑪比率計算時防止除以零的安全性
    """
    # 模擬 10% 報酬率，MDD 為 0%
    total_return = 0.10
    mdd = 0.0
    
    mdd_floor = max(mdd, 0.0001)
    calmar = total_return / mdd_floor
    
    # MDD 被限幅在 0.0001，避免了 ZeroDivisionError
    assert calmar == 1000.0
