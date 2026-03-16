import os
from datetime import datetime
from decimal import Decimal

import pandas as pd
import ta
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


def fetch_eth_price(client) -> dict:
    ticker = client.get_ticker(symbol=SYMBOL)
    return ticker


def fetch_open_orders(client) -> list[dict]:
    return client.get_open_orders(symbol=SYMBOL)


def fetch_recent_trades(client, limit: int = 10) -> list[dict]:
    try:
        return client.get_my_trades(symbol=SYMBOL, limit=limit)
    except Exception:
        return []


def fetch_klines_summary(client) -> dict:
    klines = client.get_klines(symbol=SYMBOL, interval="1h", limit=24)
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    return {
        "high_24h": max(highs),
        "low_24h": min(lows),
        "avg_price_24h": sum(closes) / len(closes),
        "volume_24h": sum(volumes),
        "last_close": closes[-1],
        "price_change_pct": ((closes[-1] - closes[0]) / closes[0]) * 100,
    }


def fetch_indicators(client) -> dict:
    klines = client.get_klines(symbol=SYMBOL, interval="1h", limit=200)
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()

    bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"] = bb.bollinger_mavg()
    df["bb_pct"] = bb.bollinger_pband()

    adx_indicator = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
    df["adx"] = adx_indicator.adx()

    last = df.iloc[-1]
    return {
        "rsi": last["rsi"],
        "bb_upper": last["bb_upper"],
        "bb_lower": last["bb_lower"],
        "bb_mid": last["bb_mid"],
        "bb_pct": last["bb_pct"],
        "adx": last["adx"],
        "close": last["close"],
    }


def render_indicators(indicators: dict) -> None:
    rsi = indicators["rsi"]
    adx = indicators["adx"]
    close = indicators["close"]
    bb_upper = indicators["bb_upper"]
    bb_lower = indicators["bb_lower"]
    bb_mid = indicators["bb_mid"]
    bb_pct = indicators["bb_pct"]

    if rsi >= 70:
        rsi_color = "red"
        rsi_label = "Sobrecomprado"
    elif rsi <= 30:
        rsi_color = "green"
        rsi_label = "Sobrevendido"
    else:
        rsi_color = "white"
        rsi_label = "Neutral"

    if adx < 25:
        adx_color = "green"
        adx_label = "Lateral ✅ Grid activo"
    elif adx > 35:
        adx_color = "red"
        adx_label = "Tendencial ⏸ Grid pausado"
    else:
        adx_color = "yellow"
        adx_label = "Transición"

    bb_inside = bb_lower <= close <= bb_upper
    bb_status_color = "green" if bb_inside else "red"
    bb_status_label = "Dentro de las bandas ✅" if bb_inside else "Fuera de las bandas ⚠"

    table = Table(title=f"Indicadores Técnicos — {SYMBOL} (1h, últimas 200 velas)", box=box.ROUNDED, style="blue")
    table.add_column("Indicador", style="bold white")
    table.add_column("Valor", justify="right")
    table.add_column("Estado", justify="left")

    table.add_row(
        "RSI (14)",
        f"[{rsi_color}]{rsi:.2f}[/{rsi_color}]",
        f"[{rsi_color}]{rsi_label}[/{rsi_color}]",
    )
    table.add_row(
        "ADX (14)",
        f"[{adx_color}]{adx:.2f}[/{adx_color}]",
        f"[{adx_color}]{adx_label}[/{adx_color}]",
    )
    table.add_row(
        "BB Superior",
        f"[dim]${bb_upper:,.2f}[/dim]",
        "",
    )
    table.add_row(
        "BB Media",
        f"[dim]${bb_mid:,.2f}[/dim]",
        "",
    )
    table.add_row(
        "BB Inferior",
        f"[dim]${bb_lower:,.2f}[/dim]",
        "",
    )
    table.add_row(
        "Precio vs BB",
        f"[{bb_status_color}]{bb_pct * 100:.1f}% del rango[/{bb_status_color}]",
        f"[{bb_status_color}]{bb_status_label}[/{bb_status_color}]",
    )

    console.print(table)


def render_header(environment: str) -> None:
    env_color = "green" if environment == "production" else "yellow"
    env_label = f"[bold {env_color}]{environment.upper()}[/bold {env_color}]"
    console.print(Panel(
        f"[bold white]Binance Account Stats[/bold white]  |  Entorno: {env_label}\n"
        f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
        box=box.DOUBLE_EDGE,
        style="bold blue",
    ))


