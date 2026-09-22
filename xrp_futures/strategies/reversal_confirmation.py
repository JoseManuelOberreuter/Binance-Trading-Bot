"""
STAGE 4 permanence mechanism (Option 2, chosen over Option 1's Trend-Strength
filter): require a Donchian(40) breakout in the OPPOSITE direction of the
currently held signal to persist for `confirm_bars` CONSECUTIVE bars before
it is accepted as a real reversal, instead of flipping on a single breakout
bar as the frozen `persist_signal(donchian_breakout_signal(...))` does.

Donchian(40) itself, and the channel calculation, are untouched — this only
changes how a REVERSAL is confirmed, wrapping the same
`indicators.trend.donchian_channel`. confirm_bars=1 must reproduce the frozen
baseline exactly (single-bar breakout accepted immediately) — this is a
correctness invariant, tested in tests/test_reversal_confirmation.py.

Continuing in the SAME direction as the current signal, or an inside-channel
bar (`raw == 0`), always resets any partially-built opposite streak — a
reversal must be `confirm_bars` bars in a row, not `confirm_bars` bars total.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from xrp_futures.indicators.trend import donchian_channel
from xrp_futures.strategies.base import attach_risk_columns


@dataclass(frozen=True)
class ConfirmedReversalParams:
    timeframe: str = "12h"
    donchian_window: int = 40  # frozen — unchanged from the accepted config
    confirm_bars: int = 1      # 1 reproduces the frozen baseline exactly
    atr_window: int = 14
    vol_window: int = 30


def confirmed_donchian_signal(
    close: pd.Series, high: pd.Series, low: pd.Series, window: int, confirm_bars: int = 1
) -> pd.Series:
    upper, lower = donchian_channel(high, low, window)
    raw = pd.Series(0, index=close.index, dtype=int)
    raw[close > upper] = 1
    raw[close < lower] = -1
    raw_vals = raw.to_numpy()

    n = len(close)
    out = np.zeros(n, dtype=int)
    current = 0
    streak_dir = 0
    streak_len = 0
    for i in range(n):
        rs = int(raw_vals[i])
        is_reversal_candidate = rs != 0 and rs != current
        if is_reversal_candidate:
            if rs == streak_dir:
                streak_len += 1
            else:
                streak_dir, streak_len = rs, 1
            if streak_len >= confirm_bars:
                current = rs
                streak_dir, streak_len = 0, 0
        else:
            streak_dir, streak_len = 0, 0
        out[i] = current

    return pd.Series(out, index=close.index, dtype=int)


def generate(bars: pd.DataFrame, params: ConfirmedReversalParams) -> pd.DataFrame:
    out = attach_risk_columns(bars, params.timeframe, params.atr_window, params.vol_window)
    out["signal"] = confirmed_donchian_signal(
        out["close"], out["high"], out["low"], params.donchian_window, params.confirm_bars
    )
    return out
