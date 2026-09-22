"""Buy & Hold benchmark (§23): no signal logic, no risk engine — just holding spot
notional exposure equal to initial equity for the whole period. Wrapped as a
BacktestResult (zero trades) so it can go through the same compute_metrics() as
every strategy for an apples-to-apples comparison."""

from __future__ import annotations

import pandas as pd

from xrp_futures.backtest.engine import BacktestResult


def buy_and_hold_result(bars: pd.DataFrame, initial_equity: float = 10_000.0) -> BacktestResult:
    close = bars["close"].reset_index(drop=True)
    equity = initial_equity * (close / close.iloc[0])
    equity.index = bars["open_time"].reset_index(drop=True)
    equity.name = "equity"
    return BacktestResult(equity_curve=equity, trades=[], bars=bars)
