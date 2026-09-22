import pandas as pd
import pytest

from xrp_futures.backtest.engine import BacktestResult, Trade
from xrp_futures.backtest.monte_carlo import (
    MonteCarloConfig, run_monte_carlo, run_monte_carlo_from_returns, trade_returns_pct,
)


def _result_from_trades(net_pnls, start_equity=10_000.0):
    idx = pd.date_range("2024-01-01", periods=len(net_pnls) + 1, freq="1D", tz="UTC")
    equity_vals = [start_equity]
    for pnl in net_pnls:
        equity_vals.append(equity_vals[-1] + pnl)
    eq = pd.Series(equity_vals, index=idx)

    trades = []
    for i, pnl in enumerate(net_pnls):
        trades.append(Trade(
            entry_time=idx[i], exit_time=idx[i + 1], side=1,
            entry_price=100, exit_price=100, notional_usdt=1000,
            gross_pnl=pnl, fees_usdt=0, funding_usdt=0, net_pnl=pnl, exit_reason="signal",
        ))
    return BacktestResult(equity_curve=eq, trades=trades, bars=pd.DataFrame())


def test_trade_returns_pct_matches_manual_calc():
    result = _result_from_trades([100.0, -50.0])  # equity: 10000 -> 10100 -> 10050
    returns = trade_returns_pct(result)
    assert returns[0] == pytest.approx(100.0 / 10_000.0)
    assert returns[1] == pytest.approx(-50.0 / 10_100.0)


def test_monte_carlo_empty_trades_returns_degenerate_result():
    result = _result_from_trades([])
    mc = run_monte_carlo(result, MonteCarloConfig(n_samples=100, seed=1))
    assert mc.n_trades == 0
    assert mc.prob_of_ruin == 0.0


def test_monte_carlo_all_winners_never_ruins():
    net_pnls = [50.0] * 30  # every trade is a small consistent win
    result = _result_from_trades(net_pnls)
    mc = run_monte_carlo(result, MonteCarloConfig(n_samples=500, seed=42, ruin_threshold_pct=0.5))
    assert mc.prob_of_ruin == 0.0
    assert (mc.final_equity_multiples > 1.0).all()


def test_monte_carlo_percentiles_are_ordered():
    net_pnls = [200, -150, 300, -400, 100, -50, 250, -300, 150, -100] * 5
    result = _result_from_trades(net_pnls)
    mc = run_monte_carlo(result, MonteCarloConfig(n_samples=1000, seed=7))
    p = mc.percentiles
    assert p[5]["final_multiple"] <= p[25]["final_multiple"] <= p[50]["final_multiple"]
    assert p[50]["final_multiple"] <= p[75]["final_multiple"] <= p[95]["final_multiple"]
    # Severity convention (see module docstring): p5 = mildest drawdown (least
    # negative), p95 = most severe (most negative) — the standard VaR-style reading,
    # matching final_multiple's own p5=worst/p95=best directionality.
    assert p[5]["max_drawdown"] >= p[50]["max_drawdown"] >= p[95]["max_drawdown"]


def test_monte_carlo_max_drawdown_percentile_severity_convention():
    # Mix of small and large losses -> resampled paths vary meaningfully in depth.
    # p95 must be the deeper (more negative) one, p5 the shallower one — both signed
    # negative for display, confirming the fix's direction unambiguously.
    net_pnls = [-100.0, -200.0, -1500.0, -50.0, -300.0] * 4
    result = _result_from_trades(net_pnls)
    mc = run_monte_carlo(result, MonteCarloConfig(n_samples=1000, seed=1))
    assert mc.percentiles[5]["max_drawdown"] < 0
    assert mc.percentiles[95]["max_drawdown"] < 0
    assert mc.percentiles[95]["max_drawdown"] < mc.percentiles[5]["max_drawdown"]


def test_monte_carlo_is_reproducible_with_seed():
    net_pnls = [100, -80, 60, -40, 120, -90] * 4
    result = _result_from_trades(net_pnls)
    mc1 = run_monte_carlo(result, MonteCarloConfig(n_samples=200, seed=123))
    mc2 = run_monte_carlo(result, MonteCarloConfig(n_samples=200, seed=123))
    assert (mc1.final_equity_multiples == mc2.final_equity_multiples).all()


def test_monte_carlo_heavy_losses_show_material_ruin_probability():
    # Mostly losing streak of large magnitude -> some resampled paths should breach ruin
    net_pnls = [-3000, -3000, -3000, 500, 500, -3000] * 3
    result = _result_from_trades(net_pnls, start_equity=10_000.0)
    mc = run_monte_carlo(result, MonteCarloConfig(n_samples=2000, seed=9, ruin_threshold_pct=0.5))
    assert mc.prob_of_ruin > 0.0


def test_run_monte_carlo_from_returns_matches_run_monte_carlo_for_equivalent_data():
    result = _result_from_trades([100.0, -50.0, 80.0, -30.0])
    returns = trade_returns_pct(result)
    mc1 = run_monte_carlo(result, MonteCarloConfig(n_samples=500, seed=7))
    mc2 = run_monte_carlo_from_returns(returns, MonteCarloConfig(n_samples=500, seed=7))
    assert mc1.n_trades == mc2.n_trades
    assert (mc1.final_equity_multiples == mc2.final_equity_multiples).all()


def test_run_monte_carlo_from_returns_pools_multiple_sources():
    window_a_returns = [0.02, -0.01, 0.015]
    window_b_returns = [0.01, -0.02, 0.03, -0.005]
    pooled = window_a_returns + window_b_returns
    mc = run_monte_carlo_from_returns(pooled, MonteCarloConfig(n_samples=300, seed=3))
    assert mc.n_trades == len(pooled)
    assert mc.prob_of_ruin == 0.0  # small, mixed returns -> no realistic ruin here
