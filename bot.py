"""
Grid trading bot — runs every 60 seconds.
Places and manages limit orders on Binance (Testnet or Production).
"""

import os
import sqlite3
import time
from pathlib import Path
from datetime import datetime

import pandas as pd
import ta
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from config import (
    CAPITAL_USDT,
    GRID_LEVELS,
    GRID_SPREAD_PCT,
    MAX_ORDER_SIZE,
    STOP_LOSS_PCT,
    SYMBOL,
    get_grid_bounds,
)
from exchange.client import get_client
from risk.manager import check_stop_loss
from strategy.grid import compute_grid_levels

load_dotenv()
console = Console()

LOOP_INTERVAL_SEC = 60
TRADES_DB = Path("logs/trades.db")


def get_adx(client) -> float:
    """Compute ADX (14) from 1h klines."""
    klines = client.get_klines(symbol=SYMBOL, interval="1h", limit=200)
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ["high", "low", "close"]:
        df[col] = df[col].astype(float)
    adx_ind = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
    return float(adx_ind.adx().iloc[-1])


def get_symbol_filters(client) -> tuple[float, float]:
    """Return (tick_size, step_size) for SYMBOL."""
    info = client.get_symbol_info(SYMBOL)
    price_filt = next((f for f in info["filters"] if f["filterType"] == "PRICE_FILTER"), {})
    lot_filt = next((f for f in info["filters"] if f["filterType"] == "LOT_SIZE"), {})
    tick_size = float(price_filt.get("tickSize", "0.01"))
    step_size = float(lot_filt.get("stepSize", "0.001"))
    return tick_size, step_size


def round_to_step(value: float, step: float) -> float:
    """Round value down to nearest step (for LOT_SIZE)."""
    if step <= 0:
        return value
    return int(value / step) * step


def round_price(price: float, tick_size: float) -> str:
    p = round_to_step(price, tick_size)
    decimals = len(str(tick_size).rstrip("0").split(".")[-1]) if "." in str(tick_size) else 8
    return f"{p:.{decimals}f}"


def round_qty(qty: float, step_size: float) -> str:
    q = round_to_step(qty, step_size)
    decimals = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 5
    return f"{q:.{decimals}f}"


