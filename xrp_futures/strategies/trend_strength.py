"""
Trend Strength (§ Stage 2 of the "capture big moves" research phase): a simple,
explainable 0-4 score meant to distinguish a normal pullback inside a still-strong
trend from a trend that has genuinely ended.

DESIGN RULE (per explicit instruction): every threshold below is a conventional,
round-number choice made BEFORE looking at how it scores the specific historical
swings this module is used to diagnose — none of them were tuned by checking which
values "work best" on the +145%/+533%/+101% XRP moves. If a later stage revisits
these, that must be a distinct, explicit step — not a quiet edit of this file.

Score = count of 4 independent, roughly equally-weighted confirming conditions, all
evaluated relative to a trend DIRECTION derived from the EMA(50) slope's sign:
  1. ADX(14) > 25                          — Wilder's own conventional "trending" cutoff
  2. |EMA(50) slope over 20 bars| > 2%      — the trend's own moving average is
                                              actually advancing, not flat
  3. bars since the last new Donchian(20)
     extreme IN THE TREND'S DIRECTION       — a structural "still making progress"
     is <= 10 bars                            check: recent new highs (uptrend) or
                                              lows (downtrend), not stale
  4. 30-day momentum in the trend's
     direction > 10%                        — the move has real magnitude, not noise

0 = no condition met (no trend) ... 4 = all four met (exceptional trend). This is
deliberately a simple vote, not a weighted/fitted model — easy to audit, easy to
explain, and the whole point of this stage is to test whether even something this
blunt already carries real signal before building anything more elaborate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from xrp_futures.indicators import momentum as momentum_mod
from xrp_futures.indicators import trend as trend_mod

NO_TREND, WEAK, NORMAL, STRONG, EXCEPTIONAL = 0, 1, 2, 3, 4


@dataclass(frozen=True)
class TrendStrengthParams:
    ema_span: int = 50
    ema_slope_lookback: int = 20
    ema_slope_threshold: float = 0.02
    adx_window: int = 14
    adx_threshold: float = 25.0
    donchian_window: int = 20
    recent_extreme_bars: int = 10
    momentum_days: int = 30
    momentum_threshold: float = 0.10
    timeframe: str = "12h"


def _bars_since_true(flags: pd.Series) -> pd.Series:
    """For each position, how many bars since `flags` was last True (0 = this bar).
    A position before the first True gets a large sentinel (never happened yet)."""
    out = np.empty(len(flags), dtype=np.int64)
    last_true_idx = -1
    values = flags.to_numpy()
    for i in range(len(values)):
        if values[i]:
            last_true_idx = i
            out[i] = 0
        else:
            out[i] = (i - last_true_idx) if last_true_idx >= 0 else 10_000
    return pd.Series(out, index=flags.index)


def compute_trend_strength(bars: pd.DataFrame, params: TrendStrengthParams = TrendStrengthParams()) -> pd.DataFrame:
    """
    Causal (no look-ahead): every column at row i uses only bars up to and including i.
    Returns `bars` plus: trend_direction (+1/-1/0, from EMA slope sign), the four raw
    component values (adx, ema_slope, bars_since_extreme, momentum), and trend_strength
    (0-4, the count of confirming conditions in trend_direction's favor).
    """
    out = bars.copy()
    close, high, low = out["close"], out["high"], out["low"]

    ema_slope = trend_mod.ema_slope(close, params.ema_span, params.ema_slope_lookback)
    adx_df = trend_mod.adx(high, low, close, params.adx_window)
    mom = momentum_mod.raw_momentum(close, params.momentum_days, params.timeframe)

    direction = pd.Series(0, index=out.index, dtype=int)
    direction[ema_slope > 0] = 1
    direction[ema_slope < 0] = -1

    roll_high = high.rolling(params.donchian_window).max()
    roll_low = low.rolling(params.donchian_window).min()
    is_new_high = high >= roll_high
    is_new_low = low <= roll_low
    bars_since_high = _bars_since_true(is_new_high)
    bars_since_low = _bars_since_true(is_new_low)
    bars_since_extreme = pd.Series(
        np.where(direction.to_numpy() == 1, bars_since_high.to_numpy(), bars_since_low.to_numpy()),
        index=out.index,
    )

    cond_adx = adx_df["adx"] > params.adx_threshold
    cond_slope = ema_slope.abs() > params.ema_slope_threshold
    cond_structure = bars_since_extreme <= params.recent_extreme_bars
    cond_momentum = (direction * mom) > params.momentum_threshold

    score = cond_adx.astype(int) + cond_slope.astype(int) + cond_structure.astype(int) + cond_momentum.astype(int)
    score[direction == 0] = 0  # no discernible direction -> no trend, regardless of component noise

    out["trend_direction"] = direction
    out["adx"] = adx_df["adx"]
    out["ema_slope"] = ema_slope
    out["bars_since_extreme"] = bars_since_extreme
    out["momentum_30d"] = mom
    out["cond_adx"] = cond_adx
    out["cond_slope"] = cond_slope
    out["cond_structure"] = cond_structure
    out["cond_momentum"] = cond_momentum
    out["trend_strength"] = score
    return out
