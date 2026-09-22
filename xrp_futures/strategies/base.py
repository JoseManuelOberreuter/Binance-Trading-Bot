"""
Shared contract every strategy module follows: given a bars DataFrame (OHLCV at some
timeframe) and a params dataclass, return bars augmented with the columns
backtest/engine.py requires: signal (int, causal), atr, realized_vol_ann.

Keeping this as a plain function contract (not a class hierarchy) makes each
strategy a pure, independently testable transform — no shared mutable state, no
inheritance to trace through when something looks wrong.
"""

from __future__ import annotations

import pandas as pd

from xrp_futures.indicators.volatility import atr as atr_fn
from xrp_futures.indicators.volatility import realized_volatility


def attach_risk_columns(bars: pd.DataFrame, timeframe: str, atr_window: int, vol_window: int) -> pd.DataFrame:
    """Attach the 'atr' and 'realized_vol_ann' columns every strategy output needs."""
    out = bars.copy()
    out["atr"] = atr_fn(out["high"], out["low"], out["close"], window=atr_window)
    out["realized_vol_ann"] = realized_volatility(out["close"], window=vol_window, timeframe=timeframe)
    return out


def persist_signal(momentary: pd.Series) -> pd.Series:
    """
    Turn a momentary event signal (e.g. a breakout that only fires on the bar it
    happens) into a persistent position signal: hold the last nonzero direction
    until the opposite direction fires. Leading bars with no signal yet -> 0 (flat).
    """
    replaced = momentary.replace(0, pd.NA)
    held = replaced.ffill()
    return held.fillna(0).astype(int)
