"""
Equal-weight multi-asset portfolio built from independent single-asset backtests.

Each asset runs as its own isolated "sleeve" (its own run_backtest, its own
equity), exactly as a set of isolated-margin sub-positions would. For one
evaluation period, every sleeve is allocated capital / N at the period start
and never rebalanced inside the period — so no hidden rebalancing trades or
costs are implied. A sleeve whose data only starts partway through the period
(asset not yet listed) simply holds its allocation as idle cash until then.

Position sizing inside a sleeve is relative to the SLEEVE's equity, so a sleeve
risk_pct of r means each trade risks roughly r / N of total portfolio capital.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from xrp_futures.backtest.engine import BacktestResult
from xrp_futures.backtest.walk_forward import slice_result_by_time


def combine_sleeves(
    results: dict[str, BacktestResult],
    start: date,
    end: date,
    initial_capital: float = 10_000.0,
) -> BacktestResult:
    """
    Combine per-asset BacktestResults into one equal-weight portfolio over [start, end].

    Each sleeve's equity is sliced to the period and normalized to its own first
    value in that period; sleeves are aligned on the union of their timestamps
    (forward-filled — equity only changes on bars where that sleeve had data) and
    held at 1.0 (cash) before their first bar. Trades from every sleeve are kept
    for counting/win-rate purposes, but their USDT amounts are in each sleeve's own
    equity base — use the returned equity curve, not summed trade PnL, for returns.
    """
    if not results:
        raise ValueError("combine_sleeves needs at least one sleeve")

    normalized = {}
    trades = []
    for symbol, result in results.items():
        sub = slice_result_by_time(result, start, end)
        trades.extend(sub.trades)
        eq = sub.equity_curve
        if eq.empty:
            continue
        normalized[symbol] = eq / eq.iloc[0]

    n = len(results)
    if not normalized:
        return BacktestResult(equity_curve=pd.Series(dtype=float, name="equity"), trades=trades, bars=pd.DataFrame())

    frame = pd.DataFrame(normalized).sort_index().ffill().fillna(1.0)
    # Sleeves with no bars at all in the period still count toward N (idle cash).
    equity = frame.sum(axis=1) + (n - frame.shape[1])
    equity = equity * (initial_capital / n)
    equity.name = "equity"
    trades.sort(key=lambda t: t.entry_time)
    return BacktestResult(equity_curve=equity, trades=trades, bars=pd.DataFrame())


def sleeve_return_correlation(results: dict[str, BacktestResult], freq: str = "1W") -> pd.DataFrame:
    """Pairwise correlation of sleeve returns, resampled to `freq` to reduce noise
    from the realized-only (step-shaped) equity curves."""
    rets = {
        symbol: r.equity_curve.resample(freq).last().pct_change()
        for symbol, r in results.items() if not r.equity_curve.empty
    }
    return pd.DataFrame(rets).corr()
