"""Trend indicators: moving averages, slope, breakout, ADX. Pure functions over Series."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False, min_periods=span).mean()


def ema_slope(close: pd.Series, span: int, lookback: int) -> pd.Series:
    """% change of the EMA itself over `lookback` bars — a smoothed trend-direction gauge."""
    e = ema(close, span)
    return e.pct_change(lookback)


def donchian_channel(high: pd.Series, low: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    """Rolling (upper, lower) channel over the *prior* `window` bars (excludes current bar)."""
    upper = high.shift(1).rolling(window).max()
    lower = low.shift(1).rolling(window).min()
    return upper, lower


def donchian_breakout_signal(close: pd.Series, high: pd.Series, low: pd.Series, window: int) -> pd.Series:
    """+1 when close breaks above the prior Donchian upper band, -1 below lower band, 0 otherwise."""
    upper, lower = donchian_channel(high, low, window)
    sig = pd.Series(0, index=close.index, dtype=int)
    sig[close > upper] = 1
    sig[close < lower] = -1
    return sig


def high_low_breakout_signal(close: pd.Series, window: int) -> pd.Series:
    """+1 when close is the highest close in `window` bars (incl. current), -1 if lowest, 0 otherwise."""
    roll_max = close.rolling(window).max()
    roll_min = close.rolling(window).min()
    sig = pd.Series(0, index=close.index, dtype=int)
    sig[close >= roll_max] = 1
    sig[close <= roll_min] = -1
    return sig


def adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.DataFrame:
    """
    Wilder ADX/+DI/-DI. Returns a DataFrame with columns ['plus_di', 'minus_di', 'adx'].
    Implemented directly (no external TA dependency) so behavior is fully inspectable.
    """
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_w = tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean() / atr_w
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean() / atr_w

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx_val = dx.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()

    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": adx_val})
