"""
Stop-loss mechanics: initial stop placement and trailing-stop updates.
All prices; all pure functions. `side` is +1 for LONG, -1 for SHORT throughout.
"""

from __future__ import annotations

import pandas as pd


def atr_stop_price(entry_price: float, atr_value: float, multiplier: float, side: int) -> float:
    """Stop = entry -+ multiplier * ATR (below entry for LONG, above for SHORT)."""
    return entry_price - side * multiplier * atr_value


def percentage_stop_price(entry_price: float, pct: float, side: int) -> float:
    """Stop = entry -+ pct% (below entry for LONG, above for SHORT)."""
    return entry_price * (1 - side * pct)


def chandelier_exit_series(
    high: pd.Series,
    low: pd.Series,
    atr_series: pd.Series,
    window: int,
    multiplier: float,
    side: int,
) -> pd.Series:
    """
    Chandelier Exit: for LONG, highest high over `window` bars minus multiplier*ATR;
    for SHORT, lowest low over `window` bars plus multiplier*ATR. This is the raw
    level at each bar — combine with trailing_stop_update() to get a monotonic stop
    that only ever moves in the position's favor.
    """
    if side == 1:
        extreme = high.rolling(window).max()
        return extreme - multiplier * atr_series
    return low.rolling(window).min() + multiplier * atr_series


def trailing_stop_update(current_stop: float, candidate_stop: float, side: int) -> float:
    """
    A trailing stop only ever tightens toward the market, never loosens:
    for LONG it can only move up, for SHORT only down.
    """
    if side == 1:
        return max(current_stop, candidate_stop)
    return min(current_stop, candidate_stop)


def stop_distance_pct(entry_price: float, stop_price: float) -> float:
    """Absolute distance between entry and stop, as a fraction of entry price."""
    if entry_price <= 0:
        return 0.0
    return abs(entry_price - stop_price) / entry_price
