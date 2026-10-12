from datetime import date

import numpy as np
import pandas as pd
import pytest

from xrp_futures.backtest.engine import BacktestResult
from xrp_futures.backtest.portfolio import combine_sleeves, sleeve_return_correlation


def _result(values, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="12h", tz="UTC")
    return BacktestResult(equity_curve=pd.Series(values, index=idx, dtype=float), trades=[], bars=pd.DataFrame())


def test_equal_weight_no_rebalance():
    # Sleeve A doubles, sleeve B flat -> portfolio +50%, not the average of per-bar returns.
    results = {"A": _result([100, 150, 200]), "B": _result([50, 50, 50])}
    port = combine_sleeves(results, date(2024, 1, 1), date(2024, 1, 31), initial_capital=1000)
    assert port.equity_curve.iloc[0] == pytest.approx(1000)
    assert port.equity_curve.iloc[-1] == pytest.approx(1500)


def test_sleeve_listed_mid_period_holds_cash_until_first_bar():
    results = {"A": _result([100, 110, 121, 133.1]), "B": _result([10, 20], start="2024-01-02")}
    port = combine_sleeves(results, date(2024, 1, 1), date(2024, 1, 31), initial_capital=200)
    eq = port.equity_curve
    assert eq.iloc[0] == pytest.approx(100 * 1.0 + 100 * 1.0)
    assert eq.iloc[-1] == pytest.approx(100 * 1.331 + 100 * 2.0)


def test_sleeve_without_data_in_period_counts_as_idle_cash():
    results = {"A": _result([100, 200]), "B": _result([100, 100], start="2025-01-01")}
    port = combine_sleeves(results, date(2024, 1, 1), date(2024, 1, 31), initial_capital=1000)
    assert port.equity_curve.iloc[-1] == pytest.approx(500 * 2 + 500)


def test_period_slicing_renormalizes_each_sleeve():
    # 4 days of data (8 bars); only the last 2 days are in the period.
    results = {"A": _result([1, 1, 1, 1, 10, 10, 20, 20])}
    port = combine_sleeves(results, date(2024, 1, 3), date(2024, 1, 4), initial_capital=100)
    assert port.equity_curve.iloc[0] == pytest.approx(100)
    assert port.equity_curve.iloc[-1] == pytest.approx(200)


def test_correlation_identical_sleeves_is_one():
    vals = 100 * np.cumprod(1 + np.random.default_rng(0).normal(0, 0.01, 200))
    corr = sleeve_return_correlation({"A": _result(vals), "B": _result(vals * 3)})
    assert corr.loc["A", "B"] == pytest.approx(1.0)