def render_balances(balances: list[dict], prices_usdt: dict[str, float]) -> None:
    table = Table(title="Balances de la Cuenta", box=box.ROUNDED, style="cyan")
    table.add_column("Activo", style="bold white")
    table.add_column("Disponible", justify="right", style="green")
    table.add_column("En orden", justify="right", style="yellow")
    table.add_column("Total", justify="right", style="bold")
    table.add_column("Valor (USDT)", justify="right", style="bold cyan")

    portfolio_total = 0.0

    for b in balances:
        free = Decimal(b["free"])
        locked = Decimal(b["locked"])
        total_qty = float(free + locked)
        price = prices_usdt.get(b["asset"], 0.0)
        value_usdt = total_qty * price
        portfolio_total += value_usdt

        value_str = f"${value_usdt:,.2f}" if value_usdt >= 0.01 else "$0.00"
        table.add_row(
            b["asset"],
            f"{free:.6f}",
            f"{locked:.6f}",
            f"{total_qty:.6f}",
            value_str,
        )

    console.print(table)
    console.print(
        f"  [bold]Total portfolio:[/bold] [bold green]${portfolio_total:,.2f} USDT[/bold green]"
    )


def render_eth_ticker(ticker: dict, summary: dict) -> None:
    change_pct = float(ticker["priceChangePercent"])
    change_color = "green" if change_pct >= 0 else "red"
    change_arrow = "▲" if change_pct >= 0 else "▼"

    table = Table(title=f"Mercado {SYMBOL}", box=box.ROUNDED, style="magenta")
    table.add_column("Métrica", style="bold white")
    table.add_column("Valor", justify="right")

    table.add_row("Precio actual", f"[bold white]${float(ticker['lastPrice']):,.2f}[/bold white]")
    table.add_row(
        "Cambio 24h",
        f"[{change_color}]{change_arrow} {change_pct:+.2f}%[/{change_color}]"
    )
    table.add_row("Máximo 24h", f"[green]${summary['high_24h']:,.2f}[/green]")
    table.add_row("Mínimo 24h", f"[red]${summary['low_24h']:,.2f}[/red]")
    table.add_row("Precio promedio 24h", f"${summary['avg_price_24h']:,.2f}")
    table.add_row("Volumen 24h (ETH)", f"{summary['volume_24h']:,.2f}")
    table.add_row("Cambio precio últimas 24 velas 1h", f"[{change_color}]{summary['price_change_pct']:+.2f}%[/{change_color}]")
    table.add_row("Bid", f"${float(ticker['bidPrice']):,.2f}")
    table.add_row("Ask", f"${float(ticker['askPrice']):,.2f}")
    table.add_row("Spread", f"${float(ticker['askPrice']) - float(ticker['bidPrice']):.4f}")

    console.print(table)


def render_open_orders(orders: list[dict]) -> None:
    if not orders:
        console.print(Panel(
            f"[dim]No hay órdenes abiertas para {SYMBOL}[/dim]",
            title="Órdenes Abiertas",
            box=box.ROUNDED,
            style="dim"
        ))
        return

    table = Table(title=f"Órdenes Abiertas — {SYMBOL}", box=box.ROUNDED, style="yellow")
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
            f"[dim]No hay trades recientes para {SYMBOL}[/dim]",
            title="Trades Recientes",
            box=box.ROUNDED,
            style="dim"
        ))
        return

    table = Table(title=f"Últimos Trades — {SYMBOL}", box=box.ROUNDED, style="white")
    table.add_column("ID", style="dim")
    table.add_column("Lado", justify="center")
    table.add_column("Precio", justify="right")
    table.add_column("Cantidad (ETH)", justify="right")
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
            ticker = fetch_eth_price(client)
            summary = fetch_klines_summary(client)
            indicators = fetch_indicators(client)
            open_orders = fetch_open_orders(client)
            recent_trades = fetch_recent_trades(client, limit=10)

        console.print()
        render_balances(balances, prices_usdt)
        console.print()
        render_eth_ticker(ticker, summary)
        console.print()
        render_indicators(indicators)
        console.print()
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
