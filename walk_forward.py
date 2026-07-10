# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - Walk-Forward 樣本外驗證模組 (Walk-Forward Analysis)

全樣本網格尋優挑出的「最佳參數」是用考古題對答案——30 組參數裡總有一組
碰巧漂亮，但那是雜訊不是訊號。本模組以滾動方式：

1. 在訓練窗 (In-Sample) 上執行網格尋優，挑出該折最佳參數。
2. 將該參數套用至緊鄰的測試窗 (Out-of-Sample) 驗證。
3. 滾動推進，串接所有測試窗報酬為一條「純樣本外」淨值曲線。
4. 輸出參數穩定度（各折最佳參數是否漂移）與參數高原檢查
   （最佳參數的鄰居也應該是好參數，孤峰即過擬合鐵證）。

評估一個系統，永遠只看樣本外的成績單。
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional

from backtester import BacktestEngine


class WalkForwardAnalyzer:
    """
    滾動式樣本內尋優 / 樣本外驗證分析器
    """

    def __init__(self,
                 engine: Optional[BacktestEngine] = None,
                 atr_periods: Optional[List[int]] = None,
                 ladder_ks: Optional[List[float]] = None,
                 backtest_kwargs: Optional[Dict[str, Any]] = None):
        """
        參數:
            engine (BacktestEngine, optional): 回測引擎，未提供時使用預設配置
            atr_periods (List[int]): ATR 週期搜尋空間
            ladder_ks (List[float]): 階梯乘數搜尋空間
            backtest_kwargs (Dict): 傳遞給 run_backtest 的其餘固定參數
        """
        self.engine = engine if engine is not None else BacktestEngine()
        self.atr_periods = atr_periods or [10, 12, 14, 16, 18, 20]
        self.ladder_ks = ladder_ks or [1.5, 2.0, 2.5, 3.0, 3.5]
        self.backtest_kwargs = backtest_kwargs or {}

    def _run_one(self, df: pd.DataFrame, atr_period: int, k: float) -> Dict[str, Any]:
        """
        執行單次回測並回傳 summary。
        """
        res = self.engine.run_backtest(
            df=df,
            atr_period=atr_period,
            k=k,
            verbose=False,
            **self.backtest_kwargs
        )
        return res["summary"]

    def _grid_search(self, df_train: pd.DataFrame) -> Dict[str, Any]:
        """
        在訓練窗上執行網格尋優，以 Calmar Ratio 為適應度指標。
        回傳最佳參數與完整網格結果（供參數高原檢查）。
        """
        grid: List[Dict[str, Any]] = []
        best = None

        for period in self.atr_periods:
            for k in self.ladder_ks:
                summary = self._run_one(df_train, period, k)
                if not summary:
                    continue

                total_return = summary["total_return"]
                mdd = abs(summary["max_drawdown"])
                calmar = total_return / max(mdd, 0.0001)

                cell = {
                    "atr_period": period,
                    "ladder_k": k,
                    "calmar": calmar,
                    "total_return": total_return,
                    "max_drawdown": summary["max_drawdown"],
                    "total_trades": summary["total_trades"],
                }
                grid.append(cell)

                if best is None or calmar > best["calmar"]:
                    best = cell

        return {"best": best, "grid": grid}

    def run(self,
            df: pd.DataFrame,
            n_folds: int = 4,
            train_ratio: float = 0.7,
            warmup_bars: int = 60) -> Dict[str, Any]:
        """
        執行 Walk-Forward 分析。

        切分方式（錨定滾動窗）：全樣本均分為 n_folds 折之測試窗，
        每折的訓練窗為該測試窗之前的所有歷史數據（至少佔全長的
        train_ratio / n_folds 比例，數據不足的首折將被略過）。

        參數:
            df (pd.DataFrame): OHLCV 全樣本數據
            n_folds (int): 測試折數
            train_ratio (float): 首折訓練窗最少佔比（相對於單折長度）
            warmup_bars (int): 測試窗前綴之指標暖機 K 線數
                （取自訓練窗尾端，僅用於指標暖機，淨值自測試窗起點重新正規化）

        回傳:
            Dict: {
                "folds": 各折結果列表,
                "oos_equity": 串接後的純樣本外淨值曲線 (pd.Series),
                "oos_summary": 樣本外整體指標,
                "param_stability": 參數漂移統計,
                "plateau": 各折參數高原檢查
            }
        """
        n = len(df)
        if n < n_folds * 40:
            raise ValueError(f"數據量 ({n} 根) 不足以切分 {n_folds} 折 Walk-Forward。")

        fold_len = n // (n_folds + 1)  # 預留首段作為第一折的訓練窗
        min_train = max(int(fold_len * train_ratio), warmup_bars + 20)

        folds: List[Dict[str, Any]] = []
        oos_segments: List[pd.Series] = []
        plateau_reports: List[Dict[str, Any]] = []

        for fold_i in range(n_folds):
            test_start = fold_len * (fold_i + 1)
            test_end = min(test_start + fold_len, n) if fold_i < n_folds - 1 else n

            if test_start < min_train:
                continue

            df_train = df.iloc[:test_start]
            # 測試窗前綴暖機數據（僅供指標收斂，績效自 test_start 起計）
            warm_start = max(0, test_start - warmup_bars)
            df_test_with_warmup = df.iloc[warm_start:test_end]

            print(f"[Fold {fold_i + 1}/{n_folds}] 訓練: {df_train.index[0].date()} ~ {df_train.index[-1].date()} "
                  f"({len(df_train)} 根) | 測試: {df.index[test_start].date()} ~ {df.index[test_end - 1].date()} "
                  f"({test_end - test_start} 根)")

            # 1. 樣本內尋優
            gs = self._grid_search(df_train)
            best = gs["best"]
            if best is None:
                continue

            # 2. 參數高原檢查
            plateau = self.check_parameter_plateau(gs["grid"], best)
            plateau_reports.append({"fold": fold_i + 1, **plateau})

            # 3. 樣本外驗證
            res_test = self.engine.run_backtest(
                df=df_test_with_warmup,
                atr_period=best["atr_period"],
                k=best["ladder_k"],
                verbose=False,
                **self.backtest_kwargs
            )

            eq = res_test["equity_curve"]["equity"]
            # 移除暖機段，並以測試窗起點重新正規化
            eq_oos = eq[eq.index >= df.index[test_start]]
            if len(eq_oos) < 2:
                continue
            eq_oos = eq_oos / eq_oos.iloc[0]

            test_return = float(eq_oos.iloc[-1] - 1.0)
            peaks = eq_oos.cummax()
            test_mdd = float(((eq_oos - peaks) / peaks).min())

            folds.append({
                "fold": fold_i + 1,
                "train_bars": len(df_train),
                "test_bars": test_end - test_start,
                "best_params": {"atr_period": best["atr_period"], "ladder_k": best["ladder_k"]},
                "train_calmar": best["calmar"],
                "train_return": best["total_return"],
                "test_return": test_return,
                "test_max_drawdown": test_mdd,
                "test_trades": res_test["summary"].get("total_trades", 0),
            })
            oos_segments.append(eq_oos)

        # 4. 串接純樣本外淨值（各折測試報酬以複利相乘）
        oos_equity = None
        oos_summary: Dict[str, Any] = {}
        if oos_segments:
            chained: List[pd.Series] = []
            level = 1.0
            for seg in oos_segments:
                chained.append(seg * level)
                level = float(chained[-1].iloc[-1])
            oos_equity = pd.concat(chained)
            oos_equity = oos_equity[~oos_equity.index.duplicated(keep='last')]

            from performance import compute_performance_metrics
            oos_summary = compute_performance_metrics(oos_equity, initial_capital=1.0)

        # 5. 參數穩定度：各折最佳參數是否漂移
        param_stability = self._param_stability(folds)

        return {
            "folds": folds,
            "oos_equity": oos_equity,
            "oos_summary": oos_summary,
            "param_stability": param_stability,
            "plateau": plateau_reports,
        }

    @staticmethod
    def check_parameter_plateau(grid: List[Dict[str, Any]], best: Dict[str, Any]) -> Dict[str, Any]:
        """
        參數高原檢查：好參數的鄰居也應該是好參數。
        計算最佳參數格點之相鄰格點 (±1 step) 的平均 Calmar，
        與最佳值比較。鄰居均值遠低於最佳值（孤峰）即過擬合警訊。
        """
        if not grid or best is None:
            return {"is_plateau": False, "neighbor_avg_calmar": None, "best_calmar": None}

        atr_vals = sorted(set(c["atr_period"] for c in grid))
        k_vals = sorted(set(c["ladder_k"] for c in grid))

        ai = atr_vals.index(best["atr_period"])
        ki = k_vals.index(best["ladder_k"])

        neighbors = []
        for da in (-1, 0, 1):
            for dk in (-1, 0, 1):
                if da == 0 and dk == 0:
                    continue
                na, nk = ai + da, ki + dk
                if 0 <= na < len(atr_vals) and 0 <= nk < len(k_vals):
                    for c in grid:
                        if c["atr_period"] == atr_vals[na] and c["ladder_k"] == k_vals[nk]:
                            neighbors.append(c["calmar"])

        if not neighbors:
            return {"is_plateau": False, "neighbor_avg_calmar": None, "best_calmar": best["calmar"]}

        neighbor_avg = float(np.mean(neighbors))
        best_calmar = best["calmar"]

        # 鄰居均值達最佳值五成以上，且鄰居均值為正，視為健康高原
        is_plateau = (best_calmar > 0) and (neighbor_avg >= 0.5 * best_calmar)

        return {
            "is_plateau": bool(is_plateau),
            "neighbor_avg_calmar": neighbor_avg,
            "best_calmar": best_calmar,
            "best_params": {"atr_period": best["atr_period"], "ladder_k": best["ladder_k"]},
        }

    @staticmethod
    def _param_stability(folds: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        統計各折最佳參數的漂移程度。參數在折與折之間劇烈跳動，
        代表系統對參數敏感、樣本外不可依賴。
        """
        if not folds:
            return {}

        atrs = [f["best_params"]["atr_period"] for f in folds]
        ks = [f["best_params"]["ladder_k"] for f in folds]

        return {
            "atr_periods": atrs,
            "ladder_ks": ks,
            "atr_period_std": float(np.std(atrs)) if len(atrs) > 1 else 0.0,
            "ladder_k_std": float(np.std(ks)) if len(ks) > 1 else 0.0,
            "n_unique_param_sets": len(set(zip(atrs, ks))),
            "n_folds": len(folds),
        }


def format_walk_forward_report(result: Dict[str, Any]) -> str:
    """
    將 Walk-Forward 結果格式化為可讀報表。
    """
    lines = ["========== Walk-Forward 樣本外驗證報告 =========="]

    for f in result.get("folds", []):
        lines.append(
            f"Fold {f['fold']}: 參數 ATR={f['best_params']['atr_period']}, k={f['best_params']['ladder_k']} | "
            f"訓練 Calmar={f['train_calmar']:.2f} | "
            f"樣本外報酬={f['test_return']:+.2%}, MDD={f['test_max_drawdown']:.2%}, 交易 {f['test_trades']} 筆"
        )

    oos = result.get("oos_summary", {})
    if oos:
        lines.append("---------- 串接樣本外整體績效 ----------")
        lines.append(f"樣本外總報酬: {oos.get('total_return', 0):+.2%}")
        lines.append(f"樣本外 CAGR: {oos.get('cagr', 0):+.2%}")
        lines.append(f"樣本外 Sharpe: {oos.get('sharpe_ratio', 0):.2f}")
        lines.append(f"樣本外 MDD: {oos.get('max_drawdown', 0):.2%}")

    ps = result.get("param_stability", {})
    if ps:
        lines.append("---------- 參數穩定度 ----------")
        lines.append(f"各折 ATR 週期: {ps.get('atr_periods')} (std={ps.get('atr_period_std', 0):.2f})")
        lines.append(f"各折階梯乘數: {ps.get('ladder_ks')} (std={ps.get('ladder_k_std', 0):.2f})")
        lines.append(f"相異參數組數: {ps.get('n_unique_param_sets')}/{ps.get('n_folds')} "
                     f"(越少越穩定；每折都不同代表參數不可依賴)")

    plateau = result.get("plateau", [])
    if plateau:
        lines.append("---------- 參數高原檢查 ----------")
        for p in plateau:
            status = "健康高原" if p.get("is_plateau") else "孤峰警訊 (過擬合風險)"
            navg = p.get("neighbor_avg_calmar")
            navg_str = f"{navg:.2f}" if navg is not None else "N/A"
            lines.append(f"Fold {p['fold']}: {status} | 最佳 Calmar={p.get('best_calmar', 0):.2f}, "
                         f"鄰居平均={navg_str}")

    return "\n".join(lines)
