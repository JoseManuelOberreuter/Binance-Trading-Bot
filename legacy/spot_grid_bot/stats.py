import os
from datetime import datetime
from decimal import Decimal

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from exchange.client import get_client

load_dotenv()

console = Console()
SYMBOL = os.getenv("SYMBOL", "ETHUSDT")

STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD"}


def fetch_account_balance(client) -> list[dict]:
    account = client.get_account()
    balances = [
        b for b in account["balances"]
        if Decimal(b["free"]) > 0 or Decimal(b["locked"]) > 0
    ]
    return balances


def fetch_prices_usdt(client, assets: list[str]) -> dict[str, float]:
    """Fetch USDT price for each asset. Returns 1.0 for stablecoins."""
    prices = {}
    for asset in assets:
        if asset in STABLECOINS:
            prices[asset] = 1.0
            continue
        symbol = f"{asset}USDT"
        try:
            ticker = client.get_symbol_ticker(symbol=symbol)
            prices[asset] = float(ticker["price"])
        except Exception:
            prices[asset] = 0.0
    return prices


def fetch_open_orders(client) -> list[dict]:
    """Fetch all open orders (all symbols)."""
    try:
        return client.get_open_orders()
    except Exception:
        return client.get_open_orders(symbol=SYMBOL)


def fetch_recent_trades(client, limit: int = 5) -> list[dict]:
    try:
        return client.get_my_trades(symbol=SYMBOL, limit=limit)
    except Exception:
        return []


def render_header(environment: str) -> None:
    env_color = "green" if environment == "production" else "yellow"
    env_label = f"[bold {env_color}]{environment.upper()}[/bold {env_color}]"
    console.print(Panel(
        f"[bold white]Binance Account Stats[/bold white]  |  Entorno: {env_label}\n"
        f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
        box=box.DOUBLE_EDGE,
        style="bold blue",
    ))


MIN_ASSET_DISPLAY_USD = 1.0


def render_balances(balances: list[dict], prices_usdt: dict[str, float]) -> float:
    """Render balances table. Hides assets < $1. Returns portfolio total in USDT."""
    rows = []
    portfolio_total = 0.0

    for b in balances:
        free = Decimal(b["free"])
        locked = Decimal(b["locked"])
        total_qty = float(free + locked)
        price = prices_usdt.get(b["asset"], 0.0)
        value_usdt = total_qty * price
        portfolio_total += value_usdt
        rows.append((b, free, locked, total_qty, value_usdt))

    visible = [(r[0], r[1], r[2], r[3], r[4]) for r in rows if r[4] >= MIN_ASSET_DISPLAY_USD]
    hidden_count = len(rows) - len(visible)

    table = Table(title="Balances", box=box.ROUNDED, style="cyan")
    table.add_column("Activo", style="bold white")
    table.add_column("Disponible", justify="right", style="green")
    table.add_column("En orden", justify="right", style="yellow")
    table.add_column("Total", justify="right", style="bold")
    table.add_column("Valor (USD)", justify="right", style="bold cyan")
    table.add_column("% Portfolio", justify="right", style="dim")

    for b, free, locked, total_qty, value_usdt in sorted(visible, key=lambda x: x[4], reverse=True):
        pct = (value_usdt / portfolio_total * 100) if portfolio_total > 0 else 0
        value_str = f"${value_usdt:,.2f}"
        table.add_row(
            b["asset"],
            f"{free:.6f}",
            f"{locked:.6f}",
            f"{total_qty:.6f}",
            value_str,
            f"{pct:.1f}%",
        )

    console.print(table)
    console.print(f"  [bold]Total portfolio:[/bold] [bold green]${portfolio_total:,.2f} USD[/bold green]")
    if hidden_count > 0:
        console.print(f"  [dim]{hidden_count} activos ocultos (valor < $1)[/dim]")
    return portfolio_total, len(visible)


def render_account_summary(
    portfolio_total: float,
    n_orders: int,
    n_assets: int,
) -> None:
    """Compact account summary line."""
    console.print(
        f"  [bold]Resumen:[/bold] Portfolio ${portfolio_total:,.2f} USDT  |  "
        f"{n_assets} activos  |  {n_orders} órdenes abiertas"
    )
    console.print()


