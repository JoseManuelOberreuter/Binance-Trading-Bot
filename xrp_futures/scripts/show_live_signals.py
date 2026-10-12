"""
Phase 2 (docs/execution_plan.md): READ-ONLY view of what the bot would decide right
now for each symbol, from real (production, public, keyless) 12h klines — and a
real-data parity check against the backtest's cached history.

Signals come from production market data even when orders will go to testnet:
testnet order books are thin and their candles can diverge from the real market.

Usage:
  python -m xrp_futures.scripts.show_live_signals
  python -m xrp_futures.scripts.show_live_signals --compare-cache
"""

from __future__ import annotations

import argparse

import pandas as pd
from binance.client import Client
from rich.console import Console
from rich.table import Table

from xrp_futures.config.live import DEFAULT
from xrp_futures.data import loader
from xrp_futures.execution.live_signal import compute_live_signal, fetch_closed_bars
from xrp_futures.strategies import strategy_a_trend as strat_a

console = Console()
SIDE = {1: "[green]LONG[/green]", -1: "[red]SHORT[/red]", 0: "FLAT"}


def compare_with_cache(symbol: str, live_bars: pd.DataFrame) -> str:
    """Signal agreement on the overlap between live klines and the cached (1h->12h) backtest data."""
    try:
        klines, _ = loader.read_cached(symbol)
    except FileNotFoundError:
        return "no cache"
    cached = strat_a.generate(loader.resample_ohlcv(klines, DEFAULT.strategy.timeframe), DEFAULT.strategy)
    live = strat_a.generate(live_bars, DEFAULT.strategy)
    warmup = DEFAULT.strategy.donchian_window * 3   # ignore bars before the live window's first breakout settles
    merged = live.iloc[warmup:].merge(cached, on="open_time", suffixes=("_live", "_bt"))
    # The cache's last day can be a partially-downloaded candle; compare complete ones only.
    merged = merged[merged["open_time"] < cached["open_time"].iloc[-1]]
    if merged.empty:
        return "no overlap"
    same = (merged["signal_live"] == merged["signal_bt"]).sum()
    return f"{same}/{len(merged)} equal"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-cache", action="store_true", help="check signals against cached backtest data")
    args = parser.parse_args()

    client = Client()   # production public endpoints, no keys
    t = Table(title=f"Live signals — last closed {DEFAULT.strategy.timeframe} candle (production market data)")
    cols = ["Symbol", "Candle (UTC)", "Close", "Target", "ATR %", "Trail if LONG", "Trail if SHORT"]
    if args.compare_cache:
        cols.append("vs backtest")
    for c in cols:
        t.add_column(c, justify="right")

    for symbol in DEFAULT.universe:
        bars = fetch_closed_bars(client, symbol, DEFAULT)
        s = compute_live_signal(symbol, bars, DEFAULT)
        row = [symbol, s.bar_open_time.strftime("%Y-%m-%d %H:%M"), f"{s.close:,.4f}", SIDE[s.signal],
               f"{s.atr / s.close:.1%}", f"{s.chandelier_long:,.4f}", f"{s.chandelier_short:,.4f}"]
        if args.compare_cache:
            row.append(compare_with_cache(symbol, bars))
        t.add_row(*row)
    console.print(t)
    console.print("Read-only: no orders were placed.")


if __name__ == "__main__":
    main()
