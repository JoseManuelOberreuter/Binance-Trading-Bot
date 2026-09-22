"""
XRP's OWN regime state (distinct from strategies/regime.py's BTC-external filter):
is XRP itself currently trending or ranging, and is realized volatility currently
expanding or compressed. Both are entry filters for the trend-following/breakout
philosophy this round of testing evaluates — the hypothesis being that breakouts
taken only when the asset is ALREADY trending (high ADX) and/or volatility is
already expanding are more likely to be real moves worth riding, not noise.

Both are computed causally (only using data up to and including each bar).
"""

from __future__ import annotations

import pandas as pd

from xrp_futures.indicators.trend import adx as adx_fn
from xrp_futures.indicators.volatility import realized_volatility

TRENDING, RANGING = 1, 0
HIGH_VOL, LOW_VOL = 1, 0


def compute_adx_trend_regime(bars: pd.DataFrame, adx_window: int = 14, adx_threshold: float = 25.0) -> pd.Series:
    """TRENDING (1) when ADX > threshold, RANGING (0) otherwise."""
    adx_val = adx_fn(bars["high"], bars["low"], bars["close"], window=adx_window)["adx"]
    regime = pd.Series(RANGING, index=bars.index, dtype=int)
    regime[adx_val > adx_threshold] = TRENDING
    return regime


def apply_regime_filter(signal: pd.Series, regime: pd.Series, allow_value: int) -> pd.Series:
    """
    Zero out `signal` wherever `regime` != allow_value. Same simple masking semantics
    as the BTC regime gate in strategy_c_trend_momentum.py: this blocks NEW entries
    taken while the regime is unfavorable, and also forces a flat exit (via the
    engine's normal signal-change path) if the regime turns unfavorable while a
    position from a favorable period is still open — not a subtler "only gate new
    entries, let existing ones ride" filter, which would need extra engine-level state.
    """
    out = signal.copy()
    out[regime.reindex(signal.index).values != allow_value] = 0
    return out.astype(int)


def compute_volatility_regime(
    bars: pd.DataFrame,
    timeframe: str,
    vol_window: int = 30,
    median_window: int = 90,
) -> pd.Series:
    """
    HIGH_VOL (1) when current realized volatility is above its own trailing rolling
    median (over `median_window` bars), LOW_VOL (0) otherwise. Self-referential and
    causal — no look-ahead, no reliance on the full-sample distribution.
    """
    vol = realized_volatility(bars["close"], window=vol_window, timeframe=timeframe, annualize=True)
    rolling_median = vol.rolling(median_window, min_periods=median_window // 2).median()
    regime = pd.Series(LOW_VOL, index=bars.index, dtype=int)
    regime[vol > rolling_median] = HIGH_VOL
    return regime
