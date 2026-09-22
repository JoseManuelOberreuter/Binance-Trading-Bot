import numpy as np
import pandas as pd
import pytest

from xrp_futures.strategies.strategy_a_trend import StrategyAParams, generate


def _synthetic_bars(n=400, start_price=100.0, drift=0.001, noise=0.001, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="6h", tz="UTC")
    close = start_price * np.cumprod(1 + drift + rng.normal(0, noise, n))
    high = close * (1 + np.abs(rng.normal(0, noise, n)))
    low = close * (1 - np.abs(rng.normal(0, noise, n)))
    open_ = np.roll(close, 1)
    open_[0] = start_price
    return pd.DataFrame({
        "open_time": idx,
        "close_time": idx + pd.Timedelta(hours=6) - pd.Timedelta(milliseconds=1),
        "open": open_, "high": high, "low": low, "close": close,
    })


def test_generate_outputs_required_engine_columns():
    bars = _synthetic_bars()
    params = StrategyAParams(timeframe="6h", ema_fast=10, ema_slow=30, atr_window=14, vol_window=20)
    out = generate(bars, params)
    for col in ("signal", "atr", "realized_vol_ann"):
        assert col in out.columns
    assert set(out["signal"].dropna().unique()).issubset({-1, 0, 1})


def test_ema_method_goes_long_in_sustained_uptrend():
    bars = _synthetic_bars(n=300, drift=0.003, noise=0.0005, seed=1)
    params = StrategyAParams(timeframe="6h", ema_fast=10, ema_slow=50, method="ema")
    out = generate(bars, params)
    assert out["signal"].iloc[-1] == 1


def test_ema_method_goes_short_in_sustained_downtrend():
    bars = _synthetic_bars(n=300, drift=-0.003, noise=0.0005, seed=2)
    params = StrategyAParams(timeframe="6h", ema_fast=10, ema_slow=50, method="ema")
    out = generate(bars, params)
    assert out["signal"].iloc[-1] == -1


def test_donchian_method_persists_direction_between_breakouts():
    bars = _synthetic_bars(n=300, drift=0.002, noise=0.0005, seed=3)
    params = StrategyAParams(timeframe="6h", method="donchian", donchian_window=20)
    out = generate(bars, params)
    # persistence: once nonzero, signal should not go back to 0 just because no new breakout fired
    nonzero_idx = out.index[out["signal"] != 0]
    if len(nonzero_idx) > 1:
        first, last = nonzero_idx[0], nonzero_idx[-1]
        span = out.loc[first:last, "signal"]
        assert (span != 0).all()


def test_unknown_method_raises():
    bars = _synthetic_bars(n=50)
    params = StrategyAParams(method="bogus")
    with pytest.raises(ValueError):
        generate(bars, params)
