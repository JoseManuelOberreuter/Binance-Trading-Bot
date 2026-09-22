"""
Momentum indicators over configurable lookback windows (expressed in DAYS, converted
internally to bar counts via the timeframe's bars-per-day so the same lookback list
— 7/14/30/60/90 days — means the same thing regardless of the strategy's timeframe).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from xrp_futures.indicators.volatility import log_returns, realized_volatility

BARS_PER_DAY = {"1h": 24, "4h": 6, "6h": 4, "12h": 2, "1d": 1}


def lookback_bars(days: int, timeframe: str) -> int:
    bpd = BARS_PER_DAY.get(timeframe)
    if bpd is None:
        raise ValueError(f"Unknown timeframe {timeframe!r}")
    return max(1, round(days * bpd))


def raw_momentum(close: pd.Series, days: int, timeframe: str) -> pd.Series:
    """Simple % change over the lookback: close / close.shift(n) - 1."""
    n = lookback_bars(days, timeframe)
    return close.pct_change(n)


def log_momentum(close: pd.Series, days: int, timeframe: str) -> pd.Series:
    """Cumulative log return over the lookback: log(close / close.shift(n))."""
    n = lookback_bars(days, timeframe)
    return np.log(close / close.shift(n))


def vol_adjusted_momentum(
    close: pd.Series,
    days: int,
    timeframe: str,
    vol_window: int = 30,
) -> pd.Series:
    """
    Risk-adjusted momentum: cumulative log return over the lookback, divided by
    realized volatility (raw, non-annualized) measured over `vol_window` bars.
    Higher = stronger trend per unit of risk taken to capture it.
    """
    mom = log_momentum(close, days, timeframe)
    vol = realized_volatility(close, vol_window, timeframe, annualize=False)
    return mom / vol.replace(0.0, np.nan)


def momentum_sign(mom: pd.Series, deadband: float = 0.0) -> pd.Series:
    """+1/-1/0 sign of a momentum series, with an optional deadband around zero."""
    sig = pd.Series(0, index=mom.index, dtype=int)
    sig[mom > deadband] = 1
    sig[mom < -deadband] = -1
    return sig
