"""
Stress testing (§27): scripted, deliberately extreme scenarios that may not be well
represented in the historical sample — the question is survival, not profitability.
A scenario "survives" if equity never reaches zero (the position-sizing/risk-engine
layer is what's actually being tested here, not the signal).

In scope for a backtest-only phase: price shocks, funding shocks, slippage shocks,
and combinations of them, all applied to real historical data before re-running the
engine. Operational failure modes named in §27 (API downtime, partial fills, a stop
order failing to execute because the exchange connection drops) require an actual
order-management/execution layer to simulate meaningfully and are deferred to the
live-execution phase — listed here as NOT YET COVERED so that gap stays visible
rather than silently assumed away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from xrp_futures.backtest.costs import CostModel
from xrp_futures.backtest.engine import BacktestConfig, run_backtest
from xrp_futures.backtest.metrics import compute_metrics

NOT_YET_COVERED = [
    "API downtime during an open position",
    "Stop order fails to execute due to connection loss",
    "Order partially filled",
]


def apply_price_shock(bars: pd.DataFrame, shock_pct: float, at_index: int) -> pd.DataFrame:
    """
    Multiply OHLC by (1 + shock_pct) for the bar at `at_index` and every bar after it —
    a permanent level shift starting at that bar, so the shocked bar's own low/high
    reflect the crash (stop-loss logic sees it intrabar) while later bars keep their
    real historical relative moves on top of the new level. This tests the system's
    response to a sudden, sustained repricing without fabricating fake dynamics.
    """
    out = bars.copy()
    factor = 1.0 + shock_pct
    mask = out.index >= at_index
    for col in ("open", "high", "low", "close"):
        out.loc[mask, col] = out.loc[mask, col] * factor
    return out


def apply_funding_shock(funding: pd.DataFrame, multiplier: float) -> pd.DataFrame:
    """Scale (and optionally flip sign of) every funding settlement by `multiplier`."""
    out = funding.copy()
    out["last_funding_rate"] = out["last_funding_rate"] * multiplier
    return out


@dataclass
class StressScenario:
    name: str
    bars_transform: Callable[[pd.DataFrame], pd.DataFrame] | None = None
    funding_transform: Callable[[pd.DataFrame], pd.DataFrame] | None = None
    cost_model: CostModel | None = None  # overrides config.cost_model if given


@dataclass
class StressResult:
    scenario_name: str
    survived: bool  # equity never touched zero
    min_equity: float
    metrics: dict


def default_scenarios(bars: pd.DataFrame, at_fraction: float = 0.5) -> list[StressScenario]:
    at_index = int(len(bars) * at_fraction)
    scenarios = [
        StressScenario(f"price_shock_{int(p * 100)}pct", bars_transform=lambda b, p=p: apply_price_shock(b, p, at_index))
        for p in (-0.20, -0.30, -0.40, -0.50)
    ]
    scenarios += [
        StressScenario(f"funding_shock_x{m}", funding_transform=lambda f, m=m: apply_funding_shock(f, m))
        for m in (3.0, 5.0, -3.0)
    ]
    scenarios += [
        StressScenario(f"slippage_shock_{int(s * 10000)}bps", cost_model=CostModel(slippage_pct=s))
        for s in (0.002, 0.005, 0.01)
    ]
    scenarios.append(StressScenario(
        "combined_worst_case",
        bars_transform=lambda b: apply_price_shock(b, -0.40, at_index),
        funding_transform=lambda f: apply_funding_shock(f, 5.0),
        cost_model=CostModel(slippage_pct=0.01),
    ))
    return scenarios


def run_stress_scenario(
    bars: pd.DataFrame,
    funding: pd.DataFrame,
    strategy_fn,
    config: BacktestConfig,
    scenario: StressScenario,
    timeframe: str,
) -> StressResult:
    """`strategy_fn(bars) -> signal_df` — bind params beforehand (see walk_forward.py)."""
    shocked_bars = scenario.bars_transform(bars) if scenario.bars_transform else bars
    shocked_funding = scenario.funding_transform(funding) if scenario.funding_transform else funding
    shocked_config = config
    if scenario.cost_model is not None:
        import dataclasses
        shocked_config = dataclasses.replace(config, cost_model=scenario.cost_model)

    signal_df = strategy_fn(shocked_bars)
    result = run_backtest(signal_df, shocked_funding, shocked_config)
    metrics = compute_metrics(result, timeframe)
    min_equity = float(result.equity_curve.min()) if len(result.equity_curve) else config.initial_equity

    return StressResult(
        scenario_name=scenario.name,
        survived=min_equity > 0,
        min_equity=min_equity,
        metrics=metrics,
    )


def run_stress_test_suite(
    bars: pd.DataFrame,
    funding: pd.DataFrame,
    strategy_fn,
    config: BacktestConfig,
    timeframe: str,
    scenarios: list[StressScenario] | None = None,
) -> list[StressResult]:
    scenarios = scenarios if scenarios is not None else default_scenarios(bars)
    return [run_stress_scenario(bars, funding, strategy_fn, config, s, timeframe) for s in scenarios]
