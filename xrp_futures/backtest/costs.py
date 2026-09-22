"""Trading cost model: fees, slippage, funding. Kept separate from the engine so
Optimistic/Base/Pessimistic cost scenarios (§22) are just different CostModel instances."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """
    All fields as fractions (0.001 = 0.10%), matching Binance USDⓈ-M futures taker fee.
    slippage_pct is added on top of the fee at every fill, in the adverse direction.
    """

    taker_fee_pct: float = 0.0004   # Binance USDT-M base taker fee (VIP0, no BNB discount)
    slippage_pct: float = 0.0005    # "Base" scenario per §22 (0 / 0.05% / 0.10% options)
    funding_enabled: bool = True

    def fee_cost(self, notional_usdt: float) -> float:
        return abs(notional_usdt) * self.taker_fee_pct

    def slippage_adjusted_price(self, price: float, side: int, is_entry: bool) -> float:
        """
        Worse fill price in the trade's direction.
        Entry LONG / exit SHORT -> pay slightly more (buying).
        Entry SHORT / exit LONG -> receive slightly less (selling).
        """
        buying = (side == 1) == is_entry
        adj = price * self.slippage_pct
        return price + adj if buying else price - adj


OPTIMISTIC = CostModel(taker_fee_pct=0.0004, slippage_pct=0.0, funding_enabled=True)
BASE = CostModel(taker_fee_pct=0.0004, slippage_pct=0.0005, funding_enabled=True)
PESSIMISTIC = CostModel(taker_fee_pct=0.0004, slippage_pct=0.0010, funding_enabled=True)


def funding_pnl(position_side: int, position_notional: float, funding_rate: float) -> float:
    """
    PnL impact of one funding settlement on an open position.
    Positive funding_rate: LONGs pay SHORTs. position_side is +1 (long) or -1 (short).
    """
    return -position_side * position_notional * funding_rate
