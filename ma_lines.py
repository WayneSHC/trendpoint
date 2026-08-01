# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 均線觸價通知的純函式元件 (spec 014)。

本模組提供兩個**無狀態純函式**：

1. `compute_ma_set`：由日線收盤價計算月／季／半年／年線（簡單移動平均）。
2. `detect_cross_below`：判定比較價是否**向下穿越**某條均線。

刻意與 `ladder_system.py` 分離的理由：那是「交易訊號的指標組裝入口」，
其對外契約（spec 004 的前綴一致性、無狀態）是為回測服務；本模組的均線是
**通知用的參考價位**，不進入任何訊號或回測路徑。混入會擴大該入口的職責，
並讓「這條均線有沒有進訊號」變得不明確——而答案必須是明確的「沒有」。

本模組不 import `monitor_signals` / `backtester` / `ladder_system`（單向依賴），
不讀 config、不做 I/O，故可獨立單元測試。
"""

from typing import Dict, List, Optional, Sequence

import pandas as pd


def compute_ma_set(daily_close: pd.Series,
                   periods: Dict[str, int]) -> Dict[str, Optional[float]]:
    """
    由**已收盤**日線收盤價計算各週期的簡單移動平均 (SMA)。

    參數:
        daily_close: 已收盤日線收盤價序列（時序遞增）。呼叫端負責排除
            當日進行中的 K 線——本函式不知道「今天」是哪天。
        periods: {線別: 週期根數}，例如 {"yearly": 240}。

    回傳:
        {線別: 均線值 或 None}。鍵集合與 `periods` 相同。

    **資料不足一律回傳 None，不回傳 NaN、不回傳 0、不以較短窗口替代。**

    為何禁止 `min_periods=1`：`ladder_system.calculate_regime_filter`
    （`ladder_system.py:463`）刻意使用 `rolling(..., min_periods=1)`，
    那是為了避免 200 日暖機期把**整段回測**封死——寧可放行也不要沒有結果。
    用在**通知**上後果完全相反：一檔上市 30 天的股票會被算出一條「年線」
    並推播給使用者，那是誤報，而誤報比漏報更糟。

    為何回傳 None 而非 NaN：`NaN > x` 恰好為 False，看似「剛好正確」，
    但那是實作巧合而非契約——一旦中間插入 `fillna()` 或改用 numpy 比較
    就會翻轉（同 `ladder_system.py:645-649` 的 `atr_ready` 教訓）。
    `None` 迫使呼叫端顯式處理。

    本函式不就地修改輸入。
    """
    result: Dict[str, Optional[float]] = {}
    n = len(daily_close)

    for name, period in periods.items():
        if n < period:
            result[name] = None
            continue
        # 明確取最後 period 根求平均——不使用 rolling，避免任何 min_periods 的餘地
        result[name] = float(daily_close.iloc[-period:].mean())

    return result


def detect_cross_below(prev_price: float,
                       curr_price: float,
                       ma_set: Dict[str, Optional[float]]) -> List[str]:
    """
    判定比較價是否**向下穿越**各條均線，回傳觸發的線別清單。

    判定式（對每條 `ma` 不為 None 的線）::

        穿越成立 ⟺ prev_price > ma  AND  curr_price <= ma

    `<=` 對應原始需求的「達到或低於」——**觸及**均線即算，不必跌破。

    為何是事件（穿越）而非狀態（低於）：去重鍵含 `bar_time`
    （`monitor_signals.py:44-50`），狀態式判定會在價格持續低於均線的期間
    **每根發一次**。使用者已於 2026-07-30 確認採事件語意；其盲點
    （開啟功能時已在均線下方的標的永不觸發）由儀表板現況表補上
    （spec 014 US4／FR-013），而非退回狀態播報。

    **僅偵測向下穿越**。向上突破（站回均線）是對稱但獨立的需求，
    未要求即不做（避免推播量倍增）；需要時可沿用同一機制擴充。

    `ma` 為 None 的線一律略過，不參與判定、不拋錯。
    """
    triggered: List[str] = []

    for name, ma in ma_set.items():
        if ma is None:
            continue
        if prev_price > ma and curr_price <= ma:
            triggered.append(name)

    return triggered


def deviation_pct(price: float, ma: Optional[float]) -> Optional[float]:
    """
    乖離幅度 `(price - ma) / ma`，供通知訊息與儀表板現況表共用（FR-009／FR-013）。

    `ma` 為 None 或 0 時回傳 None——呼叫端須顯式處理（不得顯示 0 或空白，
    那會與「價格恰在均線上」混淆）。
    """
    if ma is None or ma == 0:
        return None
    return (price - ma) / ma


def ordered_line_names(periods: Dict[str, int]) -> List[str]:
    """
    依週期由短到長排序線別——使通知與儀表板的呈現順序穩定
    （月→季→半年→年），不受 dict 插入順序影響。
    """
    return sorted(periods, key=lambda name: periods[name])


# 線別 → 中文顯示名稱。通知訊息與儀表板共用，避免兩處各自維護而漂移。
LINE_LABELS: Dict[str, str] = {
    "monthly": "月線",
    "quarterly": "季線",
    "half_yearly": "半年線",
    "yearly": "年線",
}


def line_label(name: str) -> str:
    """回傳線別的中文顯示名稱；未知線別回傳原名（不拋錯）。"""
    return LINE_LABELS.get(name, name)


def alert_type_for(name: str) -> str:
    """
    線別 → 去重用的 `alert_type`（見 spec 014 data-model.md §3）。

    與既有六種告警（BULLISH_MSS 等）無命名衝突。
    """
    return f"MA_CROSS_BELOW_{name.upper()}"


def build_status_rows(daily_close: pd.Series,
                      periods: Dict[str, int],
                      current_price: float) -> List[dict]:
    """
    產生「均線現況」表的列（spec 014 US4／FR-013），供儀表板呈現。

    每列含：線別（中文）、均線值、目前價、位置、乖離。
    **資料不足之線的 `ma` / `position` / `deviation` 一律為 None**——
    呈現端須顯示「資料不足」，不得顯示空白或 0（FR-013）。

    本函式只計算不呈現、只讀不發——不觸發任何推播（FR-014）。
    """
    ma_set = compute_ma_set(daily_close, periods)
    rows: List[dict] = []

    for name in ordered_line_names(periods):
        ma = ma_set[name]
        rows.append({
            "line": name,
            "label": line_label(name),
            "period": periods[name],
            "ma": ma,
            "price": current_price,
            "position": None if ma is None else ("在上" if current_price > ma else "在下"),
            "deviation": deviation_pct(current_price, ma),
        })

    return rows
