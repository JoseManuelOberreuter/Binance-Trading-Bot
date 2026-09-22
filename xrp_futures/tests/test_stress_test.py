import numpy as np
import pandas as pd
import pytest

from xrp_futures.backtest.costs import BASE, CostModel
from xrp_futures.backtest.engine import BacktestConfig
from xrp_futures.backtest.stress_test import (
    StressScenario, apply_funding_shock, apply_price_shock,
    default_scenarios, run_stress_scenario, run_stress_test_suite,
)
from xrp_futures.strategies.strategy_a_trend import StrategyAParams
from xrp_futures.strategies.strategy_a_trend import generate as generate_a


def _synthetic_bars(n=200, start_price=100.0, drift=0.001, noise=0.0008, seed=0):
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


def _funding(bars):
    times = bars["open_time"].iloc[::2].reset_index(drop=True)
    return pd.DataFrame({"calc_time": times, "last_funding_rate": [0.0001] * len(times)})


def test_apply_price_shock_shifts_bars_from_index_onward_only():
    bars = _synthetic_bars(n=20)
    shocked = apply_price_shock(bars, shock_pct=-0.30, at_index=10)
    for col in ("open", "high", "low", "close"):
        assert shocked.loc[:9, col].tolist() == pytest.approx(bars.loc[:9, col].tolist())
        assert shocked.loc[10:, col].tolist() == pytest.approx((bars.loc[10:, col] * 0.70).tolist())


def test_apply_funding_shock_scales_rate():
    funding = pd.DataFrame({"calc_time": pd.date_range("2024-01-01", periods=3, tz="UTC"),
                             "last_funding_rate": [0.0001, -0.0002, 0.0003]})
    shocked = apply_funding_shock(funding, multiplier=5.0)
    assert shocked["last_funding_rate"].tolist() == pytest.approx([0.0005, -0.0010, 0.0015])

    flipped = apply_funding_shock(funding, multiplier=-3.0)
    assert flipped["last_funding_rate"].tolist() == pytest.approx([-0.0003, 0.0006, -0.0009])


def test_run_stress_scenario_reports_survival_and_metrics():
    bars = _synthetic_bars(n=300, seed=3)
    funding = _funding(bars)
    params = StrategyAParams(timeframe="6h", ema_fast=10, ema_slow=40)
    config = BacktestConfig(initial_equity=10_000.0, risk_pct=0.01, use_vol_targeting=False, cost_model=BASE)

    scenario = StressScenario("price_shock_-30pct", bars_transform=lambda b: apply_price_shock(b, -0.30, 150))
    result = run_stress_scenario(bars, funding, lambda b: generate_a(b, params), config, scenario, "6h")

    assert result.scenario_name == "price_shock_-30pct"
    assert isinstance(result.survived, bool)
    assert "sharpe" in result.metrics
    assert result.min_equity <= config.initial_equity  # never grows before any trade happens


def test_run_stress_scenario_applies_cost_model_override():
    bars = _synthetic_bars(n=250, seed=5)
    funding = _funding(bars)
    params = StrategyAParams(timeframe="6h", ema_fast=10, ema_slow=40)
    base_config = BacktestConfig(initial_equity=10_000.0, risk_pct=0.01, use_vol_targeting=False,
                                  cost_model=CostModel(slippage_pct=0.0005))
    scenario = StressScenario("slippage_shock", cost_model=CostModel(slippage_pct=0.02))

    strategy_fn = lambda b: generate_a(b, params)
    base_result = run_stress_scenario(bars, funding, strategy_fn, base_config,
                                       StressScenario("baseline"), "6h")
    shocked_result = run_stress_scenario(bars, funding, strategy_fn, base_config, scenario, "6h")

    # heavier slippage should not improve net PnL versus the milder baseline cost model
    assert shocked_result.metrics["net_pnl_usdt"] <= base_result.metrics["net_pnl_usdt"] + 1e-6


def test_default_scenarios_cover_expected_categories():
    bars = _synthetic_bars(n=100)
    scenarios = default_scenarios(bars)
    names = [s.name for s in scenarios]
    assert any("price_shock_-20pct" in n for n in names)
    assert any("price_shock_-50pct" in n for n in names)
    assert any("funding_shock_x5" in n for n in names)
    assert any("slippage_shock" in n for n in names)
    assert "combined_worst_case" in names


def test_run_stress_test_suite_runs_all_default_scenarios():
    bars = _synthetic_bars(n=200, seed=11)
    funding = _funding(bars)
    params = StrategyAParams(timeframe="6h", ema_fast=10, ema_slow=40)
    config = BacktestConfig(initial_equity=10_000.0, risk_pct=0.01, use_vol_targeting=False, cost_model=BASE)

    results = run_stress_test_suite(bars, funding, lambda b: generate_a(b, params), config, "6h")
    assert len(results) == len(default_scenarios(bars))
    assert all(r.metrics for r in results)
