"""
Monte Carlo resampling over a backtest's own closed trades (§26). A single
backtest realizes ONE possible ordering of its trades; this reshuffles/resamples
that same set of trades thousands of times to see how much drawdown, ruin risk,
and final outcome vary from luck of the draw alone — before trusting the one
sequence that happened to occur.

Each trade's outcome is expressed as a % return on the equity it started with
(so trades can be recombined onto a common baseline regardless of the notional
each one happened to use), then resampled sequences are compounded into
synthetic equity paths.

**Percentile convention** (`MonteCarloResult.percentiles[p]`), for p in
DEFAULT_PERCENTILES = (5, 25, 50, 75, 95):
  - `final_multiple`: the plain p-th percentile of the 5000 simulated final-equity
    multiples. Higher is better here, so this reads intuitively: p5 = a bad/unlucky
    run, p95 = a good/lucky run.
  - `max_drawdown`: NOT a plain percentile of the raw (negative) drawdown values —
    that would read backwards (p95 landing on the MILD, near-zero end, since raw
    ascending order puts the deepest/most-negative numbers at LOW percentiles).
    Instead this is the p-th percentile of drawdown MAGNITUDE, re-signed negative
    for display: p5 = a mild drawdown (only 5% of runs were even milder), p95 = a
    severe drawdown that only 5% of runs were worse than — the standard VaR-style
    "95% of outcomes are no worse than this" reading. So within one table, p5 is
    always the optimistic corner and p95 the pessimistic corner, for BOTH columns.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from xrp_futures.backtest.engine import BacktestResult

DEFAULT_PERCENTILES = (5, 25, 50, 75, 95)


@dataclass(frozen=True)
class MonteCarloConfig:
    n_samples: int = 2000
    seed: int | None = None
    ruin_threshold_pct: float = 0.5  # equity falling to this fraction of start = "ruin" for this study


@dataclass
class MonteCarloResult:
    n_samples: int
    n_trades: int
    final_equity_multiples: np.ndarray
    max_drawdowns: np.ndarray
    prob_of_ruin: float
    percentiles: dict  # {pct: {"final_multiple": ..., "max_drawdown": ...}}


def trade_returns_pct(result: BacktestResult) -> list[float]:
    """
    Each trade's net_pnl expressed as a fraction of the equity the account had
    right before that trade opened (from the ORIGINAL equity curve, not resampled).
    """
    eq = result.equity_curve
    returns = []
    for t in result.trades:
        pos = eq.index.searchsorted(t.entry_time, side="right") - 1
        pos = max(pos, 0)
        equity_before = eq.iloc[pos]
        if equity_before > 0:
            returns.append(t.net_pnl / equity_before)
    return returns


def run_monte_carlo_from_returns(
    returns: list[float], config: MonteCarloConfig = MonteCarloConfig()
) -> MonteCarloResult:
    """
    Core resampling, taking pre-computed per-trade % returns directly — use this to
    pool trades from SEVERAL independent backtests/windows (e.g. every walk-forward
    OOS window, each scored against its own equity path via trade_returns_pct())
    into one larger, still-valid Monte Carlo sample. Plain run_monte_carlo() is a
    thin wrapper over this for the single-BacktestResult case.
    """
    returns = np.asarray(returns, dtype=float)
    n_trades = len(returns)
    if n_trades == 0:
        empty = np.array([])
        return MonteCarloResult(
            n_samples=config.n_samples, n_trades=0,
            final_equity_multiples=empty, max_drawdowns=empty,
            prob_of_ruin=0.0, percentiles={p: {"final_multiple": 1.0, "max_drawdown": 0.0} for p in DEFAULT_PERCENTILES},
        )

    rng = np.random.default_rng(config.seed)
    samples = rng.choice(returns, size=(config.n_samples, n_trades), replace=True)

    equity_paths = np.cumprod(1.0 + samples, axis=1)
    equity_paths = np.concatenate([np.ones((config.n_samples, 1)), equity_paths], axis=1)

    running_max = np.maximum.accumulate(equity_paths, axis=1)
    drawdowns = equity_paths / running_max - 1.0
    max_drawdowns = drawdowns.min(axis=1)
    final_multiples = equity_paths[:, -1]
    ruin_mask = equity_paths.min(axis=1) <= config.ruin_threshold_pct
    prob_of_ruin = float(ruin_mask.mean())

    # max_drawdowns is negative-valued (more negative = worse). A plain
    # np.percentile(max_drawdowns, p) is technically correct but reads backwards
    # from standard risk convention: p95 would land on the MILD end (close to zero,
    # since raw ascending order puts the deepest/most-negative values at LOW
    # percentiles), while everyone reading a "P95 drawdown" expects the SEVERE tail
    # (VaR-style: "95% of outcomes are no worse than this"). Fix: take the
    # percentile of the drawdown's MAGNITUDE (abs value) instead, so p95 correctly
    # lands on the deep/severe tail and p5 on the mild tail — then re-apply the
    # negative sign purely for display, matching every other MaxDD figure in this
    # codebase. final_multiple needs no such flip: it's a "higher is better"
    # quantity already, so its raw ascending percentile is intuitive as-is (p5 =
    # worst-case outcome, p95 = best-case outcome).
    dd_severity = np.abs(max_drawdowns)
    percentiles = {
        p: {
            "final_multiple": float(np.percentile(final_multiples, p)),
            "max_drawdown": -float(np.percentile(dd_severity, p)),
        }
        for p in DEFAULT_PERCENTILES
    }

    return MonteCarloResult(
        n_samples=config.n_samples, n_trades=n_trades,
        final_equity_multiples=final_multiples, max_drawdowns=max_drawdowns,
        prob_of_ruin=prob_of_ruin, percentiles=percentiles,
    )


def run_monte_carlo(result: BacktestResult, config: MonteCarloConfig = MonteCarloConfig()) -> MonteCarloResult:
    return run_monte_carlo_from_returns(trade_returns_pct(result), config)