def init_db() -> None:
    Path("logs").mkdir(exist_ok=True)
    conn = sqlite3.connect(TRADES_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            symbol TEXT,
            side TEXT,
            price REAL,
            qty REAL,
            quote_qty REAL,
            commission REAL,
            commission_asset TEXT,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_trade(conn: sqlite3.Connection, trade: dict) -> None:
    conn.execute(
        "INSERT INTO trades (order_id, symbol, side, price, qty, quote_qty, commission, commission_asset, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(trade.get("orderId", "")),
            trade.get("symbol", SYMBOL),
            trade.get("side", ""),
            float(trade.get("price", 0)),
            float(trade.get("qty", 0)),
            float(trade.get("quoteQty", 0)),
            float(trade.get("commission", 0)),
            trade.get("commissionAsset", ""),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()


def get_balance_usdt(client) -> float:
    """Get free USDT balance."""
    try:
        acc = client.get_account()
        for b in acc["balances"]:
            if b["asset"] == "USDT":
                return float(b["free"])
    except Exception:
        pass
    return 0.0


def get_balance_eth(client) -> float:
    """Get free ETH balance (base asset)."""
    base = SYMBOL.replace("USDT", "")
    try:
        acc = client.get_account()
        for b in acc["balances"]:
            if b["asset"] == base:
                return float(b["free"])
    except Exception:
        pass
    return 0.0


def get_portfolio_value_usdt(client) -> float:
    """Approximate portfolio value in USDT."""
    ticker = client.get_symbol_ticker(symbol=SYMBOL)
    price = float(ticker["price"])
    usdt = get_balance_usdt(client)
    eth = get_balance_eth(client)
    return usdt + eth * price


def run_bot_cycle(
    client,
    levels: list,
    tick_size: float,
    step_size: float,
    prev_orders: dict,
    conn: sqlite3.Connection,
) -> dict:
    """Run one bot cycle. Returns new prev_orders and status."""
    price_ticker = client.get_symbol_ticker(symbol=SYMBOL)
    current_price = float(price_ticker["price"])

    open_orders = {o["orderId"]: o for o in client.get_open_orders(symbol=SYMBOL)}

    # Map price -> level index
    def find_level(price: float, side: str) -> int | None:
        for i, lv in enumerate(levels):
            if side == "BUY" and abs(lv.buy_price - price) < 1:
                return i
            if side == "SELL" and abs(lv.sell_price - price) < 1:
                return i
        return None

    # Detect filled orders
    for oid, order in list(prev_orders.items()):
        if oid not in open_orders:
            try:
                filled = client.get_order(symbol=SYMBOL, orderId=oid)
                if filled["status"] == "FILLED":
                    save_trade(conn, filled)
                    level_idx = find_level(float(filled["price"]), filled["side"])
                    if level_idx is not None:
                        level = levels[level_idx]
                        usdt_per = min(CAPITAL_USDT / len(levels), MAX_ORDER_SIZE)
                        if filled["side"] == "BUY":
                            qty = float(filled["executedQty"])
                            sell_price = round_price(level.sell_price, tick_size)
                            new_order = client.create_order(
                                symbol=SYMBOL,
                                side="SELL",
                                type="LIMIT",
                                timeInForce="GTC",
                                quantity=round_qty(qty, step_size),
                                price=sell_price,
                            )
                            prev_orders[new_order["orderId"]] = new_order
                        else:
                            buy_qty = usdt_per / level.buy_price
                            buy_price = round_price(level.buy_price, tick_size)
                            new_order = client.create_order(
                                symbol=SYMBOL,
                                side="BUY",
                                type="LIMIT",
                                timeInForce="GTC",
                                quantity=round_qty(buy_qty, step_size),
                                price=buy_price,
                            )
                            prev_orders[new_order["orderId"]] = new_order
            except Exception as e:
                console.print(f"[yellow]Warning: {e}[/yellow]")
            if oid in prev_orders:
                del prev_orders[oid]

    # Add orders from open_orders that we don't track
    for oid, order in open_orders.items():
        if oid not in prev_orders:
            prev_orders[oid] = order

    # Initial fill: place BUY orders at levels below current price (if we have no orders)
    if not prev_orders:
        usdt_per = min(CAPITAL_USDT / len(levels), MAX_ORDER_SIZE)
        usdt_bal = get_balance_usdt(client)
        placed = 0
        for level in levels:
            if level.buy_price >= current_price:
                continue
            if usdt_bal < usdt_per * 1.1:
                break
            try:
                buy_qty = usdt_per / level.buy_price
                buy_price = round_price(level.buy_price, tick_size)
                order = client.create_order(
                    symbol=SYMBOL,
                    side="BUY",
                    type="LIMIT",
                    timeInForce="GTC",
                    quantity=round_qty(buy_qty, step_size),
                    price=buy_price,
                )
                prev_orders[order["orderId"]] = order
                placed += 1
            except Exception as e:
                console.print(f"[yellow]Skip level {level.index}: {e}[/yellow]")

    return prev_orders


def main() -> None:
    env = os.getenv("ENVIRONMENT", "production")
    if env != "testnet":
        console.print("[bold yellow]Advertencia: ENVIRONMENT no es 'testnet'. Para producción usa con cuidado.[/bold yellow]\n")

    init_db()

    try:
        client = get_client()
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        return

    current_price = float(client.get_symbol_ticker(symbol=SYMBOL)["price"])
    bounds = get_grid_bounds(current_price)
    levels = compute_grid_levels(current_price=current_price)
    tick_size, step_size = get_symbol_filters(client)
    prev_orders: dict = {}
    conn = sqlite3.connect(TRADES_DB)
    initial_value = get_portfolio_value_usdt(client)

    console.print(Panel(
        f"[bold]Grid Bot — {SYMBOL}[/bold]\n"
        f"Precio actual: ${current_price:,.2f}  |  Rango: ${bounds[0]:,.0f} - ${bounds[1]:,.0f} (±{GRID_SPREAD_PCT}%)\n"
        f"{GRID_LEVELS} niveles  |  Loop: {LOOP_INTERVAL_SEC}s",
        box=box.DOUBLE_EDGE,
        style="bold blue",
    ))
    console.print("[dim]Ctrl+C para detener[/dim]\n")

    try:
        while True:
            try:
                adx = get_adx(client)
                if adx > 35:
                    console.print(f"[yellow]ADX={adx:.1f} (>35) — Grid pausado (mercado tendencial)[/yellow]")
                    time.sleep(LOOP_INTERVAL_SEC)
                    continue

                prev_orders = run_bot_cycle(client, levels, tick_size, step_size, prev_orders, conn)

                current_value = get_portfolio_value_usdt(client)
                if check_stop_loss(initial_value, current_value):
                    console.print("[bold red]Stop loss activado — cancelando órdenes y deteniendo bot[/bold red]")
                    for oid in list(prev_orders.keys()):
                        try:
                            client.cancel_order(symbol=SYMBOL, orderId=oid)
                        except Exception:
                            pass
                    break

                # Status
                price = float(client.get_symbol_ticker(symbol=SYMBOL)["price"])
                table = Table(box=box.SIMPLE)
                table.add_column("Métrica", style="dim")
                table.add_column("Valor", justify="right")
                table.add_row("Precio", f"${price:,.2f}")
                table.add_row("ADX", f"{adx:.1f}")
                table.add_row("Órdenes abiertas", str(len(prev_orders)))
                table.add_row("Portfolio (USDT)", f"${current_value:,.2f}")
                table.add_row("Stop loss", f"{STOP_LOSS_PCT*100:.0f}%")
                console.print(table)
                console.print()

            except Exception as e:
                console.print(f"[red]Error en ciclo: {e}[/red]")

            time.sleep(LOOP_INTERVAL_SEC)
    except KeyboardInterrupt:
        console.print("\n[dim]Bot detenido por usuario[/dim]")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
