# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - spec 016 生產路徑基準凍結（T001）。

**為什麼要有這個檔**：spec 016 SC-011 要求「日線生產路徑的回測輸出與本案
實作前完全相同」。要證明這件事，基準必須在**任何程式改動之前**凍結——
若等改完再凍，比對的是改動後的自己跟改動後的自己，量不到任何東西。

**為什麼不用真實資料**：`trendpoint.db` 為 gitignored 且需網路重建，本開發
容器的 agent proxy 對行情來源回 403。以固定 seed 的合成日線取代，基準因而
**完全可離線重現**，且一樣能偵測到生產路徑的任何行為改變——這才是 SC-011
真正要守的東西（改變偵測），而不是特定標的的績效數字。

呼叫路徑刻意逐參數對齊 `run_backtest.py:106-142` 的生產呼叫，
包含 spec 012／013 的預設關閉旗標。任一預設值被悄悄改動，本基準即紅燈。

重新產生（僅在**刻意**變更生產行為時執行，且須於 PR 說明前後差異）：

    python tests/baseline_016.py
"""

from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acceptance_fixtures import make_klines  # noqa: E402

from backtester import BacktestEngine  # noqa: E402
from config import load_config  # noqa: E402

BASELINE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "016_baseline_daily.json"
)

# 日線、600 根（約 2.4 年），足以讓 ma_period 暖機後仍有可觀察的交易段。
BASELINE_BARS = 600
BASELINE_SEED = 42


def daily_frame() -> pd.DataFrame:
    """基準所用的合成日線序列（固定 seed，無 I/O、無隨機呼叫）。"""
    return make_klines(BASELINE_BARS, freq="1D", seed=BASELINE_SEED)


def run_production_daily() -> dict:
    """以**生產路徑的預設參數**跑一次日線回測，回傳可序列化的結果。

    逐參數對齊 `run_backtest.py` 的呼叫；此處刻意不簡化，因為簡化等於
    放掉一部分行為的守備範圍。
    """
    cfg = load_config()
    p = cfg.strategy.default
    engine = BacktestEngine(config=cfg)

    res = engine.run_backtest(
        df=daily_frame(),
        asset_class="equity",
        atr_period=p.atr_period,
        k=p.ladder_k,
        ch_period=p.chandelier_period,
        ch_multiplier=p.chandelier_mult,
        time_limit=p.time_limit,
        use_adx_filter=p.use_adx_filter,
        adx_period=p.adx_period,
        adx_threshold=p.adx_threshold,
        use_ma_filter=p.use_ma_filter,
        ma_period=p.ma_period,
        use_er_filter=p.use_er_filter,
        er_period=p.er_period,
        er_threshold=p.er_threshold,
        use_fvg=p.use_fvg,
        fvg_lookback=p.fvg_lookback,
        swing_n=p.swing_fractal_n,
        volume_mult=p.mss_volume_mult,
        mss_reversal_entry=p.mss_reversal_entry,
        enable_short=p.enable_short,
        use_bos_volume=p.use_bos_volume,
        bos_volume_mult=p.bos_volume_mult,
        bos_volume_period=p.bos_volume_period,
        use_dd_gate=p.use_dd_gate,
        dd_limit_pct=p.dd_limit_pct,
        dd_resume_pct=p.dd_resume_pct,
        use_settlement_gate=p.use_settlement_gate,
        verbose=False,
    )
    return _serialize(res)


def _round(value):
    """浮點一律固定小數位——避免平台間最後一位的差異造成假紅燈。"""
    if isinstance(value, float):
        return round(value, 8)
    return value


def _serialize(res: dict) -> dict:
    """把回測結果攤成純 JSON 結構：逐筆交易、逐根權益、summary。"""
    trades: pd.DataFrame = res.get("trades", pd.DataFrame())
    equity: pd.DataFrame = res.get("equity_curve", pd.DataFrame())
    summary: dict = res.get("summary", {}) or {}

    def frame_to_records(df: pd.DataFrame) -> list:
        if df is None or len(df) == 0:
            return []
        out = df.copy()
        out.index = [str(i) for i in out.index]
        records = []
        for idx, row in out.iterrows():
            rec = {"_index": idx}
            for col in sorted(out.columns):
                v = row[col]
                if isinstance(v, pd.Timestamp):
                    v = str(v)
                elif hasattr(v, "item"):
                    v = v.item()
                rec[str(col)] = _round(v)
            records.append(rec)
        return records

    return {
        "_generated_by": "python tests/baseline_016.py",
        "_source": f"make_klines(n={BASELINE_BARS}, freq='1D', seed={BASELINE_SEED})",
        "summary": {k: _round(summary[k]) for k in sorted(summary)},
        "trades": frame_to_records(trades),
        "equity_curve": frame_to_records(equity),
        "trades_columns": sorted(str(c) for c in trades.columns) if len(trades) else [],
        "equity_columns": sorted(str(c) for c in equity.columns) if len(equity) else [],
    }


def load_baseline() -> dict:
    with open(BASELINE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    payload = run_production_daily()
    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print(f"已凍結基準 → {BASELINE_PATH}")
    print(f"  交易筆數 {len(payload['trades'])}、權益根數 {len(payload['equity_curve'])}")
    print(f"  summary 鍵 {list(payload['summary'])}")


if __name__ == "__main__":
    main()
