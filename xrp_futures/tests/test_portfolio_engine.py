import numpy as np
import pandas as pd
import pytest

from xrp_futures.backtest.costs import CostModel
from xrp_futures.backtest.engine import BacktestConfig, run_backtest
from xrp_futures.backtest.portfolio_engine import run_portfolio_backtest

COST = CostModel(taker_fee_pct=0.0004, slippage_pct=0.0005, funding_enabled=True)


def _random_bars(seed: int, n: int = 300):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    signal = np.sign(pd.Series(close).diff(10).fillna(0)).astype(int).to_numpy()
    ot = pd.date_range("2024-01-01", periods=n, freq="12h", tz="UTC")
    return pd.DataFrame(dict(open_time=ot, close_time=ot + pd.Timedelta(hours=12) - pd.Timedelta(milliseconds=1),
                             open=open_, high=high, low=low, close=close, signal=signal,
                             atr=close * 0.03, realized_vol_ann=0.5, regime=signal))


def _funding(bars):
    times = bars["open_time"] + pd.Timedelta(hours=4)
    return pd.DataFrame(dict(calc_time=times, last_funding_rate=0.0001))


def _config(**kw):
    return BacktestConfig(initial_equity=10_000.0, risk_pct=0.02, use_vol_targeting=False,
                          stop_type="chandelier", chandelier_window=5, chandelier_multiplier=4.0,
                          trailing_enabled=True, cost_model=COST, **kw)


@pytest.mark.parametrize("extra", [{}, {"regime_risk_multipliers": (2.0, 1.0, 0.5)}, {"max_notional_pct": 0.5}])
def test_single_symbol_reproduces_run_backtest(extra):
    bars, cfg = _random_bars(1), _config(**extra)
    single = run_backtest(bars, _funding(bars), cfg)
    port = run_portfolio_backtest({"A": bars}, {"A": _funding(bars)}, cfg).result
    assert len(port.trades) == len(single.trades) > 5
    for a, b in zip(port.trades, single.trades):
        assert (a.entry_time, a.exit_time, a.side, a.exit_reason) == (b.entry_time, b.exit_time, b.side, b.exit_reason)
        assert a.net_pnl == pytest.approx(b.net_pnl, rel=1e-9)
    np.testing.assert_allclose(port.equity_curve.to_numpy(), single.equity_curve.to_numpy(), rtol=1e-9)
    np.testing.assert_allclose(port.mtm_equity_curve.to_numpy(), single.mtm_equity_curve.to_numpy(), rtol=1e-9)


def test_exposure_cap_skips_entries_and_is_never_exceeded():
    symbols = {f"S{k}": _random_bars(k) for k in range(4)}
    fundings = {s: _funding(b) for s, b in symbols.items()}
    cfg = _config()
    free = run_portfolio_backtest(symbols, fundings, cfg)
    capped = run_portfolio_backtest(symbols, fundings, cfg, max_total_notional_pct=0.5)
    assert free.skipped_entries == 0
    assert capped.skipped_entries > 0
    assert len(capped.result.trades) < len(free.result.trades)
    # Rebuild open notional at each entry: never above cap x equity at that moment.
    trades = capped.result.trades
    for t in trades:
        open_before = sum(o.notional_usdt for o in trades
                          if o is not t and o.entry_time <= t.entry_time < o.exit_time)
        equity = capped.result.equity_curve.loc[:t.entry_time].iloc[-1]
        assert open_before + t.notional_usdt <= 0.5 * (equity + t.notional_usdt * 0.0004) + 1e-6


def test_late_listing_symbol_starts_flat():
    a, b = _random_bars(1), _random_bars(2).iloc[100:]
    run = run_portfolio_backtest({"A": a, "B": b}, {"A": _funding(a), "B": _funding(b)}, _config())
    assert all(t.entry_time > b["open_time"].iloc[0] for t in run.trades_by_symbol["B"])
    assert len(run.result.equity_curve) == len(a)


def test_drawdown_multiplier_steps():
    from xrp_futures.backtest.portfolio_engine import drawdown_multiplier
    steps = ((0.10, 0.5), (0.20, 0.25))
    assert drawdown_multiplier(0.05, steps) == 1.0
    assert drawdown_multiplier(0.10, steps) == 0.5
    assert drawdown_multiplier(0.35, steps) == 0.25


def test_drawdown_throttle_shrinks_entries_while_under_water():
    symbols = {f"S{k}": _random_bars(k) for k in range(4)}
    fundings = {s: _funding(b) for s, b in symbols.items()}
    cfg = _config()
    free = run_portfolio_backtest(symbols, fundings, cfg).result
    throttled = run_portfolio_backtest(symbols, fundings, cfg, drawdown_risk_steps=((0.01, 0.1),)).result
    mtm = free.mtm_equity_curve
    assert (mtm / mtm.cummax()).min() < 0.99          # the free run does go under water
    first_free, first_throttled = free.trades[0], throttled.trades[0]
    assert first_throttled.notional_usdt == pytest.approx(first_free.notional_usdt)  # at the peak: full size
    assert max(t.notional_usdt for t in throttled.trades) <= max(t.notional_usdt for t in free.trades)
    assert sum(t.notional_usdt for t in throttled.trades) < 0.5 * sum(t.notional_usdt for t in free.trades)
