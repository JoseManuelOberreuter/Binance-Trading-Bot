"""
Download and cache historical XRPUSDT (traded symbol) and BTCUSDT (regime filter)
USDⓈ-M futures data: 1h klines + funding rate settlements, from Binance's public
data dump. No API key required.

Usage:
  python -m xrp_futures.scripts.download_data
  python -m xrp_futures.scripts.download_data --start 2020-01-01 --end 2026-09-01
  python -m xrp_futures.scripts.download_data --symbols XRPUSDT BTCUSDT --force-refresh
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from xrp_futures.data import loader

DEFAULT_START = date(2020, 1, 1)
DEFAULT_SYMBOLS = ["XRPUSDT", "BTCUSDT"]


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--start", type=_parse_date, default=DEFAULT_START)
    parser.add_argument("--end", type=_parse_date, default=date.today())
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()

    for symbol in args.symbols:
        print(f"\n=== {symbol} ===")
        klines = loader.load_klines_1h(symbol, args.start, args.end, force_refresh=args.force_refresh)
        print(f"  klines (1h): {len(klines):,} bars "
              f"[{klines['open_time'].min()} .. {klines['open_time'].max()}]" if not klines.empty else "  klines (1h): EMPTY")

        funding = loader.load_funding(symbol, args.start, args.end, force_refresh=args.force_refresh)
        print(f"  funding: {len(funding):,} settlements "
              f"[{funding['calc_time'].min()} .. {funding['calc_time'].max()}]" if not funding.empty else "  funding: EMPTY")

    print(f"\nCached under {loader.CACHE_DIR}")


if __name__ == "__main__":
    main()
