"""
BTC-based market regime filter (§16/§17): BTC's own trend/momentum, used as an
external filter for XRP signals even though BTC itself is never traded here.

Hypothesis to test (not assumed true): XRP trend/momentum works better when
conditioned on the broader crypto regime. compare_strategies.py should report
XRP-only vs XRP+BTC-regime side by side so this is an empirical finding, not a
baked-in assumption.
"""

from __future__ import annotations

import pandas as pd

from xrp_futures.indicators.momentum import raw_momentum
from xrp_futures.indicators.trend import ema, ema_slope

BULL, NEUTRAL, BEAR = 1, 0, -1


def compute_btc_regime(
    btc_bars: pd.DataFrame,
    timeframe: str,
    ema_span: int = 200,
    slope_lookback: int = 20,
    momentum_days: int = 30,
) -> pd.DataFrame:
    """
    Returns btc_bars with an added 'regime' column in {BULL, NEUTRAL, BEAR}:
      BULL: close > EMA(ema_span) AND EMA slope > 0 AND momentum > 0
      BEAR: close < EMA(ema_span) AND EMA slope < 0 AND momentum < 0
      NEUTRAL: mixed signals
    """
    out = btc_bars.copy()
    e = ema(out["close"], ema_span)
    slope = ema_slope(out["close"], ema_span, slope_lookback)
    mom = raw_momentum(out["close"], momentum_days, timeframe)

    bull = (out["close"] > e) & (slope > 0) & (mom > 0)
    bear = (out["close"] < e) & (slope < 0) & (mom < 0)

    regime = pd.Series(NEUTRAL, index=out.index, dtype=int)
    regime[bull] = BULL
    regime[bear] = BEAR
    out["regime"] = regime
    return out


def align_regime_to(bars: pd.DataFrame, regime_bars: pd.DataFrame) -> pd.Series:
    """
    Causally align BTC regime onto another symbol's bar index: each row gets the
    most recent BTC regime known AT OR BEFORE that row's open_time (merge_asof
    backward — never looks at a BTC regime computed after the XRP bar it's attached to).
    """
    left = bars[["open_time"]].reset_index(drop=True)
    right = regime_bars[["open_time", "regime"]].sort_values("open_time")
    merged = pd.merge_asof(left, right, on="open_time", direction="backward")
    return merged["regime"].fillna(NEUTRAL).astype(int)
