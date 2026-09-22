import pandas as pd
import pytest

from xrp_futures.backtest import metrics as m
from xrp_futures.backtest.engine import BacktestResult, Trade


def _equity(values, freq="1h", start="2024-01-01"):
    idx = pd.date_range(start, periods=len(values), freq=freq, tz="UTC")
    return pd.Series(values, index=idx, name="equity")


def test_cagr_doubling_over_one_year_1h():
    bars_per_year = 24 * 365
    eq = _equity([10_000.0] + [10_000.0] * (bars_per_year - 1) + [20_000.0])
    # last-vs-first ratio = 2 over exactly 1 year of bars -> CAGR = 100%
    assert m.cagr(eq, "1h") == pytest.approx(1.0, rel=1e-2)


def test_cagr_flat_equity_is_zero():
    eq = _equity([10_000.0] * 100)
    assert m.cagr(eq, "1h") == pytest.approx(0.0)


def test_max_drawdown_simple_vshape():
    eq = _equity([100, 120, 90, 110])
    # peak 120 -> trough 90 -> dd = 90/120 - 1 = -0.25
    assert m.max_drawdown(eq) == pytest.approx(-0.25)


def test_drawdown_series_zero_at_new_highs():
    eq = _equity([100, 110, 120])
    dd = m.drawdown_series(eq)
    assert (dd == 0).all()


def test_calmar_ratio():
    assert m.calmar_ratio(cagr_value=0.20, max_dd=-0.10) == pytest.approx(2.0)
    assert m.calmar_ratio(cagr_value=0.20, max_dd=0.0) == 0.0


def test_sharpe_zero_for_constant_returns_with_zero_std():
    eq = _equity([100.0] * 50)
    rets = m.equity_returns(eq)
    assert m.sharpe_ratio(rets, "1h") == 0.0


def test_sharpe_positive_for_steadily_rising_equity():
    values = [100 * (1.0001 ** i) for i in range(500)]
    eq = _equity(values)
    rets = m.equity_returns(eq)
    assert m.sharpe_ratio(rets, "1h") > 0


def test_trade_stats_win_rate_and_profit_factor():
    trades = [
        Trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"), 1,
              100, 110, 1000, 100, 2, 0, 98, "signal"),
        Trade(pd.Timestamp("2024-01-02", tz="UTC"), pd.Timestamp("2024-01-03", tz="UTC"), 1,
              110, 100, 1000, -100, 2, 0, -102, "stop"),
    ]
    stats = m.trade_stats(trades)
    assert stats["total_trades"] == 2
    assert stats["win_rate"] == pytest.approx(0.5)
    assert stats["profit_factor"] == pytest.approx(98 / 102)
    assert stats["net_pnl_usdt"] == pytest.approx(98 - 102)
    assert stats["fees_usdt"] == pytest.approx(4)


def test_trade_stats_avg_win_loss_duration_mfe():
    trades = [
        Trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-01T06:00", tz="UTC"), 1,
              100, 110, 1000, 100, 2, 0, 98, "signal", mfe_pct=0.15, duration_hours=6.0),
        Trade(pd.Timestamp("2024-01-02", tz="UTC"), pd.Timestamp("2024-01-02T12:00", tz="UTC"), 1,
              110, 100, 1000, -100, 2, 0, -102, "stop", mfe_pct=0.02, duration_hours=12.0),
    ]
    stats = m.trade_stats(trades)
    assert stats["avg_win_usdt"] == pytest.approx(98)
    assert stats["avg_loss_usdt"] == pytest.approx(-102)
    assert stats["avg_duration_hours"] == pytest.approx(9.0)
    assert stats["avg_mfe_pct"] == pytest.approx(0.085)


def test_top_n_contribution_concentrated_winners():
    # One huge winner should dominate — classic trend-following signature.
    trades = [
        Trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"), 1,
              100, 100, 1000, 0, 0, 0, pnl, "signal")
        for pnl in [-10, -10, -10, -10, 1000]
    ]
    contribution = m.top_n_contribution(trades, 1)
    total = sum(t.net_pnl for t in trades)
    assert contribution == pytest.approx(1000 / total)
    assert contribution > 0.9  # the single best trade drives nearly all of the return


