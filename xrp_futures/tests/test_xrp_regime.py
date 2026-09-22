import numpy as np
import pandas as pd

from xrp_futures.strategies.xrp_regime import (
    HIGH_VOL, LOW_VOL, RANGING, TRENDING,
    apply_regime_filter, compute_adx_trend_regime, compute_volatility_regime,
)


def _trending_bars(n=200):
    close = pd.Series(np.linspace(100, 300, n))  # strong sustained uptrend -> high ADX
    return pd.DataFrame({"high": close + 1, "low": close - 1, "close": close})


def _choppy_bars(n=200, seed=1):
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + rng.normal(0, 0.3, n).cumsum() * 0.05)  # tiny random walk, no trend
    close = close.clip(lower=90, upper=110)
    return pd.DataFrame({"high": close + 0.5, "low": close - 0.5, "close": close})


def test_adx_trend_regime_flags_sustained_trend():
    bars = _trending_bars()
    regime = compute_adx_trend_regime(bars, adx_window=14, adx_threshold=25)
    assert regime.iloc[-20:].eq(TRENDING).all()


def test_adx_trend_regime_flags_choppy_market_as_ranging():
    bars = _choppy_bars()
    regime = compute_adx_trend_regime(bars, adx_window=14, adx_threshold=25)
    # not asserting 100% RANGING (randomness can occasionally trend briefly), but the
    # large majority of bars in a directionless random walk should be RANGING
    assert regime.iloc[30:].eq(RANGING).mean() > 0.7


def test_volatility_regime_flags_expansion_right_after_a_vol_shock():
    # A rolling-median-relative classifier detects REGIME CHANGES, not sustained levels:
    # once enough bars pass inside the new regime, the trailing median "catches up" to it
    # and readings revert to ~50/50. So this checks the transition itself — median_window
    # spans the whole calm segment (never biased by the shock) and we look right after the
    # shock starts, before the rolling window has absorbed much of the new volatile data.
    calm = np.full(120, 100.0) * (1 + np.random.default_rng(0).normal(0, 0.0005, 120)).cumprod()
    rng = np.random.default_rng(2)
    volatile = calm[-1] * np.cumprod(1 + rng.normal(0, 0.03, 40))
    close = pd.Series(np.concatenate([calm, volatile]))
    bars = pd.DataFrame({"close": close})

    regime = compute_volatility_regime(bars, timeframe="1h", vol_window=5, median_window=110)
    late_calm_high_vol_rate = regime.iloc[90:119].mean()
    post_shock_high_vol_rate = regime.iloc[121:140].mean()
    assert post_shock_high_vol_rate > late_calm_high_vol_rate


def test_volatility_regime_values_are_binary():
    bars = _choppy_bars()
    regime = compute_volatility_regime(bars, timeframe="1h", vol_window=10, median_window=30)
    assert set(regime.dropna().unique()).issubset({HIGH_VOL, LOW_VOL})


def test_apply_regime_filter_masks_disallowed_bars():
    signal = pd.Series([1, 1, -1, -1, 0])
    regime = pd.Series([TRENDING, RANGING, TRENDING, RANGING, TRENDING])
    filtered = apply_regime_filter(signal, regime, allow_value=TRENDING)
    assert filtered.tolist() == [1, 0, -1, 0, 0]


def test_apply_regime_filter_preserves_allowed_signal():
    signal = pd.Series([1, -1, 1])
    regime = pd.Series([TRENDING, TRENDING, TRENDING])
    filtered = apply_regime_filter(signal, regime, allow_value=TRENDING)
    assert filtered.tolist() == [1, -1, 1]
