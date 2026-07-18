# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
spec 004 US3 — 資料容錯（Gap & Outlier），對應 spec 001 SC-005。

場景 1：中段缺漏 K 線經 clean_kline_dataframe 後，向前填補（ffill，嚴禁 bfill）、
        索引嚴格遞增、後續 ATR 無 NaN，並發出含填補根數的警告。
場景 2：價格歸零或千倍跳動經 validate_data_contract 後拒絕（raise ValueError）
        並發出含時間戳的警告；正常資料通過。

含 T016 的有效性驗證（threshold is load-bearing）：把跳動上限放寬為無限大後，
spike 不再被攔截——證明離群攔截確實由 max_close_jump_ratio 驅動，而非其他規則。
"""

import logging

import numpy as np
import pandas as pd
import pytest

from acceptance_fixtures import (
    make_klines,
    make_klines_with_gap,
    make_klines_with_outlier,
)
from config import DataQualityConfig
from data_ingestion import clean_kline_dataframe, validate_data_contract
from ladder_system import calculate_atr, calculate_tr

_QUALITY = DataQualityConfig()  # 預設 max_close_jump_ratio=3.0，測試不依賴 config 檔


# ---------------------------------------------------------------------------
# 場景 1：缺漏 K 線的 ffill 容錯
# ---------------------------------------------------------------------------

def test_gap_forward_filled_not_backfilled(caplog):
    gap_at, gap_len = 120, 3
    gapped = make_klines_with_gap(600, gap_at=gap_at, gap_len=gap_len, freq="5min")
    pre_gap_close = gapped["close"].iloc[gap_at - 1]
    post_gap_close = gapped["close"].iloc[gap_at + gap_len]  # 缺口後第一根原值

    with caplog.at_level(logging.WARNING):
        cleaned = clean_kline_dataframe(gapped)

    # 索引嚴格遞增、無重複、無殘留 NaN
    assert cleaned.index.is_monotonic_increasing
    assert not cleaned.index.has_duplicates
    assert not cleaned.isna().any().any()

    # 缺口列以「缺口前」的值填補（ffill），而非「缺口後」的值（bfill）
    for j in range(gap_at, gap_at + gap_len):
        assert cleaned["close"].iloc[j] == pre_gap_close
        assert cleaned["close"].iloc[j] != post_gap_close

    # 後續 ATR 除 warmup 外無 NaN
    atr = calculate_atr(
        calculate_tr(cleaned["high"], cleaned["low"], cleaned["close"]), period=14)
    assert atr.iloc[14:].notna().all()

    # 發出含填補根數的警告（中段缺口，無 head drop → 填補 = gap_len）
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ffill" in r.getMessage() and str(gap_len) in r.getMessage()
               for r in warnings)


# ---------------------------------------------------------------------------
# 場景 2：離群值拒絕
# ---------------------------------------------------------------------------

def test_zero_price_rejected(caplog):
    outlier = make_klines_with_outlier(600, at=300, kind="zero", freq="5min")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError, match="非正價格"):
            validate_data_contract(outlier, quality=_QUALITY)
    ts = str(outlier.index[300])
    assert any("非正價格" in r.getMessage() and ts in r.getMessage()
               for r in caplog.records)


def test_price_spike_rejected(caplog):
    outlier = make_klines_with_outlier(600, at=300, kind="spike", freq="5min")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError, match="跳動"):
            validate_data_contract(outlier, quality=_QUALITY)
    ts = str(outlier.index[300])
    assert any("跳動" in r.getMessage() and ts in r.getMessage()
               for r in caplog.records)


def test_clean_data_passes():
    clean = make_klines(600, freq="5min")
    assert validate_data_contract(clean, quality=_QUALITY) is True


# ---------------------------------------------------------------------------
# T016：閾值有效性——放寬上限後 spike 不再被攔截，證明攔截由閾值驅動
# ---------------------------------------------------------------------------

def test_jump_threshold_is_load_bearing():
    outlier = make_klines_with_outlier(600, at=300, kind="spike", freq="5min")
    lax = DataQualityConfig(max_close_jump_ratio=float("inf"))
    # 放寬跳動上限後，唯一攔截 spike 的規則失效——spike 價格為正，故通過驗證
    assert validate_data_contract(outlier, quality=lax) is True


# ---------------------------------------------------------------------------
# spec 011（FR-003）：未調整參考價之正性不受 back-adjust 負價豁免影響
# ---------------------------------------------------------------------------

def _cont_with_unadj(n: int = 30):
    """模擬期貨連續序列：調整後價含負值（合法），未調整價恆為正。"""
    df = make_klines(n, freq="5min")
    for col in ("open", "high", "low", "close"):
        df[f"unadj_{col}"] = df[col]
    shift = float(df["high"].max()) + 50.0
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] - shift          # 整體平移至負值域（back-adjust 早年樣態）
    return df


def test_backadjusted_negative_prices_still_pass_with_positive_unadj():
    """對照組：調整後價為負但未調整價為正 → 放寬模式下應通過。"""
    df = _cont_with_unadj()
    assert (df["close"] < 0).all(), "fixture 應產生負的調整後價"
    assert validate_data_contract(df, quality=_QUALITY,
                                  allow_nonpositive_prices=True) is True


def test_nonpositive_unadjusted_price_rejected_despite_exemption(caplog):
    """FR-003：unadj_* 出現非正值屬資料異常，即使 allow_nonpositive_prices=True 仍須擋下。

    這道檢查必須置於放寬模式的提早 return 之前——連續層是唯一帶此旗標的
    呼叫端，也正是唯一需要本檢查的地方。
    """
    df = _cont_with_unadj()
    df.loc[df.index[5], "unadj_close"] = 0.0
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError, match="unadj_close"):
            validate_data_contract(df, quality=_QUALITY,
                                   allow_nonpositive_prices=True)


def test_nonfinite_unadjusted_price_rejected():
    """unadj_* 含 NaN/inf 同樣須擋下（有限性）。"""
    df = _cont_with_unadj()
    df.loc[df.index[3], "unadj_open"] = np.inf
    with pytest.raises(ValueError, match="unadj_open"):
        validate_data_contract(df, quality=_QUALITY,
                               allow_nonpositive_prices=True)


def test_unadjusted_check_skipped_when_columns_absent():
    """現貨/既有資料無 unadj_* 欄位時不受影響（FR-008 作用域：不擴及現貨契約）。"""
    df = make_klines(30, freq="5min")
    assert "unadj_close" not in df.columns
    assert validate_data_contract(df, quality=_QUALITY) is True
