import numpy as np
import pandas as pd
import pytest

from xrp_futures.strategies.strategy_a_trend import StrategyAParams
from xrp_futures.strategies.strategy_b_momentum import StrategyBParams, generate as generate_b
from xrp_futures.strategies.strategy_c_trend_momentum import StrategyCParams, generate as generate_c
from xrp_futures.strategies.regime import BULL, BEAR, NEUTRAL


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


def test_strategy_b_long_in_uptrend():
    bars = _synthetic_bars(n=300, drift=0.003, noise=0.0003, seed=1)
    params = StrategyBParams(timeframe="6h", lookback_days=5, form="raw")
    out = generate_b(bars, params)
    assert out["signal"].iloc[-1] == 1


def test_strategy_b_deadband_suppresses_weak_signal():
    bars = _synthetic_bars(n=60, drift=0.0, noise=0.00001, seed=5)  # near-flat
    params = StrategyBParams(timeframe="6h", lookback_days=1, form="raw", deadband=0.5)
    out = generate_b(bars, params)
    assert (out["signal"].iloc[-10:] == 0).all()


def test_strategy_b_unknown_form_raises():
    bars = _synthetic_bars(n=50)
    with pytest.raises(ValueError):
        generate_b(bars, StrategyBParams(form="bogus"))


def test_strategy_c_flat_when_trend_and_momentum_disagree():
    bars = _synthetic_bars(n=200, seed=7)
    params = StrategyCParams(
        timeframe="6h",
        trend=StrategyAParams(timeframe="6h", ema_fast=5, ema_slow=10),
        momentum=StrategyBParams(timeframe="6h", lookback_days=1, form="raw"),
    )
    out = generate_c(bars, params)
    disagree = out["trend_signal"] != out["momentum_signal"]
    assert (out.loc[disagree, "signal"] == 0).all()


def test_strategy_c_matches_trend_when_they_agree():
    bars = _synthetic_bars(n=200, seed=8)
    params = StrategyCParams(timeframe="6h")
    out = generate_c(bars, params)
    agree = out["trend_signal"] == out["momentum_signal"]
    assert (out.loc[agree, "signal"] == out.loc[agree, "trend_signal"]).all()


def test_strategy_c_btc_regime_gate_blocks_contrary_trades():
    bars = _synthetic_bars(n=100, drift=0.002, noise=0.0003, seed=9)
    params = StrategyCParams(
        timeframe="6h",
        trend=StrategyAParams(timeframe="6h", ema_fast=5, ema_slow=15),
        momentum=StrategyBParams(timeframe="6h", lookback_days=1, form="raw"),
        use_btc_regime=True,
    )
    # force a BEAR regime throughout -> no LONGs should survive the gate
    regime = pd.Series([BEAR] * len(bars))
    out = generate_c(bars, params, btc_regime=regime)
    assert (out["signal"] != 1).all()


def test_strategy_c_requires_regime_series_when_gate_enabled():
    bars = _synthetic_bars(n=50)
    params = StrategyCParams(use_btc_regime=True)
    with pytest.raises(ValueError):
        generate_c(bars, params, btc_regime=None)


def test_strategy_c_unknown_mode_raises():
    with pytest.raises(ValueError):
        StrategyCParams(mode="bogus")


def test_strategy_c_veto_mode_keeps_trend_when_momentum_neutral():
    # trend=+1, momentum=0 (neutral, not disagreeing) -> "and" would zero this out,
    # "veto" should keep the trend direction since momentum isn't actively opposing it.
    bars = _synthetic_bars(n=200, seed=7)
    and_params = StrategyCParams(
        timeframe="6h", mode="and",
        trend=StrategyAParams(timeframe="6h", ema_fast=5, ema_slow=10),
        momentum=StrategyBParams(timeframe="6h", lookback_days=1, form="raw", deadband=1e6),  # never fires -> always 0
    )
    veto_params = StrategyCParams(
        timeframe="6h", mode="veto",
        trend=and_params.trend, momentum=and_params.momentum,
    )
    and_out = generate_c(bars, and_params)
    veto_out = generate_c(bars, veto_params)

    assert (and_out["momentum_signal"] == 0).all()  # momentum neutral throughout (huge deadband)
    assert (and_out["signal"] == 0).all()  # "and" zeroes everything out when momentum is always neutral
    assert (veto_out["signal"] == veto_out["trend_signal"]).all()  # "veto" keeps trend when momentum is neutral


def test_strategy_c_veto_mode_still_blocks_active_disagreement():
    bars = _synthetic_bars(n=200, seed=8)
    params = StrategyCParams(timeframe="6h", mode="veto")
    out = generate_c(bars, params)
    active_disagreement = (out["trend_signal"] != 0) & (out["momentum_signal"] == -out["trend_signal"])
    assert (out.loc[active_disagreement, "signal"] == 0).all()
