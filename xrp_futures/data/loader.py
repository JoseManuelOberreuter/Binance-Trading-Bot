"""
Local parquet cache on top of binance_vision downloads, plus OHLCV resampling.

Base granularity is always 1h klines (the finest interval we bulk-download); every
other timeframe used by a strategy (4h/6h/12h/1d) is derived from the cached 1h
series via resample_ohlcv() rather than downloaded separately.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from xrp_futures.data import binance_vision as bv

CACHE_DIR = Path(__file__).parent / "cache"
BASE_KLINE_INTERVAL = "1h"

TIMEFRAME_TO_PANDAS_RULE = {
    "1h": "1h",
    "4h": "4h",
    "6h": "6h",
    "12h": "12h",
    "1d": "1D",
}


def _klines_cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol.upper()}_{BASE_KLINE_INTERVAL}_klines.parquet"


def _funding_cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol.upper()}_funding.parquet"


def _load_incremental(
    path: Path,
    time_col: str,
    start: date,
    end: date,
    force_refresh: bool,
    fetch_fn,
    log_label: str,
) -> pd.DataFrame:
    """
    Shared incremental-cache logic for klines and funding: backfill the front if the
    cache starts later than `start`, extend the tail if it ends before `end`, and
    otherwise serve entirely from disk. A backfill that finds nothing (e.g. `start`
    predates the symbol's futures listing) costs one small targeted fetch, not a full
    re-download — critical because "predates listing" is permanently true for a fixed
    start date, so a naive "cache doesn't start early enough -> redownload everything"
    check would refetch the ENTIRE history on every single call.
    """
    cached = pd.read_parquet(path) if (path.exists() and not force_refresh) else None
    changed = False

    if cached is None or cached.empty:
        cached, stats = fetch_fn(start, end)
        if stats.missing:
            print(f"[loader] {log_label}: {len(stats.missing)}/{stats.requested} periods unavailable "
                  f"(e.g. before listing or not yet published): {stats.missing[:3]}...")
        changed = True
    else:
        cached_min = cached[time_col].min().date()
        cached_max = cached[time_col].max().date()
        pieces = [cached]

        if start < cached_min:
            backfill, _ = fetch_fn(start, cached_min)
            if not backfill.empty:
                pieces.append(backfill)
                changed = True

        if end > cached_max:
            tail, _ = fetch_fn(cached_max, end)  # re-fetch last cached day in case it was partial
            if not tail.empty:
                pieces.append(tail)
                changed = True

        if changed:
            cached = (
                pd.concat(pieces, ignore_index=True)
                .drop_duplicates(subset=time_col)
                .sort_values(time_col)
                .reset_index(drop=True)
            )

    if changed:
        cached.to_parquet(path, index=False)

    return cached[(cached[time_col].dt.date >= start) & (cached[time_col].dt.date <= end)].reset_index(drop=True)


def load_klines_1h(
    symbol: str,
    start: date,
    end: date | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Load cached 1h klines for symbol, downloading/backfilling/extending as needed."""
    end = end or date.today()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _load_incremental(
        path=_klines_cache_path(symbol),
        time_col="open_time",
        start=start,
        end=end,
        force_refresh=force_refresh,
        fetch_fn=lambda s, e: bv.download_klines(symbol, BASE_KLINE_INTERVAL, s, e),
        log_label=f"{symbol} klines",
    )


def load_funding(
    symbol: str,
    start: date,
    end: date | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Load cached funding-rate settlements for symbol, downloading/backfilling/extending as needed."""
    end = end or date.today()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _load_incremental(
        path=_funding_cache_path(symbol),
        time_col="calc_time",
        start=start,
        end=end,
        force_refresh=force_refresh,
        fetch_fn=lambda s, e: bv.download_funding_rate(symbol, s, e),
        log_label=f"{symbol} funding",
    )


def resample_ohlcv(df_1h: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample 1h klines up to a coarser timeframe ('1h', '4h', '6h', '12h', '1d')."""
    if timeframe not in TIMEFRAME_TO_PANDAS_RULE:
        raise ValueError(f"Unsupported timeframe {timeframe!r}; use one of {sorted(TIMEFRAME_TO_PANDAS_RULE)}")
    if timeframe == "1h":
        return df_1h.reset_index(drop=True)

    rule = TIMEFRAME_TO_PANDAS_RULE[timeframe]
    indexed = df_1h.set_index("open_time")
    agg = indexed.resample(rule, label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "quote_volume": "sum",
        "count": "sum",
        "taker_buy_volume": "sum",
        "taker_buy_quote_volume": "sum",
    })
    agg = agg.dropna(subset=["open", "high", "low", "close"]).reset_index()
    agg["close_time"] = agg["open_time"] + pd.Timedelta(rule) - pd.Timedelta(milliseconds=1)
    return agg
