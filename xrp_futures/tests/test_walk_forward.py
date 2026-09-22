import functools
from datetime import date

import numpy as np
import pandas as pd
import pytest

from xrp_futures.backtest.engine import BacktestConfig, BacktestResult, Trade
from xrp_futures.backtest.walk_forward import (
    WalkForwardWindow, evaluate_fixed_config_walk_forward, run_walk_forward,
    select_best_params, slice_result_by_time,
)
from xrp_futures.strategies.strategy_a_trend import StrategyAParams
from xrp_futures.strategies.strategy_a_trend import generate as generate_a


def test_slice_result_by_time_restricts_equity_and_trades():
    idx = pd.date_range("2024-01-01", periods=10, freq="1D", tz="UTC")
    eq = pd.Series(np.arange(10) + 100.0, index=idx)
    trades = [
        Trade(idx[1], idx[2], 1, 100, 101, 1000, 10, 0, 0, 10, "signal"),
        Trade(idx[8], idx[9], 1, 100, 99, 1000, -10, 0, 0, -10, "signal"),
    ]
    result = BacktestResult(equity_curve=eq, trades=trades, bars=pd.DataFrame())

    sliced = slice_result_by_time(result, date(2024, 1, 2), date(2024, 1, 4))
    assert list(sliced.equity_curve.index) == list(idx[1:4])
    assert len(sliced.trades) == 1
    assert sliced.trades[0].entry_time == idx[1]


def _synthetic_bars(start="2020-01-01", years=7, freq="6h", drift=0.0004, noise=0.0008, seed=0):
    idx = pd.date_range(start, periods=int(365 * 4 * years), freq=freq, tz="UTC")
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + drift + rng.normal(0, noise, len(idx)))
    high = close * (1 + np.abs(rng.normal(0, noise, len(idx))))
    low = close * (1 - np.abs(rng.normal(0, noise, len(idx))))
    open_ = np.roll(close, 1)
    open_[0] = 100.0
    return pd.DataFrame({
        "open_time": idx,
        "close_time": idx + pd.Timedelta(hours=6) - pd.Timedelta(milliseconds=1),
        "open": open_, "high": high, "low": low, "close": close,
    })


def _empty_funding():
    return pd.DataFrame(columns=["calc_time", "last_funding_rate"])


def _strategy_fn_factory(params):
    return lambda bars: generate_a(bars, params)


def test_select_best_params_picks_higher_scoring_candidate_from_train_window_only():
    bars = _synthetic_bars(years=2, drift=0.0006, noise=0.0006, seed=1)  # sustained uptrend
    funding = _empty_funding()
    config = BacktestConfig(initial_equity=10_000.0, risk_pct=0.01, use_vol_targeting=False)

    # A short-lookback EMA pair reacts to the uptrend almost immediately; a very long
    # one barely turns "long" inside a 2-year train window -> should score worse.
    fast_responsive = StrategyAParams(timeframe="6h", ema_fast=5, ema_slow=20)
    slow_unresponsive = StrategyAParams(timeframe="6h", ema_fast=200, ema_slow=800)
    grid = [fast_responsive, slow_unresponsive]

    best_params, best_score, best_metrics = select_best_params(
        bars, funding, grid, _strategy_fn_factory, config,
        train_start=date(2020, 1, 1), train_end=date(2021, 12, 31), timeframe="6h",
    )
    assert best_params is fast_responsive
    assert best_score == best_metrics["sharpe"]


def test_run_walk_forward_produces_one_step_per_window_with_oos_metrics():
    bars = _synthetic_bars(years=7, drift=0.0004, noise=0.0007, seed=2)
    funding = _empty_funding()
    config = BacktestConfig(initial_equity=10_000.0, risk_pct=0.01, use_vol_targeting=False)
    grid = [
        StrategyAParams(timeframe="6h", ema_fast=10, ema_slow=40),
        StrategyAParams(timeframe="6h", ema_fast=20, ema_slow=80),
    ]
    windows = [
        WalkForwardWindow(date(2020, 1, 1), date(2021, 12, 31), date(2022, 1, 1), date(2022, 6, 30), "w1"),
        WalkForwardWindow(date(2021, 1, 1), date(2022, 12, 31), date(2023, 1, 1), date(2023, 6, 30), "w2"),
    ]

    steps = run_walk_forward(bars, funding, grid, _strategy_fn_factory, config, "6h", windows=windows)

    assert len(steps) == 2
    for step, window in zip(steps, windows):
        assert step.window == window
        assert step.chosen_params in grid
        assert "sharpe" in step.train_metrics
        assert "sharpe" in step.test_metrics
        # chosen params must never have seen the test window's own data to be chosen
        assert step.window.train_end < step.window.test_start


def test_evaluate_fixed_config_walk_forward_no_inner_search():
    bars = _synthetic_bars(years=4, drift=0.0004, noise=0.0007, seed=5)
    funding = _empty_funding()
    config = BacktestConfig(initial_equity=10_000.0, risk_pct=0.01, use_vol_targeting=False)
    params = StrategyAParams(timeframe="6h", ema_fast=10, ema_slow=40)
    windows = [
        WalkForwardWindow(date(2020, 1, 1), date(2021, 12, 31), date(2022, 1, 1), date(2022, 6, 30), "w1"),
        WalkForwardWindow(date(2021, 1, 1), date(2022, 12, 31), date(2023, 1, 1), date(2023, 6, 30), "w2"),
    ]
    steps = evaluate_fixed_config_walk_forward(
        bars, funding, _strategy_fn_factory(params), config, "6h", windows=windows,
    )
    assert len(steps) == 2
    for step, window in zip(steps, windows):
        assert step.window == window
        assert "sharpe" in step.test_metrics
        assert step.test_result.equity_curve.index.min() >= pd.Timestamp(window.test_start, tz="UTC")
