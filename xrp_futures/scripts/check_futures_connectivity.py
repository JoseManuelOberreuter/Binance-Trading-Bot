"""
Phase 1 (docs/execution_plan.md): READ-ONLY connectivity check against Binance
USDⓈ-M Futures. Places, modifies and cancels nothing — only GET endpoints.

Checks: server reachability, per-symbol order filters + mark price for the whole
universe, and (with API keys) the futures wallet, position mode, open positions
and open orders. In production it also checks the API key's permissions and warns
if Spot trading or withdrawals are enabled (the bot must never touch Spot).

Usage:
  python -m xrp_futures.scripts.check_futures_connectivity               # FUTURES_ENVIRONMENT (default testnet)
  python -m xrp_futures.scripts.check_futures_connectivity --public-only # no API keys needed
"""

from __future__ import annotations

import argparse

from binance.client import Client
from rich.console import Console
from rich.table import Table

from xrp_futures.config.live import DEFAULT
from xrp_futures.execution.client import PRODUCTION, futures_environment, get_futures_client
from xrp_futures.execution.symbol_filters import load_filters

console = Console()


def check_public(client: Client) -> None:
    client.futures_ping()
    filters = load_filters(client, DEFAULT.universe, DEFAULT.use_production_filters)
    source = "production" if DEFAULT.use_production_filters or not client.testnet else "testnet"

    t = Table(title=f"Universe: order filters ({source} rules) and mark price")
    for col in ["Symbol", "Mark price", "Tick", "Qty step", "Min qty", "Min notional", "Min order (USDT)"]:
        t.add_column(col, justify="right")
    for symbol, f in filters.items():
        mark = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        t.add_row(symbol, f"{mark:,.4f}", f"{f.tick_size:f}", f"{f.step_size:f}", f"{f.min_qty:f}",
                  f"{f.min_notional:f}", f"{f.min_order_usdt(mark):,.2f}")
    console.print(t)


def check_account(client: Client, environment: str) -> None:
    if environment == PRODUCTION:
        check_production_key_permissions(client)

    account = client.futures_account()
    console.print(f"\n[bold]Futures wallet[/bold]: wallet={float(account['totalWalletBalance']):,.2f} USDT  "
                  f"available={float(account['availableBalance']):,.2f} USDT  "
                  f"unrealized={float(account['totalUnrealizedProfit']):+,.2f} USDT")

    dual = client.futures_get_position_mode()["dualSidePosition"]
    mode_ok = "[green]OK[/green]" if not dual else "[red]HEDGE MODE — plan requires one-way[/red]"
    console.print(f"Position mode: {'hedge' if dual else 'one-way'} {mode_ok}")

    positions = [p for p in client.futures_position_information() if float(p["positionAmt"]) != 0]
    console.print(f"Open positions: {len(positions)}")
    for p in positions:
        console.print(f"  {p['symbol']:<10} amt={p['positionAmt']:>12} entry={p['entryPrice']:>12} "
                      f"liq={p['liquidationPrice']:>12} margin={p.get('marginType', '?')}")

    orders = client.futures_get_open_orders()
    console.print(f"Open orders: {len(orders)}")
    for o in orders:
        console.print(f"  {o['symbol']:<10} {o['type']:<12} {o['side']:<5} qty={o['origQty']} id={o['clientOrderId']}")
    # Stops are algo orders since 2025-12-09 and don't show up in the list above.
    algo = client.futures_get_open_algo_orders()
    console.print(f"Open stop (algo) orders: {len(algo)}")
    for o in algo:
        console.print(f"  {o['symbol']:<10} {o['orderType']:<12} {o['side']:<5} qty={o['quantity']} "
                      f"trigger={o['triggerPrice']} id={o['clientAlgoId']}")


def check_production_key_permissions(client: Client) -> None:
    """Warn loudly if the production key could touch Spot or withdraw (plan decision 14)."""
    perms = client.get_account_api_permissions()
    risky = {k: perms.get(k) for k in ("enableSpotAndMarginTrading", "enableWithdrawals", "enableInternalTransfer")}
    if not perms.get("enableFutures"):
        console.print("[red]API key does NOT have Futures permission enabled.[/red]")
    for k, v in risky.items():
        status = "[red]ENABLED — disable it in Binance API management[/red]" if v else "[green]disabled[/green]"
        console.print(f"  {k}: {status}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-only", action="store_true", help="skip account checks (no API keys needed)")
    args = parser.parse_args()

    environment = futures_environment()
    console.print(f"[bold]Environment:[/bold] {environment}")
    if args.public_only:
        client = Client(testnet=(environment != PRODUCTION))
        check_public(client)
        return

    client = get_futures_client(environment)
    check_public(client)
    check_account(client, environment)
    console.print("\n[green]Read-only check finished — no orders were placed, modified or cancelled.[/green]")


if __name__ == "__main__":
    main()
