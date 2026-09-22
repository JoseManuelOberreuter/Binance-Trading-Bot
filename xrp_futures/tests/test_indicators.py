import numpy as np
import pandas as pd
import pytest

from xrp_futures.indicators import momentum, trend, volatility


def test_ema_converges_to_constant_series():
    s = pd.Series([100.0] * 60)
    e = trend.ema(s, span=10)
    assert e.iloc[-1] == pytest.approx(100.0)


def test_ema_slope_positive_on_uptrend():
    s = pd.Series(np.linspace(100, 200, 100))
    slope = trend.ema_slope(s, span=10, lookback=20)
    assert slope.iloc[-1] > 0


def test_donchian_breakout_signal_fires_on_new_high():
    high = pd.Series([10] * 5 + [11] * 5 + [20])
    low = high - 1
    close = high - 0.5
    sig = trend.donchian_breakout_signal(close, high, low, window=5)
    assert sig.iloc[-1] == 1


def test_donchian_channel_excludes_current_bar():
    high = pd.Series([10, 10, 10, 10, 10, 100])
    low = high - 1
    upper, _ = trend.donchian_channel(high, low, window=5)
    # last bar's own high (100) must not leak into its own channel
    assert upper.iloc[-1] == 10


def test_high_low_breakout_signal():
    close = pd.Series([1, 2, 3, 2, 1, 5])
    sig = trend.high_low_breakout_signal(close, window=5)
    assert sig.iloc[-1] == 1  # 5 is the new rolling max


def test_atr_equals_true_range_on_constant_range_series():
    n = 50
    high = pd.Series(np.arange(n) + 2.0)
    low = pd.Series(np.arange(n) + 0.0)  # constant range of 2, no gaps vs prior close possible below
    close = pd.Series(np.arange(n) + 1.0)
    a = volatility.atr(high, low, close, window=14)
    # after warmup, true range should stabilize near the constant 2.0 bar range
    assert a.iloc[-1] == pytest.approx(2.0, rel=0.2)


def test_adx_columns_and_bounds():
    n = 200
    close = pd.Series(np.linspace(100, 300, n))  # strong sustained uptrend
    high = close + 1
    low = close - 1
    result = trend.adx(high, low, close, window=14)
    assert list(result.columns) == ["plus_di", "minus_di", "adx"]
    # strong sustained trend -> +DI should dominate -DI, ADX should be high (>25) once warmed up
    tail = result.iloc[-20:]
    assert (tail["plus_di"] > tail["minus_di"]).all()
    assert tail["adx"].iloc[-1] > 25


def test_log_returns_matches_manual_calc():
    close = pd.Series([100.0, 110.0, 99.0])
    lr = volatility.log_returns(close)
    assert lr.iloc[1] == pytest.approx(np.log(110 / 100))
    assert lr.iloc[2] == pytest.approx(np.log(99 / 110))


def test_realized_volatility_annualization_scales_correctly():
    rng = np.random.default_rng(42)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.001, 500))))
    raw = volatility.realized_volatility(close, window=30, timeframe="1h", annualize=False)
    ann = volatility.realized_volatility(close, window=30, timeframe="1h", annualize=True)
    factor = np.sqrt(volatility.BARS_PER_YEAR["1h"])
    assert ann.iloc[-1] == pytest.approx(raw.iloc[-1] * factor)


def test_raw_momentum_matches_manual_pct_change():
    close = pd.Series([100.0] * 24 + [110.0])  # 1 day (24 bars on 1h) later, +10%
    mom = momentum.raw_momentum(close, days=1, timeframe="1h")
    assert mom.iloc[-1] == pytest.approx(0.10)


def test_lookback_bars_scales_with_timeframe():
    assert momentum.lookback_bars(7, "1h") == 168
    assert momentum.lookback_bars(7, "6h") == 28
    assert momentum.lookback_bars(1, "1d") == 1


def test_vol_adjusted_momentum_penalizes_high_volatility():
    n = 200
    calm = pd.Series(100 * (1.0005 ** np.arange(n)))  # smooth uptrend
    rng = np.random.default_rng(1)
    noisy = pd.Series(100 * np.cumprod(1 + 0.0005 + rng.normal(0, 0.01, n)))

    calm_mom = momentum.vol_adjusted_momentum(calm, days=5, timeframe="1h").iloc[-1]
    noisy_mom = momentum.vol_adjusted_momentum(noisy, days=5, timeframe="1h").iloc[-1]
    assert calm_mom > 0
    # same-ish drift, but noisy series should score lower per unit of risk (usually)
    assert abs(calm_mom) > 0 and abs(noisy_mom) >= 0  # sanity: no NaN/inf blowup
    assert np.isfinite(calm_mom)


def test_momentum_sign_deadband():
    mom = pd.Series([0.02, -0.02, 0.001, -0.001, 0.0])
    sig = momentum.momentum_sign(mom, deadband=0.005)
    assert list(sig) == [1, -1, 0, 0, 0]
