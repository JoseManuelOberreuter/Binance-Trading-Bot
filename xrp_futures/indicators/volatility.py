"""Realized volatility and ATR estimators. Pure functions over OHLCV DataFrames."""

from __future__ import annotations

import numpy as np
import pandas as pd

BARS_PER_YEAR = {
    "1h": 24 * 365,
    "4h": 6 * 365,
    "6h": 4 * 365,
    "12h": 2 * 365,
    "1d": 365,
}


def log_returns(close: pd.Series) -> pd.Series:
    return np.log(close / close.shift(1))


def realized_volatility(
    close: pd.Series,
    window: int,
    timeframe: str,
    annualize: bool = True,
) -> pd.Series:
    """
    Rolling realized volatility from close-to-close log returns, std-dev based.
    Annualized using the bar count implied by `timeframe` unless annualize=False
    (in which case it's the raw per-bar volatility).
    """
    rets = log_returns(close)
    vol = rets.rolling(window).std()
    if annualize:
        bars_per_year = BARS_PER_YEAR.get(timeframe)
        if bars_per_year is None:
            raise ValueError(f"Unknown timeframe {timeframe!r} for annualization")
        vol = vol * np.sqrt(bars_per_year)
    return vol


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder-style ATR (exponential moving average of true range, alpha=1/window)."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
