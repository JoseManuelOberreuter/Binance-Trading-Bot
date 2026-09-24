"""
Per-symbol order constraints from futures_exchange_info(): price tick, market-order
quantity step/minimum, and minimum notional. Pure functions over the parsed filters
so rounding/minimum logic is testable without a network.

Rounding is done with Decimal (float step math like 0.1 * 3 drifts) and always
DOWN for quantity — never size a position larger than the risk budget allows.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from binance.client import Client


@dataclass(frozen=True)
class SymbolFilters:
    symbol: str
    tick_size: Decimal       # PRICE_FILTER.tickSize (stop prices)
    step_size: Decimal       # MARKET_LOT_SIZE.stepSize (market entries/exits)
    min_qty: Decimal         # MARKET_LOT_SIZE.minQty
    min_notional: Decimal    # MIN_NOTIONAL.notional (USDT)

    def round_qty(self, qty: float) -> Decimal:
        """Largest multiple of step_size <= qty."""
        return (Decimal(str(qty)) / self.step_size).to_integral_value(rounding=ROUND_DOWN) * self.step_size

    def round_price(self, price: float) -> Decimal:
        """Nearest multiple of tick_size."""
        return (Decimal(str(price)) / self.tick_size).to_integral_value(rounding=ROUND_HALF_UP) * self.tick_size

    def min_order_usdt(self, price: float) -> float:
        """Smallest order that satisfies both min_qty and min_notional at `price`."""
        return max(float(self.min_qty) * price, float(self.min_notional))

    def qty_for_notional(self, notional_usdt: float, price: float) -> Decimal | None:
        """
        Quantity for a target USDT notional, rounded down to the step. None when the
        rounded order falls below min_qty or min_notional — the caller skips (and logs)
        the entry rather than upsizing it past the risk budget.
        """
        if notional_usdt <= 0 or price <= 0:
            return None
        qty = self.round_qty(notional_usdt / price)
        if qty < self.min_qty or float(qty) * price < float(self.min_notional):
            return None
        return qty


def parse_symbol_filters(symbol_info: dict) -> SymbolFilters:
    """Build SymbolFilters from one entry of futures_exchange_info()['symbols']."""
    f = {x["filterType"]: x for x in symbol_info["filters"]}
    lot = f.get("MARKET_LOT_SIZE") or f["LOT_SIZE"]
    return SymbolFilters(
        symbol=symbol_info["symbol"],
        tick_size=Decimal(f["PRICE_FILTER"]["tickSize"]).normalize(),
        step_size=Decimal(lot["stepSize"]).normalize(),
        min_qty=Decimal(lot["minQty"]).normalize(),
        min_notional=Decimal(f["MIN_NOTIONAL"]["notional"]).normalize(),
    )


def filters_for(exchange_info: dict, symbols) -> dict[str, SymbolFilters]:
    """Filters for each requested symbol; raises if any is missing (delisted/typo)."""
    by_symbol = {s["symbol"]: s for s in exchange_info["symbols"]}
    missing = [s for s in symbols if s not in by_symbol]
    if missing:
        raise KeyError(f"symbols not listed on this futures exchange: {missing}")
    return {s: parse_symbol_filters(by_symbol[s]) for s in symbols}


def load_filters(client: Client, symbols, use_production_filters: bool) -> dict[str, SymbolFilters]:
    """
    Filters the bot sizes orders with. With use_production_filters=True the (public,
    keyless) production exchange info is used even when `client` points at testnet,
    so the testnet run skips exactly the orders production would reject.
    """
    source = Client() if use_production_filters and client.testnet else client
    return filters_for(source.futures_exchange_info(), symbols)
