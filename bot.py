"""
Grid trading bot — runs every 60 seconds.
Places and manages limit orders on Binance (Testnet or Production).
"""

import os
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import ta
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich import box

from config import (
    ADX_PAUSE_THRESHOLD,
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


def get_adx_and_direction(client) -> tuple[float, float, float]:
    """
    Compute ADX and directional indicators from 1h klines.
    Returns (adx, plus_di, minus_di).
    Bearish when minus_di > plus_di. Bullish when plus_di > minus_di.
    """
    klines = client.get_klines(symbol=SYMBOL, interval="1h", limit=200)
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ["high", "low", "close"]:
        df[col] = df[col].astype(float)
    adx_ind = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
    adx = float(adx_ind.adx().iloc[-1])
    plus_di = float(adx_ind.adx_pos().iloc[-1])
    minus_di = float(adx_ind.adx_neg().iloc[-1])
    return adx, plus_di, minus_di


def get_symbol_filters(client) -> tuple[float, float, float]:
    """Return (tick_size, step_size, min_notional) for SYMBOL."""
    info = client.get_symbol_info(SYMBOL)
    price_filt = next((f for f in info["filters"] if f["filterType"] == "PRICE_FILTER"), {})
    lot_filt = next((f for f in info["filters"] if f["filterType"] == "LOT_SIZE"), {})
    notional_filt = next(
        (f for f in info["filters"] if f["filterType"] in ("NOTIONAL", "MIN_NOTIONAL")),
        {},
    )
    tick_size = float(price_filt.get("tickSize", "0.01"))
    step_size = float(lot_filt.get("stepSize", "0.001"))
    min_notional = float(notional_filt.get("minNotional", "5.0"))
    return tick_size, step_size, min_notional


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


def round_up_to_step(value: float, step: float) -> float:
    """Round value up to nearest step (for meeting min notional)."""
    if step <= 0:
        return value
    return (int(value / step) + 1) * step if value > 0 else step


def ensure_min_notional(
    price: float, qty: float, step_size: float, min_notional: float
) -> float:
    """
    Ensure quantity satisfies Binance's min notional (price * qty >= min_notional).
    Returns adjusted quantity rounded up to step_size if needed.
    """
    notional = price * qty
    if notional >= min_notional:
        return qty
    min_qty = min_notional / price
    return round_up_to_step(min_qty, step_size)


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
            datetime.now(timezone.utc).isoformat(),
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
    min_notional: float,
    prev_orders: dict,
    conn: sqlite3.Connection,
) -> tuple[dict, list]:
    """Run one bot cycle. Returns (prev_orders, events). Events are dicts with type, ..."""
    events = []
    price_ticker = client.get_symbol_ticker(symbol=SYMBOL)
    current_price = float(price_ticker["price"])
    open_orders = {o["orderId"]: o for o in client.get_open_orders(symbol=SYMBOL)}

    # Find nearest level (tolerant to grid recomputation each cycle)
    grid_step = (levels[1].buy_price - levels[0].buy_price) if len(levels) >= 2 else 15.0
    max_distance = grid_step * 1.5  # Reject if fill is too far from any level

    def find_level(price: float, side: str) -> int | None:
        best_idx, best_dist = None, float("inf")
        for i, lv in enumerate(levels):
            d = abs(lv.buy_price - price) if side == "BUY" else abs(lv.sell_price - price)
            if d < best_dist:
                best_dist, best_idx = d, i
        return best_idx if best_idx is not None and best_dist < max_distance else None

    for oid, order in list(prev_orders.items()):
        if oid not in open_orders:
            try:
                filled = client.get_order(symbol=SYMBOL, orderId=oid)
                if filled["status"] == "FILLED":
                    save_trade(conn, filled)
                    side = filled["side"]
                    price = float(filled["price"])
                    qty = float(filled["executedQty"])
                    events.append({"type": "order_filled", "side": side, "price": price, "qty": qty})

                    level_idx = find_level(price, side)
                    if level_idx is not None:
                        level = levels[level_idx]
                        usdt_per = min(CAPITAL_USDT / len(levels), MAX_ORDER_SIZE)
                        if side == "BUY":
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
                            events.append({"type": "order_placed", "side": "SELL", "price": level.sell_price, "qty": qty})
                        else:
                            profit = qty * (level.sell_price - level.buy_price)
                            events.append({
                                "type": "trade_profit",
                                "buy": level.buy_price,
                                "sell": level.sell_price,
                                "qty": qty,
                                "profit": profit,
                            })
                            buy_qty_raw = usdt_per / level.buy_price
                            buy_qty = ensure_min_notional(
                                level.buy_price, buy_qty_raw, step_size, min_notional
                            )
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
                            events.append({"type": "order_placed", "side": "BUY", "price": level.buy_price, "qty": buy_qty})
            except Exception as e:
                events.append({"type": "error", "msg": str(e)})
            if oid in prev_orders:
                del prev_orders[oid]

    for oid, order in open_orders.items():
        if oid not in prev_orders:
            prev_orders[oid] = order

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
                buy_qty_raw = usdt_per / level.buy_price
                buy_qty = ensure_min_notional(
                    level.buy_price, buy_qty_raw, step_size, min_notional
                )
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
                events.append({"type": "error", "msg": str(e)})
        if placed > 0:
            events.append({"type": "orders_initial", "count": placed, "side": "BUY"})

    return prev_orders, events


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
    tick_size, step_size, min_notional = get_symbol_filters(client)
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
                adx, plus_di, minus_di = get_adx_and_direction(client)
                is_bearish = minus_di > plus_di
                should_pause = adx > ADX_PAUSE_THRESHOLD and is_bearish

                if should_pause:
                    ts = datetime.now().strftime("%H:%M:%S")
                    console.print(
                        f"[dim]{ts}[/dim] [yellow]⏸ Grid pausado — ADX={adx:.0f}, tendencia bajista (-DI:{minus_di:.0f} > +DI:{plus_di:.0f})[/yellow]"
                    )
                    time.sleep(LOOP_INTERVAL_SEC)
                    continue

                price = float(client.get_symbol_ticker(symbol=SYMBOL)["price"])
                levels = compute_grid_levels(current_price=price)
                prev_orders, cycle_events = run_bot_cycle(
                    client, levels, tick_size, step_size, min_notional, prev_orders, conn
                )
                ts = datetime.now().strftime("%H:%M:%S")

                for ev in cycle_events:
                    t = ev.get("type", "")
                    if t == "orders_initial":
                        console.print(f"[dim]{ts}[/dim] [cyan]📤 {ev['count']} órdenes BUY colocadas[/cyan]")
                    elif t == "order_filled":
                        side = ev["side"]
                        c = "green" if side == "BUY" else "red"
                        console.print(f"[dim]{ts}[/dim] [{c}]📥 {side} ejecutada @ ${ev['price']:,.2f} x {ev['qty']:.6f}[/{c}]")
                    elif t == "order_placed":
                        side = ev["side"]
                        c = "green" if side == "BUY" else "red"
                        console.print(f"[dim]{ts}[/dim] [{c}]📤 Orden {side} colocada @ ${ev['price']:,.2f}[/{c}]")
                    elif t == "trade_profit":
                        profit = ev["profit"]
                        pc = "green" if profit >= 0 else "red"
                        console.print(
                            f"[dim]{ts}[/dim] [bold {pc}]✅ Trade: ${profit:+,.2f} "
                            f"(compra ${ev['buy']:,.0f} → venta ${ev['sell']:,.0f})[/bold {pc}]"
                        )
                    elif t == "error":
                        console.print(f"[dim]{ts}[/dim] [yellow]⚠ {ev['msg']}[/yellow]")

                current_value = get_portfolio_value_usdt(client)
                trend = "Alcista" if not is_bearish else "Bajista"
                console.print(
                    f"[dim]{ts}[/dim] ${price:,.2f} | ADX {adx:.0f} {trend} | "
                    f"{len(prev_orders)} órdenes | ${current_value:,.2f} | SL {STOP_LOSS_PCT*100:.0f}%"
                )
                if check_stop_loss(initial_value, current_value):
                    console.print(
                        f"[dim]{ts}[/dim] [bold red]🛑 Stop loss activado — Portfolio ${current_value:,.2f} "
                        f"< {STOP_LOSS_PCT*100:.0f}% desde inicio. Cancelando órdenes.[/bold red]"
                    )
                    for oid in list(prev_orders.keys()):
                        try:
                            client.cancel_order(symbol=SYMBOL, orderId=oid)
                        except Exception:
                            pass
                    break

            except Exception as e:
                console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] [red]❌ Error: {e}[/red]")

            time.sleep(LOOP_INTERVAL_SEC)
    except KeyboardInterrupt:
        console.print("\n[dim]Bot detenido por usuario[/dim]")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
