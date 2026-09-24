"""
Reconciliation (Phase 3, docs/execution_plan.md decisions 7 and 18). Pure function.

Compares what the bot believes (BotState) with what Binance reports (positions and
open orders) and returns the actions needed — it executes nothing itself. Binance is
the source of truth. Only orders whose clientOrderId starts with BOT_PREFIX belong to
the bot; anything else (e.g. a manual trade by the user) is never modified — a
position the bot didn't open pauses that symbol and raises an alert instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from xrp_futures.execution.state import BotState

BOT_PREFIX = "xfb_"
STOP_ORDER_TYPES = {"STOP_MARKET", "STOP"}

# Action kinds
PLACE_STOP = "PLACE_STOP"                  # bot position has no protective stop on the exchange
RECORD_CLOSED = "RECORD_CLOSED"            # bot position is gone on the exchange (stop filled while away)
CANCEL_ORPHAN_STOP = "CANCEL_ORPHAN_STOP"  # bot stop order without a bot position
CANCEL_DUPLICATE_STOP = "CANCEL_DUPLICATE_STOP"
ADOPT_STOP = "ADOPT_STOP"                  # exactly one bot stop exists but state has another id
PAUSE_SYMBOL = "PAUSE_SYMBOL"              # needs a human: unknown position or quantity mismatch


@dataclass(frozen=True)
class ExchangePosition:
    symbol: str
    amt: Decimal                  # signed: >0 long, <0 short
    entry_price: float


@dataclass(frozen=True)
class ExchangeOrder:
    symbol: str
    client_order_id: str
    type: str
    stop_price: float

    @property
    def is_bot_stop(self) -> bool:
        return self.client_order_id.startswith(BOT_PREFIX) and self.type in STOP_ORDER_TYPES


@dataclass(frozen=True)
class Action:
    kind: str
    symbol: str
    detail: str = ""
    client_order_id: str | None = None


def parse_positions(raw: list[dict]) -> dict[str, ExchangePosition]:
    """futures_position_information() rows -> non-zero positions by symbol (one-way mode)."""
    out = {}
    for p in raw:
        amt = Decimal(p["positionAmt"])
        if amt != 0:
            out[p["symbol"]] = ExchangePosition(p["symbol"], amt, float(p["entryPrice"]))
    return out


def parse_orders(raw: list[dict]) -> list[ExchangeOrder]:
    """futures_get_open_orders() rows -> ExchangeOrder list."""
    return [ExchangeOrder(o["symbol"], o.get("clientOrderId", ""), o["type"], float(o.get("stopPrice") or 0))
            for o in raw]


def reconcile(state: BotState, positions: dict[str, ExchangePosition], orders: list[ExchangeOrder],
              universe) -> list[Action]:
    actions: list[Action] = []
    for symbol in universe:
        if symbol in state.paused:
            continue   # a human must look at it first; don't pile on actions
        local = state.positions.get(symbol)
        remote = positions.get(symbol)
        bot_stops = [o for o in orders if o.symbol == symbol and o.is_bot_stop]

        if local is None:
            if remote is not None:
                actions.append(Action(PAUSE_SYMBOL, symbol,
                                      f"position {remote.amt} not opened by the bot — left untouched"))
            for o in bot_stops:
                actions.append(Action(CANCEL_ORPHAN_STOP, symbol, "bot stop without a bot position",
                                      o.client_order_id))
            continue

        if remote is None:
            actions.append(Action(RECORD_CLOSED, symbol, "position closed on the exchange (stop likely filled)"))
            for o in bot_stops:
                actions.append(Action(CANCEL_ORPHAN_STOP, symbol, "stop left after position closed",
                                      o.client_order_id))
            continue

        if remote.amt != local.signed_qty:
            actions.append(Action(PAUSE_SYMBOL, symbol,
                                  f"quantity mismatch: bot {local.signed_qty}, exchange {remote.amt}"))
            continue

        if not bot_stops:
            actions.append(Action(PLACE_STOP, symbol, f"missing stop at {local.stop_price}"))
            continue
        keep = next((o for o in bot_stops if o.client_order_id == local.stop_client_order_id), bot_stops[0])
        if keep.client_order_id != local.stop_client_order_id:
            actions.append(Action(ADOPT_STOP, symbol, "state had a different stop id", keep.client_order_id))
        for o in bot_stops:
            if o is not keep:
                actions.append(Action(CANCEL_DUPLICATE_STOP, symbol, "extra bot stop", o.client_order_id))
    return actions
