import numpy as np
import pandas as pd
import pytest

from xrp_futures.strategies.trend_strength import (
    EXCEPTIONAL, NO_TREND, TrendStrengthParams, _bars_since_true, compute_trend_strength,
)


def test_bars_since_true_basic():
    flags = pd.Series([False, True, False, False, True, False])
    out = _bars_since_true(flags)
    assert out.tolist() == [10_000, 0, 1, 2, 0, 1]


def test_bars_since_true_never_true_returns_sentinel():
    flags = pd.Series([False, False, False])
    out = _bars_since_true(flags)
    assert out.tolist() == [10_000, 10_000, 10_000]


def _bars(closes, highs=None, lows=None, n_pad=0):
    n = len(closes)
    idx = pd.date_range("2020-01-01", periods=n, freq="12h", tz="UTC")
    closes = pd.Series(closes, dtype=float)
    highs = pd.Series(highs, dtype=float) if highs is not None else closes + 0.5
    lows = pd.Series(lows, dtype=float) if lows is not None else closes - 0.5
    return pd.DataFrame({"open_time": idx, "open": closes, "high": highs, "low": lows, "close": closes})


def test_flat_market_scores_no_trend():
    n = 150
    rng = np.random.default_rng(0)
    closes = 100 + rng.normal(0, 0.05, n).cumsum() * 0.02  # tiny directionless wobble
    bars = _bars(closes)
    result = compute_trend_strength(bars)
    tail = result.iloc[-30:]
    assert (tail["trend_strength"] <= 1).mean() > 0.7  # mostly flat/weak, not "strong"


def test_strong_sustained_uptrend_scores_high():
    n = 200
    closes = 100 * (1.01 ** np.arange(n))  # a clean, strong, sustained 1%/bar uptrend
    bars = _bars(closes)
    params = TrendStrengthParams(timeframe="12h", momentum_days=5)  # shorter lookback fits this synthetic series
    result = compute_trend_strength(bars, params)
    tail = result.iloc[-20:]
    assert (tail["trend_direction"] == 1).all()
    assert tail["trend_strength"].mean() >= 3  # should register as strong-to-exceptional


def test_trend_strength_is_bounded_0_to_4():
    n = 200
    rng = np.random.default_rng(1)
    closes = 100 * np.cumprod(1 + rng.normal(0.001, 0.02, n))
    bars = _bars(closes)
    result = compute_trend_strength(bars)
    valid = result["trend_strength"].dropna()
    assert valid.between(0, 4).all()


def test_no_direction_forces_no_trend_score():
    # construct a bar where ema_slope is exactly the boundary and gets set to 0
    n = 100
    closes = np.full(n, 100.0)  # perfectly flat -> ema_slope == 0 -> direction == 0
    bars = _bars(closes)
    result = compute_trend_strength(bars)
    tail = result.iloc[-10:]
    assert (tail["trend_direction"] == 0).all()
    assert (tail["trend_strength"] == NO_TREND).all()


def test_component_columns_present():
    n = 120
    closes = 100 * (1.005 ** np.arange(n))
    bars = _bars(closes)
    result = compute_trend_strength(bars)
    for col in ["trend_direction", "adx", "ema_slope", "bars_since_extreme", "momentum_30d",
                "cond_adx", "cond_slope", "cond_structure", "cond_momentum", "trend_strength"]:
        assert col in result.columns


def test_downtrend_direction_and_momentum_sign():
    n = 200
    closes = 100 * (0.985 ** np.arange(n))  # sustained downtrend, ~14%/10-bar decay -> clears the 10% momentum bar
    bars = _bars(closes)
    params = TrendStrengthParams(timeframe="12h", momentum_days=5)
    result = compute_trend_strength(bars, params)
    tail = result.iloc[-20:]
    assert (tail["trend_direction"] == -1).all()
    # momentum itself is negative (raw price momentum), but cond_momentum checks
    # direction*momentum > threshold, which should still be favorable (True) in a
    # clean sustained downtrend
    assert (tail["momentum_30d"] < 0).all()
    assert tail["cond_momentum"].mean() > 0.5
