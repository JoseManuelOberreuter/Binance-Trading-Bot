"""
Entry sizing for the live bot (Phase 3, docs/execution_plan.md). Pure function.

Same math as the backtest engine (risk/position_sizing + risk/stops): notional =
equity × risk_pct / initial-stop distance, with the stop at 2×ATR of the last
CLOSED candle. Then exchange rounding. An entry that can't be placed within the
rules is returned as a Skip with a reason — never upsized, never forced.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from xrp_futures.config.live import LiveConfig
from xrp_futures.execution.symbol_filters import SymbolFilters
from xrp_futures.risk import position_sizing as ps
from xrp_futures.risk import stops as stp


@dataclass(frozen=True)
class EntryPlan:
    symbol: str
    side: int                # 1 long, -1 short
    qty: Decimal
    notional: float          # qty × reference price, after rounding
    stop_price: Decimal      # initial stop, rounded to tick


@dataclass(frozen=True)
class Skip:
    symbol: str
    reason: str              # "kill_switch" | "invalid_atr" | "below_minimum" | "exposure_cap"
    detail: str = ""
    target_usdt: float | None = None     # the notional the risk budget allowed
    limit_usdt: float | None = None      # the exchange minimum or the remaining exposure cap


def plan_entry(
    symbol: str,
    side: int,
    equity: float,
    price: float,
    atr: float,
    filters: SymbolFilters,
    open_notional: float,
    config: LiveConfig,
    kill_switch_tripped: bool = False,
) -> EntryPlan | Skip:
    """
    `price` is the reference price the order is expected to fill near (mark price);
    `atr` is from the last closed candle; `open_notional` is the notional already
    open across all symbols (for the portfolio exposure cap).
    """
    if kill_switch_tripped:
        return Skip(symbol, "kill_switch", "drawdown limit reached; new entries blocked")
    if not (math.isfinite(atr) and atr > 0 and price > 0 and equity > 0):
        return Skip(symbol, "invalid_atr", f"atr={atr} price={price} equity={equity}")

    stop = stp.atr_stop_price(price, atr, config.initial_stop_atr_multiplier, side)
    distance = stp.stop_distance_pct(price, stop)
    target = ps.risk_per_trade_notional(equity_usdt=equity, risk_pct=config.risk_pct,
                                        stop_distance_pct=distance).notional_usdt

    qty = filters.qty_for_notional(target, price)
    if qty is None:
        minimum = filters.min_order_usdt(price)
        return Skip(symbol, "below_minimum", f"target {target:.2f} USDT < min order {minimum:.2f} USDT",
                    target_usdt=target, limit_usdt=minimum)

    notional = float(qty) * price
    cap = config.max_total_notional_pct * equity
    if open_notional + notional > cap:
        return Skip(symbol, "exposure_cap", f"open {open_notional:.2f} + {notional:.2f} > cap {cap:.2f} USDT",
                    target_usdt=notional, limit_usdt=max(cap - open_notional, 0.0))

    return EntryPlan(symbol, side, qty, notional, filters.round_price(stop))
