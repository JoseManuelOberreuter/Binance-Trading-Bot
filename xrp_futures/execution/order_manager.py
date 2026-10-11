"""
Exchange adapter for the live engine (Phase 4, docs/execution_plan.md).

BinanceFuturesExchange is the ONLY place that sends orders. Formats were verified on
testnet by scripts/probe_testnet_stop.py:
  - market orders: futures_create_order; the response has no fill price, so fills
    (price, commission, realizedPnl) are read back from futures_account_trades(orderId)
  - stops: algo STOP_MARKET (clientAlgoId, triggerPrice, trigger on mark price by default), listed by
    futures_get_open_algo_orders, cancelled by futures_cancel_algo_order
Every order id carries the bot prefix (reconcile.BOT_PREFIX) so the bot only ever
manages its own orders.

DryRunExchange wraps it for --dry-run: reads pass through, every write is logged and
answered with a synthetic result — nothing is sent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from binance.client import Client
from binance.exceptions import BinanceAPIException

from xrp_futures.execution.reconcile import (
    BOT_PREFIX, ExchangeOrder, ExchangePosition, parse_algo_orders, parse_positions,
)
from xrp_futures.execution.symbol_filters import SymbolFilters

log = logging.getLogger("xfb")

SEVEN_DAYS_MS = 7 * 24 * 3600 * 1000
# Binance codes meaning "the order may or may not have executed" (send/execution status
# unknown). Any other API error on an order is a definite rejection.
UNKNOWN_STATUS_CODES = {-1001, -1006, -1007}


def outcome_unknown(exc: Exception) -> bool:
    """True if, after this exception, an order we sent might still have executed."""
    return not isinstance(exc, BinanceAPIException) or exc.code in UNKNOWN_STATUS_CODES


@dataclass(frozen=True)
class Fill:
    order_id: int
    price: float              # quantity-weighted average fill price
    qty: Decimal
    commission: float         # USDT
    realized_pnl: float       # Binance's realizedPnl (before commission); 0 for opening fills
    time_ms: int


@dataclass(frozen=True)
class Snapshot:
    positions: dict[str, ExchangePosition]
    stops: list[ExchangeOrder]
    wallet_equity: float      # totalMarginBalance (wallet + unrealized)


def new_client_id(kind: str, symbol: str) -> str:
    """e.g. xfb_e_XRPUSDT_1790285426863 (<= 36 chars, Binance's limit)."""
    return f"{BOT_PREFIX}{kind}_{symbol}_{int(time.time() * 1000)}"


def aggregate_fills(order_id: int, trades: list[dict]) -> Fill:
    rows = [t for t in trades if int(t["orderId"]) == order_id]
    if not rows:
        raise LookupError(f"no fills found for order {order_id}")
    qty = sum(Decimal(t["qty"]) for t in rows)
    notional = sum(float(t["qty"]) * float(t["price"]) for t in rows)
    return Fill(order_id=order_id, price=notional / float(qty), qty=qty,
                commission=sum(float(t["commission"]) for t in rows),
                realized_pnl=sum(float(t["realizedPnl"]) for t in rows),
                time_ms=max(int(t["time"]) for t in rows))


class BinanceFuturesExchange:
    def __init__(self, client: Client, filters: dict[str, SymbolFilters], stop_working_type: str = "MARK_PRICE"):
        if stop_working_type not in ("MARK_PRICE", "CONTRACT_PRICE"):
            raise ValueError(f"stop_working_type must be MARK_PRICE or CONTRACT_PRICE, got {stop_working_type!r}")
        self.client = client
        self.filters = filters
        self.stop_working_type = stop_working_type

    # ---- reads -------------------------------------------------------------------
    def sync_clock(self) -> int:
        """Align signed-request timestamps with Binance's clock (avoids -1021 after the PC
        sleeps or drifts). Returns the offset applied, in ms."""
        server = int(self.client.futures_time()["serverTime"])
        self.client.timestamp_offset = server - int(time.time() * 1000)
        return self.client.timestamp_offset

    def snapshot(self) -> Snapshot:
        account = self.client.futures_account()
        return Snapshot(
            positions=parse_positions(self.client.futures_position_information()),
            stops=parse_algo_orders(self.client.futures_get_open_algo_orders()),
            wallet_equity=float(account["totalMarginBalance"]),
        )

    def mark_price(self, symbol: str) -> float:
        return float(self.client.futures_mark_price(symbol=symbol)["markPrice"])

    def order_fill(self, symbol: str, order_id: int, retries: int = 5) -> Fill:
        for attempt in range(retries):
            try:
                return aggregate_fills(order_id, self.client.futures_account_trades(symbol=symbol, orderId=order_id))
            except LookupError:
                time.sleep(0.5 * (attempt + 1))   # fills can lag the order response briefly
        raise LookupError(f"{symbol}: fills for order {order_id} not available")

    def entry_status(self, symbol: str, client_id: str) -> Fill | None:
        """
        What happened to an entry order whose response was lost: its Fill if it executed,
        None if Binance has no such order (or it ended unfilled). Raises LookupError while
        it's still being processed, and lets other errors through (status still unknown).
        """
        try:
            order = self.client.futures_get_order(symbol=symbol, origClientOrderId=client_id)
        except BinanceAPIException as e:
            if e.code == -2013:          # order does not exist
                return None
            raise
        if float(order.get("executedQty", 0)) > 0 and order["status"] != "NEW":
            return self.order_fill(symbol, int(order["orderId"]))
        if order["status"] in ("NEW", "PARTIALLY_FILLED"):
            raise LookupError(f"{symbol}: entry {client_id} still {order['status']}")
        return None

    def stop_fill(self, symbol: str, client_algo_id: str) -> Fill | None:
        """Fill of a stop that triggered while the bot wasn't watching; None if it didn't."""
        try:
            algo = self.client.futures_get_algo_order(symbol=symbol, clientAlgoId=client_algo_id)
        except BinanceAPIException as e:
            log.warning("%s: cannot look up stop %s: %s", symbol, client_algo_id, e.message)
            return None
        actual = algo.get("actualOrderId")
        if not actual:
            return None
        return self.order_fill(symbol, int(actual))

    def funding_since(self, symbol: str, since_ms: int) -> float:
        """Sum of FUNDING_FEE income for symbol since since_ms (queried in <=7-day windows)."""
        total, start, now = 0.0, since_ms, int(time.time() * 1000)
        while start < now:
            end = min(start + SEVEN_DAYS_MS, now)
            rows = self.client.futures_income_history(symbol=symbol, incomeType="FUNDING_FEE",
                                                      startTime=start, endTime=end, limit=1000)
            total += sum(float(r["income"]) for r in rows)
            start = end + 1
        return total

    # ---- writes ------------------------------------------------------------------
    def setup_symbol(self, symbol: str, leverage: int) -> None:
        """Isolated margin + leverage, changing only what differs (Binance rejects margin-type
        changes while the symbol has open orders, even when it's already isolated)."""
        current = self.client.futures_symbol_config(symbol=symbol)[0]
        changes = []
        if current["marginType"] != "ISOLATED":
            changes.append((self.client.futures_change_margin_type, dict(symbol=symbol, marginType="ISOLATED")))
        if int(current["leverage"]) != leverage:
            changes.append((self.client.futures_change_leverage, dict(symbol=symbol, leverage=leverage)))
        for call, kwargs in changes:
            try:
                call(**kwargs)
                log.info("%s: %s %s", symbol, call.__name__, kwargs)
            except BinanceAPIException as e:
                log.warning("%s: %s failed: %s (%s) — current: %s %sx", symbol, call.__name__, e.message, e.code,
                            current["marginType"], current["leverage"])

    def ensure_margin(self, symbol: str, leverage: int) -> str | None:
        """Make sure the symbol is ISOLATED at `leverage` before entering. Returns None if so,
        else what it actually is. Cross margin would let one liquidation drain the whole
        wallet, and a higher leverage puts liquidation closer than the stop."""
        self.setup_symbol(symbol, leverage)
        current = self.client.futures_symbol_config(symbol=symbol)[0]
        if current["marginType"] != "ISOLATED" or int(current["leverage"]) != leverage:
            return f"{current['marginType']} {current['leverage']}x (expected ISOLATED {leverage}x)"
        return None

    def market_order(self, symbol: str, side: int, qty: Decimal, reduce_only: bool,
                     client_id: str | None = None) -> Fill:
        client_id = client_id or new_client_id("x" if reduce_only else "e", symbol)
        params = dict(symbol=symbol, side="BUY" if side == 1 else "SELL", type="MARKET", quantity=str(qty),
                      newClientOrderId=client_id, newOrderRespType="RESULT")
        if reduce_only:
            params["reduceOnly"] = "true"
        order = self.client.futures_create_order(**params)
        return self.order_fill(symbol, int(order["orderId"]))

    def place_stop(self, symbol: str, position_side: int, qty: Decimal, stop_price: Decimal) -> str:
        cid = new_client_id("s", symbol)
        self.client.futures_create_algo_order(
            algoType="CONDITIONAL", symbol=symbol, side="SELL" if position_side == 1 else "BUY",
            type="STOP_MARKET", quantity=str(qty), triggerPrice=str(stop_price), reduceOnly="true",
            workingType=self.stop_working_type, priceProtect="FALSE", clientAlgoId=cid)
        return cid

    def cancel_stop(self, symbol: str, client_algo_id: str) -> None:
        try:
            self.client.futures_cancel_algo_order(symbol=symbol, clientAlgoId=client_algo_id)
        except BinanceAPIException as e:   # already triggered/cancelled: nothing left to cancel
            log.warning("%s: cancel stop %s: %s", symbol, client_algo_id, e.message)


class DryRunExchange:
    """Pass-through reads; every write is logged and answered synthetically. Sends nothing."""

    def __init__(self, real: BinanceFuturesExchange):
        self.real = real
        self.writes: list[str] = []

    def sync_clock(self) -> int:
        return self.real.sync_clock()

    def snapshot(self) -> Snapshot:
        return self.real.snapshot()

    def entry_status(self, symbol: str, client_id: str) -> Fill | None:
        return None    # dry run never sends entries

    def ensure_margin(self, symbol: str, leverage: int) -> str | None:
        self._record(f"ensure {symbol} ISOLATED {leverage}x")
        return None

    def mark_price(self, symbol: str) -> float:
        return self.real.mark_price(symbol)

    def stop_fill(self, symbol: str, client_algo_id: str) -> Fill | None:
        return self.real.stop_fill(symbol, client_algo_id)

    def funding_since(self, symbol: str, since_ms: int) -> float:
        return self.real.funding_since(symbol, since_ms)

    def _record(self, msg: str) -> None:
        self.writes.append(msg)
        log.info("[DRY-RUN] would %s", msg)

    def setup_symbol(self, symbol: str, leverage: int) -> None:
        self._record(f"set {symbol} ISOLATED {leverage}x")

    def market_order(self, symbol: str, side: int, qty: Decimal, reduce_only: bool,
                     client_id: str | None = None) -> Fill:
        price = self.mark_price(symbol)
        self._record(f"{'BUY' if side == 1 else 'SELL'} {qty} {symbol} at market"
                     f"{' (reduce-only)' if reduce_only else ''} ~{price}")
        return Fill(order_id=0, price=price, qty=qty, commission=0.0, realized_pnl=0.0,
                    time_ms=int(time.time() * 1000))

    def place_stop(self, symbol: str, position_side: int, qty: Decimal, stop_price: Decimal) -> str:
        self._record(f"place STOP_MARKET {'SELL' if position_side == 1 else 'BUY'} {qty} {symbol} @ {stop_price}")
        return new_client_id("s", symbol)

    def cancel_stop(self, symbol: str, client_algo_id: str) -> None:
        self._record(f"cancel stop {client_algo_id} on {symbol}")