def render_open_orders(orders: list[dict]) -> None:
    if not orders:
        console.print(Panel(
            "[dim]No hay órdenes abiertas[/dim]",
            title="Órdenes Abiertas",
            box=box.ROUNDED,
            style="dim"
        ))
        return

    table = Table(title="Órdenes Abiertas", box=box.ROUNDED, style="yellow")
    table.add_column("Par", style="dim")
    table.add_column("ID", style="dim")
    table.add_column("Tipo", style="bold")
    table.add_column("Lado", justify="center")
    table.add_column("Precio", justify="right")
    table.add_column("Cantidad", justify="right")
    table.add_column("Ejecutado", justify="right")
    table.add_column("Creada", style="dim")

    for o in orders:
        side_color = "green" if o["side"] == "BUY" else "red"
        table.add_row(
            o.get("symbol", ""),
            str(o["orderId"]),
            o["type"],
            f"[{side_color}]{o['side']}[/{side_color}]",
            f"${float(o['price']):,.2f}",
            f"{float(o['origQty']):.4f}",
            f"{float(o['executedQty']):.4f}",
            datetime.fromtimestamp(o["time"] / 1000).strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


def render_recent_trades(trades: list[dict]) -> None:
    if not trades:
        console.print(Panel(
            "[dim]No hay trades recientes[/dim]",
            title="Trades Recientes",
            box=box.ROUNDED,
            style="dim"
        ))
        return

    table = Table(title="Últimos Trades", box=box.ROUNDED, style="white")
    table.add_column("ID", style="dim")
    table.add_column("Lado", justify="center")
    table.add_column("Precio", justify="right")
    base = SYMBOL.replace("USDT", "")
    table.add_column(f"Cantidad ({base})", justify="right")
    table.add_column("Total (USDT)", justify="right", style="bold")
    table.add_column("Comisión", justify="right", style="dim")
    table.add_column("Fecha", style="dim")

    total_pnl = 0.0

    for t in trades:
        side_color = "green" if t["isBuyer"] else "red"
        side_label = "BUY" if t["isBuyer"] else "SELL"
        qty = float(t["qty"])
        price = float(t["price"])
        total = qty * price
        commission = float(t["commission"])

        if not t["isBuyer"]:
            total_pnl += total
        else:
            total_pnl -= total

        table.add_row(
            str(t["id"]),
            f"[{side_color}]{side_label}[/{side_color}]",
            f"${price:,.2f}",
            f"{qty:.6f}",
            f"${total:,.2f}",
            f"{commission:.6f} {t['commissionAsset']}",
            datetime.fromtimestamp(t["time"] / 1000).strftime("%Y-%m-%d %H:%M"),
        )

    pnl_color = "green" if total_pnl >= 0 else "red"
    console.print(table)
    console.print(
        f"  [dim]P&L neto últimos {len(trades)} trades:[/dim] "
        f"[bold {pnl_color}]${total_pnl:+,.2f} USDT[/bold {pnl_color}]"
    )


def main():
    environment = os.getenv("ENVIRONMENT", "production")
    render_header(environment)

    try:
        console.print("\n[dim]Conectando con Binance...[/dim]")
        client = get_client()

        with console.status("[bold cyan]Obteniendo datos de la cuenta...[/bold cyan]"):
            balances = fetch_account_balance(client)
            assets = [b["asset"] for b in balances]
            prices_usdt = fetch_prices_usdt(client, assets)
            open_orders = fetch_open_orders(client)
            recent_trades = fetch_recent_trades(client, limit=5)

        console.print()
        portfolio_total, n_visible_assets = render_balances(balances, prices_usdt)
        render_account_summary(portfolio_total, len(open_orders), n_visible_assets)
        render_open_orders(open_orders)
        console.print()
        render_recent_trades(recent_trades)
        console.print()
        console.print("[bold green]✓ Stats cargadas correctamente[/bold green]\n")

    except ValueError as e:
        console.print(f"\n[bold red]Error de configuración:[/bold red] {e}")
    except Exception as e:
        console.print(f"\n[bold red]Error al conectar con Binance:[/bold red] {e}")
        console.print("[dim]Verifica que tu API key sea válida y tenga permisos de lectura.[/dim]")


if __name__ == "__main__":
    main()
