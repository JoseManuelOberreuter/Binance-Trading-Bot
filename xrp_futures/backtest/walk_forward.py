"""
Walk-forward evaluation (§21): pick parameters using only data up to a train
window's end, then evaluate that FIXED, unmodified parameter set on the
following out-of-sample test window it never influenced. Repeated across the
exact rolling windows the spec calls for, so late results can't leak into
earlier parameter choices.

`strategy_fn` must be a zero-extra-arg callable `(bars) -> signal_df` for one
fixed params value — bind params (and, for Strategy C/D, a btc_regime Series)
with functools.partial before building the param_grid, e.g.:
    functools.partial(strategy_a.generate, params=StrategyAParams(ema_fast=50))

Signals are generated causally over the FULL bars history up to each window's
end (so EMA/ATR/etc. have real warmup instead of NaN at the window boundary),
then metrics are computed only from the equity/trades falling inside the
window being scored — no information from outside a window ever affects that
window's score, only the indicator warmup uses earlier bars, which is exactly
what a live system would also have available.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from xrp_futures.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from xrp_futures.backtest.metrics import compute_metrics


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    label: str = ""


# The exact rolling windows from §21.
SPEC_WINDOWS = [
    WalkForwardWindow(date(2020, 1, 1), date(2022, 12, 31), date(2023, 1, 1), date(2023, 12, 31), "2020-22 -> 2023"),
    WalkForwardWindow(date(2021, 1, 1), date(2023, 12, 31), date(2024, 1, 1), date(2024, 12, 31), "2021-23 -> 2024"),
    WalkForwardWindow(date(2022, 1, 1), date(2024, 12, 31), date(2025, 1, 1), date(2025, 12, 31), "2022-24 -> 2025"),
    WalkForwardWindow(date(2023, 1, 1), date(2025, 12, 31), date(2026, 1, 1), date(2026, 12, 31), "2023-25 -> 2026"),
]


@dataclass
class WalkForwardStep:
    window: WalkForwardWindow
    chosen_params: object
    train_score: float
    train_metrics: dict
    test_metrics: dict
    test_result: BacktestResult  # sliced to the test window; feed directly into Monte Carlo/stress testing


def slice_result_by_time(result: BacktestResult, start: date, end: date) -> BacktestResult:
    """
    Restrict a BacktestResult's equity curve and trades to [start, end]. compute_metrics()
    only ever looks at ratios between the slice's own endpoints (CAGR, Sharpe, drawdown are
    all scale-invariant to where the slice starts), so this is a correct, self-contained
    way to score a sub-period without re-running the backtest from a different equity base.
    """
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    eq = result.equity_curve
    sliced_eq = eq[(eq.index >= start_ts) & (eq.index < end_ts)]
    trades = [t for t in result.trades if start_ts <= t.entry_time < end_ts]
    return BacktestResult(equity_curve=sliced_eq, trades=trades, bars=result.bars)


def _bars_up_to(bars: pd.DataFrame, end: date) -> pd.DataFrame:
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    return bars[bars["open_time"] < end_ts].reset_index(drop=True)


def select_best_params(
    bars: pd.DataFrame,
    funding: pd.DataFrame,
    param_grid: list,
    strategy_fn_factory,
    config: BacktestConfig,
    train_start: date,
    train_end: date,
    timeframe: str,
    selection_metric: str = "sharpe",
) -> tuple[object, float, dict]:
    """
    Run every candidate in param_grid over data up to train_end, score each on ONLY
    the [train_start, train_end] slice, and return (best_params, best_score, best_metrics).
    `strategy_fn_factory(params)` must return a `(bars) -> signal_df` callable.
    """
    train_bars = _bars_up_to(bars, train_end)
    best = None
    for params in param_grid:
        signal_df = strategy_fn_factory(params)(train_bars)
        result = run_backtest(signal_df, funding, config)
        sub = slice_result_by_time(result, train_start, train_end)
        metrics = compute_metrics(sub, timeframe)
        score = metrics.get(selection_metric, float("-inf"))
        if best is None or score > best[1]:
            best = (params, score, metrics)
    return best


def run_walk_forward(
    bars: pd.DataFrame,
    funding: pd.DataFrame,
    param_grid: list,
    strategy_fn_factory,
    config: BacktestConfig,
    timeframe: str,
    windows: list[WalkForwardWindow] = None,
    selection_metric: str = "sharpe",
) -> list[WalkForwardStep]:
    windows = windows if windows is not None else SPEC_WINDOWS
    steps = []
    for window in windows:
        chosen_params, train_score, train_metrics = select_best_params(
            bars, funding, param_grid, strategy_fn_factory, config,
            window.train_start, window.train_end, timeframe, selection_metric,
        )

        test_bars = _bars_up_to(bars, window.test_end)
        signal_df = strategy_fn_factory(chosen_params)(test_bars)
        result = run_backtest(signal_df, funding, config)
        test_sub = slice_result_by_time(result, window.test_start, window.test_end)
        test_metrics = compute_metrics(test_sub, timeframe)

        steps.append(WalkForwardStep(
            window=window, chosen_params=chosen_params, train_score=train_score,
            train_metrics=train_metrics, test_metrics=test_metrics, test_result=test_sub,
        ))
    return steps


def summarize_walk_forward(steps: list[WalkForwardStep]) -> dict:
    """
    Aggregate OOS test performance across all windows — the single most important
    number for §33's acceptance bar is the OOS Sharpe/CAGR here, NOT any train score.
    """
    if not steps:
        return dict(n_windows=0)
    test_sharpes = [s.test_metrics["sharpe"] for s in steps]
    test_cagrs = [s.test_metrics["cagr"] for s in steps]
    test_mdds = [s.test_metrics["max_drawdown"] for s in steps]
    return dict(
        n_windows=len(steps),
        avg_test_sharpe=sum(test_sharpes) / len(steps),
        min_test_sharpe=min(test_sharpes),
        avg_test_cagr=sum(test_cagrs) / len(steps),
        worst_test_max_drawdown=min(test_mdds),
        all_windows_positive_sharpe=all(s > 0 for s in test_sharpes),
    )


@dataclass
class FixedConfigStep:
    window: WalkForwardWindow
    test_metrics: dict
    test_result: BacktestResult


def evaluate_fixed_config_walk_forward(
    bars: pd.DataFrame,
    funding: pd.DataFrame,
    strategy_fn,
    config: BacktestConfig,
    timeframe: str,
    windows: list[WalkForwardWindow] = None,
) -> list[FixedConfigStep]:
    """
    Same OOS-windowed evaluation as run_walk_forward(), but for ONE fixed, already-
    chosen strategy configuration — no inner parameter search. Use this instead of
    run_walk_forward() when the goal is "is this specific candidate robust across
    time" rather than "which parameter wins" (deliberately avoids hunting for the
    single best-scoring parameter in each window, which run_walk_forward's inner
    selection step does by design).
    """
    windows = windows if windows is not None else SPEC_WINDOWS
    steps = []
    for window in windows:
        test_bars = _bars_up_to(bars, window.test_end)
        signal_df = strategy_fn(test_bars)
        result = run_backtest(signal_df, funding, config)
        test_sub = slice_result_by_time(result, window.test_start, window.test_end)
        test_metrics = compute_metrics(test_sub, timeframe)
        steps.append(FixedConfigStep(window=window, test_metrics=test_metrics, test_result=test_sub))
    return steps
