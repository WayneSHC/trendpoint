# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 系統設定模組單元測試 (Configuration Model Tests)

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


# ---------------------------------------------------------------- spec 013 進場閘門參數

def test_dd_resume_must_be_strictly_below_limit():
    """SC-005：恢復門檻 >= 封鎖門檻之設定被 schema 拒絕（相等與反向兩種情形皆測）。"""
    # 相等：遲滯區間退化為單一門檻 → 逐根翻動
    with pytest.raises(Exception) as eq_err:
        SingleStrategyParams(dd_limit_pct=0.10, dd_resume_pct=0.10)
    assert "dd_resume_pct" in str(eq_err.value)

    # 反向：恢復門檻比封鎖門檻更深，語意上不可能解除
    with pytest.raises(Exception) as rev_err:
        SingleStrategyParams(dd_limit_pct=0.10, dd_resume_pct=0.25)
    assert "dd_resume_pct" in str(rev_err.value)

    # 合法組合不受影響（含 resume=0.0 的特例）
    assert SingleStrategyParams(dd_limit_pct=0.10, dd_resume_pct=0.0).dd_resume_pct == 0.0
    assert SingleStrategyParams(dd_limit_pct=0.20, dd_resume_pct=0.10).dd_limit_pct == 0.20


def test_entry_gates_default_off():
    """FR-009：兩道閘門預設關閉——預設值一旦被改，SC-001 的基準保證即失效。"""
    p = SingleStrategyParams()
    assert p.use_dd_gate is False
    assert p.use_settlement_gate is False
    assert p.dd_limit_pct == 0.20 and p.dd_resume_pct == 0.10


def test_entry_gates_are_overridable_per_ticker():
    """FR-012：四參數皆可經 ticker_overrides 覆寫。"""
    cfg = SystemConfig()
    cfg.strategy.ticker_overrides["0050.TW"] = SingleStrategyParams(
        use_dd_gate=True, dd_limit_pct=0.15, dd_resume_pct=0.05, use_settlement_gate=True
    )
    p = cfg.strategy.get_params_for_ticker("0050.TW")
    assert p.use_dd_gate and p.use_settlement_gate and p.dd_limit_pct == 0.15
    # 未覆寫的標的仍取 default（閘門關閉）
    assert cfg.strategy.get_params_for_ticker("2330.TW").use_dd_gate is False


def test_shipped_config_yaml_keeps_gates_off():
    """入版控的 config.yaml 必須維持兩道閘門關閉（B 段實測未完成前不得預設啟用）。"""
    cfg = load_config()
    assert cfg.strategy.default.use_dd_gate is False
    assert cfg.strategy.default.use_settlement_gate is False


def test_equity_history_period_is_config_driven_not_hardcoded():
    """憲章 V：現貨取數期間屬可調參數，只能來自 config。

    這條同時是回歸測試：`yfinance_source` 曾把 `("10y", "1d")` 硬編碼在模組層，
    導致「拉長回測期間」這個最基本的研究動作必須改程式碼。
    """
    from data_sources.yfinance_source import YfinanceAdapter

    assert SystemConfig().data.equity_history_period == "max"
    assert load_config().data.equity_history_period == "max"

    class _Stub:
        equity_history_period = "5y"

    assert YfinanceAdapter(cfg=_Stub())._period_for("daily") == "5y"


def test_five_minute_period_is_not_config_driven():
    """5 分線固定 5 天——那是 yfinance 對 5m 的供給上限，不是策略參數。

    若它跟著 `equity_history_period` 走，設成 max 會直接讓 5m 取數失敗。
    """
    from data_sources.yfinance_source import YfinanceAdapter

    class _Stub:
        equity_history_period = "max"

    assert YfinanceAdapter(cfg=_Stub())._period_for("5m") == "5d"