def test_top_n_contribution_near_zero_total_is_unstable_returns_none():
    # total net PnL is exactly 0 here, tiny relative to the scale of the trades
    # themselves -> the ratio would be meaningless (division by ~0), so None not 0.0.
    trades = [
        Trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"), 1,
              100, 100, 1000, 0, 0, 0, pnl, "signal")
        for pnl in [50, -50]
    ]
    assert m.top_n_contribution(trades, 5) is None


def test_top_n_contribution_large_total_relative_to_trades_is_stable():
    # here total (900) is NOT small relative to trade scale (max |pnl|=500) -> stable ratio
    trades = [
        Trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"), 1,
              100, 100, 1000, 0, 0, 0, pnl, "signal")
        for pnl in [500, 400, -50, 50]
    ]
    contribution = m.top_n_contribution(trades, 2)
    assert contribution is not None
    assert contribution == pytest.approx(900 / 900)


def test_top_n_contribution_empty_trades():
    assert m.top_n_contribution([], 5) == 0.0


def test_trade_stats_empty():
    stats = m.trade_stats([])
    assert stats["total_trades"] == 0
    assert stats["win_rate"] == 0.0
    assert stats["profit_factor"] == 0.0


def test_longest_losing_streak():
    trades = []
    for pnl in [10, -5, -5, -5, 10, -5]:
        trades.append(Trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"),
                             1, 100, 100, 1000, pnl, 0, 0, pnl, "signal"))
    assert m.longest_losing_streak(trades) == 3


def test_side_breakdown_separates_long_and_short():
    trades = [
        Trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"),
              1, 100, 110, 1000, 100, 0, 0, 100, "signal"),
        Trade(pd.Timestamp("2024-01-02", tz="UTC"), pd.Timestamp("2024-01-03", tz="UTC"),
              -1, 100, 90, 1000, 100, 0, 0, 100, "signal"),
    ]
    breakdown = m.side_breakdown(trades)
    assert breakdown["long"]["total_trades"] == 1
    assert breakdown["short"]["total_trades"] == 1


def test_compute_metrics_end_to_end_no_trades():
    eq = _equity([10_000.0] * 20)
    result = BacktestResult(equity_curve=eq, trades=[], bars=pd.DataFrame())
    metrics = m.compute_metrics(result, "1h")
    assert metrics["total_trades"] == 0
    assert metrics["cagr"] == pytest.approx(0.0)
    assert metrics["max_drawdown"] == pytest.approx(0.0)


def test_realized_move_pct_long_and_short():
    long_trade = Trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"),
                        1, 100, 110, 1000, 0, 0, 0, 0, "signal")
    short_trade = Trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"),
                         -1, 100, 90, 1000, 0, 0, 0, 0, "signal")
    assert m.realized_move_pct(long_trade) == pytest.approx(0.10)
    assert m.realized_move_pct(short_trade) == pytest.approx(0.10)  # short profits from the drop


def test_mfe_efficiency_full_capture():
    # exit price equals the best favorable price ever seen -> nothing given back
    trade = Trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"),
                  1, 100, 120, 1000, 0, 0, 0, 0, "signal", mfe_pct=0.20)
    stats = m.mfe_efficiency_stats([trade])
    assert stats["avg_realized_pct"] == pytest.approx(0.20)
    assert stats["capture_efficiency"] == pytest.approx(1.0)


def test_mfe_efficiency_partial_giveback():
    # ran up to +20% (MFE) but only exited at +5% -> efficiency well below 1
    trade = Trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"),
                  1, 100, 105, 1000, 0, 0, 0, 0, "signal", mfe_pct=0.20)
    stats = m.mfe_efficiency_stats([trade])
    assert stats["avg_realized_pct"] == pytest.approx(0.05)
    assert stats["capture_efficiency"] == pytest.approx(0.25)


def test_mfe_efficiency_empty_trades():
    stats = m.mfe_efficiency_stats([])
    assert stats["capture_efficiency"] is None
    assert stats["avg_mfe_pct"] == 0.0


def test_mfe_efficiency_zero_mfe_returns_none_efficiency():
    trade = Trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"),
                  1, 100, 100, 1000, 0, 0, 0, 0, "signal", mfe_pct=0.0)
    stats = m.mfe_efficiency_stats([trade])
    assert stats["capture_efficiency"] is None
