"""
Phase 4 step 1 (docs/execution_plan.md decision 20): TESTNET-ONLY probe of the order
flow the bot depends on, printing Binance's raw responses so parsers are built on
the real format, not on assumptions:

  1. isolated margin + 3x on XRPUSDT
  2. market BUY ~6 USDT (fake money)
  3. algo STOP_MARKET reduceOnly 10% below, CONTRACT_PRICE trigger
  4. confirm it shows in openAlgoOrders (and NOT in regular open orders)
  5. cancel it, confirm it's gone
  6. market SELL reduceOnly to close; read the fills (realizedPnl, commission)

Refuses to run against production. Cleans up (cancel stop, close position) on error.

Usage:
  python -m xrp_futures.scripts.probe_testnet_stop
"""

from __future__ import annotations

import json
import time

from binance.exceptions import BinanceAPIException
from rich.console import Console

from xrp_futures.execution.client import TESTNET, futures_environment, get_futures_client
from xrp_futures.execution.symbol_filters import filters_for

console = Console()
SYMBOL = "XRPUSDT"
TARGET_USDT = 6.0


def show(title: str, payload) -> None:
    console.print(f"\n[bold cyan]{title}[/bold cyan]")
    console.print(json.dumps(payload, indent=2, default=str))


def main() -> None:
    if futures_environment() != TESTNET:
        raise SystemExit("probe_testnet_stop only runs with FUTURES_ENVIRONMENT=testnet")
    client = get_futures_client(TESTNET)
    f = filters_for(client.futures_exchange_info(), [SYMBOL])[SYMBOL]

    for call, kwargs in ((client.futures_change_margin_type, dict(symbol=SYMBOL, marginType="ISOLATED")),
                         (client.futures_change_leverage, dict(symbol=SYMBOL, leverage=3))):
        try:
            show(call.__name__, call(**kwargs))
        except BinanceAPIException as e:   # -4046 "No need to change margin type" is fine
            show(f"{call.__name__} (exception)", {"code": e.code, "message": e.message})

    tag = str(int(time.time()))
    stop_id = f"xfb_probe_s_{tag}"
    opened_qty = None
    stop_placed = False
    try:
        price = float(client.futures_mark_price(symbol=SYMBOL)["markPrice"])
        qty = f.qty_for_notional(TARGET_USDT, price)
        entry = client.futures_create_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=str(qty),
                                            newClientOrderId=f"xfb_probe_e_{tag}", newOrderRespType="RESULT")
        show("1) market entry response", entry)
        opened_qty = qty

        pos = [p for p in client.futures_position_information(symbol=SYMBOL) if float(p["positionAmt"]) != 0]
        show("2) position after entry", pos)

        stop_price = f.round_price(price * 0.90)
        stop = client.futures_create_algo_order(
            algoType="CONDITIONAL", symbol=SYMBOL, side="SELL", type="STOP_MARKET", quantity=str(qty),
            triggerPrice=str(stop_price), reduceOnly="true", workingType="CONTRACT_PRICE",
            priceProtect="FALSE", clientAlgoId=stop_id)
        stop_placed = True
        show("3) algo stop response", stop)

        show("4a) openAlgoOrders", client.futures_get_open_algo_orders(symbol=SYMBOL))
        show("4b) regular open orders (stop should NOT be here)", client.futures_get_open_orders(symbol=SYMBOL))

        show("5a) cancel algo stop", client.futures_cancel_algo_order(symbol=SYMBOL, clientAlgoId=stop_id))
        stop_placed = False
        show("5b) openAlgoOrders after cancel", client.futures_get_open_algo_orders(symbol=SYMBOL))
    finally:
        if stop_placed:
            try:
                client.futures_cancel_algo_order(symbol=SYMBOL, clientAlgoId=stop_id)
                console.print("[yellow]cleanup: stop cancelled[/yellow]")
            except BinanceAPIException as e:
                console.print(f"[red]cleanup: could not cancel stop {stop_id}: {e.message}[/red]")
        if opened_qty is not None:
            close = client.futures_create_order(symbol=SYMBOL, side="SELL", type="MARKET", quantity=str(opened_qty),
                                                reduceOnly="true", newClientOrderId=f"xfb_probe_x_{tag}",
                                                newOrderRespType="RESULT")
            show("6a) close response", close)

    time.sleep(1)
    show("6b) fills (account trades)", client.futures_account_trades(symbol=SYMBOL, limit=4))
    left = [p for p in client.futures_position_information(symbol=SYMBOL) if float(p["positionAmt"]) != 0]
    console.print(f"\n[green]Probe done. Remaining {SYMBOL} position: {left or 'none'}; "
                  f"open algo orders: {len(client.futures_get_open_algo_orders(symbol=SYMBOL))}[/green]")


if __name__ == "__main__":
    main()
