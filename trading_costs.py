# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 交易成本與部位 Sizing 元件 (spec 008b)

可插拔的 CostModel / PositionSizer 元件，依資產類別注入 BacktestEngine：
- Equity 元件**逐字重現** backtester.py 現行內聯公式（現貨路徑位元不變，SC-001）。
- Futures 元件實作台指期模型：每口定額費兩邊 + 期交稅兩邊 + tick 滑價、
  名目值百分比保證金、使用率上限整數口 sizing（FR-002/004/005/012）。

費率來源（憲章 II/V）：帳戶政策層 = config `trading_cost`（含 `futures` 巢狀）；
契約內生層 = instrument 的 `contract`（ContractSpec）。本模組不得出現費率常數。

術語註記：「契約金額」（期交稅基）與「名目值」（保證金基）為同一量——
價格(點) × point_value × 口數。

價格基準（spec 011）：本模組**對基準無知**——給什麼價就算什麼，簽章不含
基準概念。選價是呼叫端（BacktestEngine）的責任，語意為：
- `FuturesSizer.size` / `margin_per_lot` 的 price = 訊號根**未調整**收盤
- `FuturesCostModel.entry_costs` / `exit_costs` 的 price = 成交根**未調整**價
  套滑價後之值（稅基）
- Equity 元件一律維持成交價（現行語意，位元不變）
理由：back-adjust 連續序列的價位水準不等於當年真實市價（TXF 實測早年偏離
約 45 倍、最低穿零至 −5,312），凡「價位 × 乘數」型的名目值計算都必須用
未調整價，否則保證金低估數十倍、負價位甚至算出負稅額。
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import NamedTuple, Tuple

from instruments import AssetClass, ContractSpec, Instrument


class TradeCosts(NamedTuple):
    """單邊成交的摩擦成本明細（NT$）。滑價不在此——滑價內含於成交價偏移。"""
    commission: float   # 手續費（現股=比例；期貨=交易所定額+券商加收）
    tax: float          # 稅（現股=證交稅僅賣邊；期貨=期交稅兩邊）

    @property
    def total(self) -> float:
        return self.commission + self.tax


class CostModel(ABC):
    """每筆成交（單邊）的摩擦成本與滑價。純函式、無狀態、O(1)（憲章 IV）。"""

    @abstractmethod
    def slip(self, raw_price: float, side: str) -> float:
        """回傳滑價後成交價；side ∈ {"buy","sell"}，方向恆不利。"""

    @abstractmethod
    def entry_costs(self, price: float, units: float) -> TradeCosts:
        """買/開倉邊摩擦成本。price 為滑價後成交價。"""

    @abstractmethod
    def exit_costs(self, price: float, units: float) -> TradeCosts:
        """賣/平倉邊摩擦成本。"""


class PositionSizer(ABC):
    """進場部位單位決定。輸入僅允許決策時點已知資訊（憲章 I / FR-007）。

    `size` 的 price 輸入語意依實作而異（analyze M1）：
    - EquitySizer：**成交價**（現行語意——成交當下以成交價算最大可負擔）
    - FuturesSizer：**訊號根收盤價**（FR-004 保證金以訊號根名目值計）
    引擎按資產類別傳入對應價格。
    """

    @abstractmethod
    def size(self, equity: float, price: float) -> float:
        """回傳部位單位（現股=股數、期貨=口數）。0 = 不進場。"""

    @abstractmethod
    def partial_units(self, held: float, fraction: float) -> float:
        """部分出場單位；0 = 跳過該次平倉（呼叫端仍執行對應風控動作）。"""


# ---------------------------------------------------------------------------
# Equity 元件：逐字重現 backtester.py 現行內聯公式（SC-001 位元不變支柱）
# ---------------------------------------------------------------------------

class EquityCostModel(CostModel):
    """現貨 ad-valorem 成本：手續費兩邊、證交稅僅賣邊、比例滑價。"""

    def __init__(self, commission_rate: float, tax_rate: float, slippage_rate: float):
        self.commission_rate = commission_rate
        self.tax_rate = tax_rate
        self.slippage_rate = slippage_rate

    def slip(self, raw_price: float, side: str) -> float:
        if side == "buy":
            return raw_price * (1 + self.slippage_rate)
        return raw_price * (1 - self.slippage_rate)

    def entry_costs(self, price: float, units: float) -> TradeCosts:
        cost = units * price
        return TradeCosts(commission=cost * self.commission_rate, tax=0.0)

    def exit_costs(self, price: float, units: float) -> TradeCosts:
        revenue = units * price
        return TradeCosts(commission=revenue * self.commission_rate,
                          tax=revenue * self.tax_rate)


