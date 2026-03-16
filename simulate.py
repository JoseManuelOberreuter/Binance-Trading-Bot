"""
Grid trading simulation over historical data.
Runs paper trading to estimate P&L before using real capital.

Usage:
  python simulate.py              # Single run with .env config
  python simulate.py --interval 15m   # Use 15-minute candles
  python simulate.py --compare        # Compare multiple configurations
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

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
    SYMBOL,
    get_grid_bounds,
)
from exchange.client import get_client
from strategy.grid import compute_grid_levels, run_grid_simulation

load_dotenv()
console = Console()

# Candles per day by interval
CANDLES_PER_DAY = {"1h": 24, "15m": 96}
MAX_KLINES_PER_REQUEST = 1000


def fetch_klines(client, symbol: str, interval: str, target_days: int = 30) -> list:
    """Fetch enough historical klines to cover target_days. Handles API limit of 1000."""
    total_needed = target_days * CANDLES_PER_DAY.get(interval, 24)
    all_klines: list = []
    end_time = None

    while len(all_klines) < total_needed:
        limit = min(MAX_KLINES_PER_REQUEST, total_needed - len(all_klines))
        kwargs = {"symbol": symbol, "interval": interval, "limit": limit}
        if end_time is not None:
            kwargs["endTime"] = end_time

        batch = client.get_klines(**kwargs)
        if not batch:
            break

        all_klines = batch + all_klines
        end_time = batch[0][0] - 1
        if len(batch) < limit:
            break

    return all_klines[:total_needed]


def run_single_simulation(
    client,
    interval: str = "1h",
    target_days: int = 30,
    custom_params: dict | None = None,
) -> tuple[dict, list]:
    """Run one simulation. Returns (report_dict, klines)."""
    params = custom_params or {}
    levels_n = params.get("grid_levels", GRID_LEVELS)
    capital = params.get("capital_usdt", CAPITAL_USDT)
    max_order = params.get("max_order_size", MAX_ORDER_SIZE)

    klines = fetch_klines(client, SYMBOL, interval, target_days)
    if len(klines) < 10:
        return {"error": "Insufficient data"}, klines

    # Use dynamic range from first candle close, or fixed bounds from custom_params
    if "grid_lower" in params and "grid_upper" in params:
        lower, upper = params["grid_lower"], params["grid_upper"]
    else:
        ref_price = float(klines[0][4])
        lower, upper = get_grid_bounds(ref_price)

    levels = compute_grid_levels(lower=lower, upper=upper, n_levels=levels_n)
    result = run_grid_simulation(
        klines,
        capital=capital,
        levels=levels,
        max_order_size=max_order,
    )

    first_ts = klines[0][0]
    last_ts = klines[-1][0]
    days = (last_ts - first_ts) / (1000 * 60 * 60 * 24)
    win_rate = (result.winning_trades / result.total_trades * 100) if result.total_trades else 0

    return {
        "params": {
            "grid_lower": lower,
            "grid_upper": upper,
            "grid_levels": levels_n,
            "capital_usdt": capital,
            "interval": interval,
            "klines_count": len(klines),
            "days_simulated": days,
        },
        "result": {
            "total_trades": result.total_trades,
            "winning_trades": result.winning_trades,
            "win_rate_pct": win_rate,
            "pnl_usdt": result.pnl_usdt,
            "pnl_pct": result.pnl_pct,
        },
        "trades": result.trades,
    }, klines


def main() -> None:
    parser = argparse.ArgumentParser(description="Grid trading simulation")
    parser.add_argument(
        "--interval",
        "-i",
        choices=["1h", "15m"],
        default="1h",
        help="Candle interval (default: 1h)",
    )
    parser.add_argument(
        "--compare",
        "-c",
        action="store_true",
        help="Compare multiple configurations",
    )
    parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=30,
        help="Days to simulate (default: 30)",
    )
    args = parser.parse_args()

    try:
        console.print("[dim]Conectando con Binance...[/dim]")
        client = get_client()
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        return

    if args.compare:
        run_compare_mode(client, args.interval, args.days)
    else:
        run_single_mode(client, args.interval, args.days)


def run_single_mode(client, interval: str, target_days: int) -> None:
    """Single simulation with .env config (dynamic grid ±GRID_SPREAD_PCT%)."""
    console.print(Panel(
        f"[bold]Grid Simulation — {SYMBOL}[/bold]\n"
        f"Rango dinámico: ±{GRID_SPREAD_PCT}% del precio  |  "
        f"{GRID_LEVELS} niveles  |  Capital: ${CAPITAL_USDT:,.0f}  |  Velas: {interval}",
        box=box.DOUBLE_EDGE,
        style="bold blue",
    ))
    console.print()

    with console.status("[bold cyan]Obteniendo datos históricos..."):
        report, klines = run_single_simulation(client, interval, target_days)

    if "error" in report:
        console.print(f"[red]{report['error']}[/red]")
        return

    params = report["params"]
    res = report["result"]
    days = params["days_simulated"]
    step = (params["grid_upper"] - params["grid_lower"]) / params["grid_levels"]
    step_pct = (step / params["grid_lower"] * 100) if params["grid_lower"] > 0 else 0

    table = Table(
        title=f"Resultados — {SYMBOL} ({days:.0f} días, velas {interval})",
        box=box.ROUNDED,
        style="green",
    )
    table.add_column("Métrica", style="bold white")
    table.add_column("Valor", justify="right")

    pnl_color = "green" if res["pnl_usdt"] >= 0 else "red"
    table.add_row("Trades ejecutados", str(res["total_trades"]))
    table.add_row("Trades ganadores", f"{res['winning_trades']} ({res['win_rate_pct']:.1f}%)")
    table.add_row("P&L simulado", f"[{pnl_color}]${res['pnl_usdt']:+,.2f} USDT[/{pnl_color}]")
    table.add_row("Retorno %", f"[{pnl_color}]{res['pnl_pct']:+.2f}%[/{pnl_color}]")
    table.add_row("Espaciado", f"${step:,.2f} ({step_pct:.2f}%)")
    table.add_row("Período", f"{days:.1f} días")
    table.add_row("[bold]Total ganado (USD)[/bold]", f"[bold {pnl_color}]${res['pnl_usdt']:+,.2f} USDT[/bold {pnl_color}]")
    console.print(table)
    console.print()

    if report.get("trades"):
        trades_table = Table(title="Últimos 10 Trades", box=box.ROUNDED, style="dim")
        trades_table.add_column("#", style="dim")
        trades_table.add_column("Compra", justify="right")
        trades_table.add_column("Venta", justify="right")
        trades_table.add_column("Qty", justify="right")
        trades_table.add_column("P&L", justify="right")
        for i, t in enumerate(report["trades"][-10:], 1):
            c = "green" if t.profit_usdt >= 0 else "red"
            trades_table.add_row(
                str(i),
                f"${t.buy_price:,.2f}",
                f"${t.sell_price:,.2f}",
                f"{t.qty:.6f}",
                f"[{c}]${t.profit_usdt:+,.2f}[/{c}]",
            )
        console.print(trades_table)
        console.print()

    save_report(report, "simulation.json")
    console.print("[bold green]✓ Simulación guardada en logs/simulation.json[/bold green]")

    if res["pnl_pct"] < 10 and res["pnl_pct"] >= 0:
        console.print(
            "\n[dim]💡 Para buscar ~10% mensual: ejecuta [bold]python simulate.py --compare[/bold] "
            "y prueba las configs 'Objetivo 10%'. O usa [bold]--interval 15m[/bold] para más trades.[/dim]"
        )
    console.print()


def run_compare_mode(client, interval: str, target_days: int) -> None:
    """Compare multiple grid configurations."""
    console.print(Panel(
        f"[bold]Comparación de Configuraciones — {SYMBOL}[/bold]\n"
        f"Velas: {interval}  |  Período: {target_days} días  |  Capital: ${CAPITAL_USDT:,.0f}",
        box=box.DOUBLE_EDGE,
        style="bold blue",
    ))
    console.print()

    # Configs: Base uses dynamic ±GRID_SPREAD_PCT; others use fixed range for comparison
    configs = [
        {"name": "Base (dinámico ±15%)", "grid_levels": GRID_LEVELS},
        {"name": "Rango fijo $2050-$2450", "grid_lower": 2050, "grid_upper": 2450, "grid_levels": 25},
        {"name": "Rango $2100-$2400 (40 niv)", "grid_lower": 2100, "grid_upper": 2400, "grid_levels": 40},
        {"name": "Rango $2000-$2500 (50 niv)", "grid_lower": 2000, "grid_upper": 2500, "grid_levels": 50},
        {"name": "Rango estrecho $2150-$2350", "grid_lower": 2150, "grid_upper": 2350, "grid_levels": 50},
        {"name": "Rango amplio $2000-$2600", "grid_lower": 2000, "grid_upper": 2600, "grid_levels": 15},
    ]

    results = []
    for cfg in configs:
        name = cfg["name"]
        params = {k: v for k, v in cfg.items() if k != "name"}
        with console.status(f"Simulando: {name}..."):
            report, _ = run_single_simulation(
                client,
                interval,
                target_days,
                custom_params={**params, "capital_usdt": CAPITAL_USDT, "max_order_size": MAX_ORDER_SIZE},
            )
        cfg_with_name = {"name": name, **params}
        if "error" not in report:
            results.append((cfg_with_name, report))
        else:
            results.append((cfg_with_name, {"result": {"total_trades": 0, "pnl_usdt": 0, "pnl_pct": 0, "win_rate_pct": 0}}))

    # Comparison table
    table = Table(
        title="Comparación de Resultados",
        box=box.ROUNDED,
        style="cyan",
    )
    table.add_column("Configuración", style="bold white")
    table.add_column("Trades", justify="right")
    table.add_column("Win Rate", justify="right")
    table.add_column("P&L USDT", justify="right")
    table.add_column("Retorno %", justify="right")

    best_idx = 0
    best_pct = -999.0
    for i, (cfg, report) in enumerate(results):
        res = report.get("result", {})
        pnl = res.get("pnl_usdt", 0)
        pct = res.get("pnl_pct", 0)
        if pct > best_pct:
            best_pct = pct
            best_idx = i

        pnl_color = "green" if pnl >= 0 else "red"
        pct_color = "green" if pct >= 0 else "red"
        table.add_row(
            cfg["name"],
            str(res.get("total_trades", 0)),
            f"{res.get('win_rate_pct', 0):.1f}%",
            f"[{pnl_color}]${pnl:+,.2f}[/{pnl_color}]",
            f"[{pct_color}]{pct:+.2f}%[/{pct_color}]",
        )

    console.print(table)
    console.print()

    best_name = results[best_idx][0]["name"]
    console.print(f"  [bold]Mejor configuración:[/bold] [green]{best_name}[/green] ({best_pct:+.2f}% retorno)")
    console.print()

    # Save comparison
    compare_report = {
        "timestamp": datetime.now().isoformat(),
        "symbol": SYMBOL,
        "interval": interval,
        "days": target_days,
        "capital": CAPITAL_USDT,
        "configs": [
            {
                "name": cfg["name"],
                **cfg,
                "result": r.get("result", {}),
            }
            for cfg, r in results
        ],
    }
    save_report(compare_report, "simulation_compare.json")
    console.print("[bold green]✓ Comparación guardada en logs/simulation_compare.json[/bold green]\n")


def save_report(report: dict, filename: str) -> None:
    """Save report to logs/ directory."""
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    # Make trades JSON-serializable
    def serialize(obj):
        if hasattr(obj, "__dict__") and "buy_price" in getattr(obj, "__dict__", {}):
            return {"buy_price": obj.buy_price, "sell_price": obj.sell_price, "qty": obj.qty, "profit_usdt": obj.profit_usdt}
        return obj

    out = {}
    for k, v in report.items():
        if k == "trades" and v:
            out[k] = [{"buy_price": t.buy_price, "sell_price": t.sell_price, "qty": t.qty, "profit_usdt": t.profit_usdt} for t in v]
        elif isinstance(v, dict):
            out[k] = v
        else:
            out[k] = v

    with open(logs_dir / filename, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