class EquitySizer(PositionSizer):
    """現貨整張 sizing：最大可負擔股數向下取整至 lot_size 倍數。"""

    def __init__(self, commission_rate: float, lot_size: int):
        self.commission_rate = commission_rate
        self.lot_size = lot_size

    def _round_to_lot(self, shares: float) -> float:
        if self.lot_size <= 1:
            return float(shares)
        return float(int(shares // self.lot_size) * self.lot_size)

    def size(self, equity: float, price: float) -> float:
        # price = 成交價（現行語意：backtester 於成交根以 execution_price 計算）
        max_affordable = equity / (price * (1.0 + self.commission_rate))
        return self._round_to_lot(max_affordable)

    def partial_units(self, held: float, fraction: float) -> float:
        return self._round_to_lot(held * fraction)


# ---------------------------------------------------------------------------
# Futures 元件（spec 008b FR-002/004/005/012）
# ---------------------------------------------------------------------------

class FuturesCostModel(CostModel):
    """台指期每口成本：定額費兩邊 + 期交稅兩邊（契約金額×稅率）+ tick 滑價（點偏移）。"""

    def __init__(self, contract: ContractSpec, fut_cfg):
        self.contract = contract
        self.fee_per_lot = contract.exchange_fee_per_lot + fut_cfg.broker_commission_per_lot
        self.tax_rate = fut_cfg.tax_rate
        self.slippage_points = fut_cfg.slippage_ticks * contract.tick_size

    def slip(self, raw_price: float, side: str) -> float:
        # 點數偏移（非百分比），方向恆不利；滑價成本內含於成交價、不重複計費
        if side == "buy":
            return raw_price + self.slippage_points
        return raw_price - self.slippage_points

    def _side_costs(self, price: float, units: float) -> TradeCosts:
        notional = price * self.contract.point_value * units   # 契約金額（= 名目值）
        return TradeCosts(commission=self.fee_per_lot * units,
                          tax=notional * self.tax_rate)

    def entry_costs(self, price: float, units: float) -> TradeCosts:
        return self._side_costs(price, units)

    def exit_costs(self, price: float, units: float) -> TradeCosts:
        return self._side_costs(price, units)


class FuturesSizer(PositionSizer):
    """保證金式整數口 sizing：口數 = floor(權益 × 使用率 ÷ 每口保證金)。"""

    def __init__(self, contract: ContractSpec, fut_cfg):
        self.contract = contract
        self.margin_rate = fut_cfg.margin_rate
        self.margin_utilization = fut_cfg.margin_utilization

    def margin_per_lot(self, price: float) -> float:
        """每口保證金 = 名目值（點數 × point_value）× margin_rate（FR-004）。"""
        return price * self.contract.point_value * self.margin_rate

    def size(self, equity: float, price: float) -> float:
        # price = 訊號根收盤價（FR-004/FR-007：只用訊號根已知資訊）
        per_lot = self.margin_per_lot(price)
        if per_lot <= 0.0 or equity <= 0.0:
            return 0.0
        return float(math.floor(equity * self.margin_utilization / per_lot))

    def partial_units(self, held: float, fraction: float) -> float:
        # FR-012：floor；0 口時呼叫端跳過平倉但風控（移保本位）照做
        return float(math.floor(held * fraction))


# ---------------------------------------------------------------------------
# 工廠
# ---------------------------------------------------------------------------

def for_asset_class(instrument: Instrument | None, config) -> Tuple[CostModel, PositionSizer]:
    """依 instrument 資產類別自 config 建構 (CostModel, PositionSizer)。

    instrument 為 None 或 equity → 現股元件（現行語意）；
    futures → 期貨元件（需 instrument.contract，Pydantic 已保證非 None）。
    """
    tc = config.trading_cost
    if instrument is None or instrument.asset_class == AssetClass.EQUITY:
        return (EquityCostModel(tc.commission_rate, tc.tax_rate, tc.slip_rate),
                EquitySizer(tc.commission_rate, tc.lot_size))
    if instrument.contract is None:  # 理論上被 Pydantic validator 擋下（防禦性）
        raise ValueError(f"futures instrument '{instrument.id}' 缺 contract，無法建構成本元件")
    fut = tc.futures
    return (FuturesCostModel(instrument.contract, fut),
            FuturesSizer(instrument.contract, fut))
