import pandas as pd
import pytest

from xrp_futures.backtest.costs import CostModel
from xrp_futures.backtest.engine import BacktestConfig, run_backtest


def _bars(rows):
    """rows: list of (open, high, low, close, signal) at 1h spacing starting 2024-01-01."""
    start = pd.Timestamp("2024-01-01", tz="UTC")
    data = []
    for i, (o, h, l, c, sig) in enumerate(rows):
        ot = start + pd.Timedelta(hours=i)
        data.append(dict(
            open_time=ot, close_time=ot + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
            open=o, high=h, low=l, close=c, signal=sig, atr=2.0, realized_vol_ann=0.15,
        ))
    return pd.DataFrame(data)


def _empty_funding():
    return pd.DataFrame(columns=["calc_time", "last_funding_rate"])


def test_hand_traced_entry_and_atr_stop_exit():
    # bar0: flat, signal turns long (causal, decided at bar0's close)
    # bar1: entry fills at THIS bar's open (101) -> one-bar delayed execution, no lookahead
    # bar2: still long, nothing happens
    # bar3: low=95 breaches the ATR stop -> exit at the stop price
    # bar4: flat, signal 0, no re-entry
    rows = [
        (100, 101, 99, 100, 0),   # bar0
        (101, 102, 99, 101, 1),   # bar1 -> entry happens here (bar1 open), signal decided from bar0
        (101, 103, 100, 102, 1),  # bar2
        (102, 103, 95, 97, 0),    # bar3 -> stop hit intrabar
        (97, 98, 96, 97, 0),      # bar4
    ]
    bars = _bars(rows)
    config = BacktestConfig(
        initial_equity=10_000.0,
        risk_pct=1.0,       # deliberately huge so it never binds -> isolates vol-target sizing
        target_vol=0.15,
        stop_type="atr",
        atr_multiplier=1.0,
        trailing_enabled=False,  # isolate the fixed-initial-stop case; trailing is covered separately
        cost_model=CostModel(taker_fee_pct=0.0004, slippage_pct=0.0005, funding_enabled=True),
    )
    result = run_backtest(bars, _empty_funding(), config)

    # No position exists until bar2 (index 2) -> equity flat at initial through bar1.
    assert result.equity_curve.iloc[0] == pytest.approx(10_000.0)
    assert result.equity_curve.iloc[1] == pytest.approx(10_000.0)

    # Vol-target scalar = target_vol/realized_vol = 1.0 -> notional = equity = 10,000 exactly,
    # since risk_pct=1.0 makes the risk-per-trade notional enormous (not the binding constraint).
    entry_fee = 0.0004 * 10_000.0
    assert result.equity_curve.iloc[2] == pytest.approx(10_000.0 - entry_fee)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == 1
    assert trade.exit_reason == "stop"
    assert trade.notional_usdt == pytest.approx(10_000.0)

    entry_fill = 101 * 1.0005  # slippage on entry (buying)
    stop_price = entry_fill - 1.0 * 2.0  # ATR stop = entry - 1*ATR
    exit_fill = stop_price * (1 - 0.0005)  # slippage on exit (selling)
    expected_gross = (exit_fill - entry_fill) / entry_fill * 10_000.0
    expected_fees = entry_fee + 0.0004 * 10_000.0
    expected_net = expected_gross - expected_fees

    assert trade.entry_price == pytest.approx(entry_fill)
    assert trade.exit_price == pytest.approx(exit_fill)
    assert trade.gross_pnl == pytest.approx(expected_gross, rel=1e-6)
    assert trade.net_pnl == pytest.approx(expected_net, rel=1e-6)

    expected_equity_3 = 10_000.0 - entry_fee + expected_gross - 0.0004 * 10_000.0
    assert result.equity_curve.iloc[3] == pytest.approx(expected_equity_3, rel=1e-6)
    # flat afterwards -> unchanged
    assert result.equity_curve.iloc[4] == pytest.approx(expected_equity_3, rel=1e-6)


def test_signal_only_affects_next_bar_not_same_bar():
    # bar0's signal=1 is decided from bar0's own close and must NOT fill at bar0's own
    # open (100) — it must fill at bar1's open (101), one bar later.
    rows = [
        (100, 101, 99, 100, 1),
        (101, 105, 100, 104, 1),
        (104, 106, 103, 105, 1),
    ]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="pct", pct_stop=0.5)  # wide stop, never triggers
    result = run_backtest(bars, _empty_funding(), config)

    assert len(result.trades) == 1  # still open at the end -> forced "end_of_data" close
    trade = result.trades[0]
    assert trade.exit_reason == "end_of_data"
    assert trade.entry_price == pytest.approx(101 * 1.0005)  # bar1's open, NOT bar0's open (100)


def test_funding_applied_to_open_position_only():
    # bar0 signal=1 (decided causally) -> bar1: entry at open=100
    # bar1 signal=1 (still long) -> bar2: position already open when funding settles inside bar2
    rows = [
        (100, 101, 99, 100, 1),
        (100, 101, 99, 100, 1),
        (100, 106, 99, 105, 1),
        (105, 106, 104, 105, 0),
    ]
    bars = _bars(rows)
    funding_time = bars.loc[2, "open_time"] + pd.Timedelta(minutes=30)  # inside bar2's interval
    funding = pd.DataFrame({"calc_time": [funding_time], "last_funding_rate": [0.0004]})

    config = BacktestConfig(stop_type="pct", pct_stop=0.9)  # effectively unreachable
    result = run_backtest(bars, funding, config)

    equity_before_funding = result.equity_curve.iloc[1]  # right after entry, before bar2
    equity_after_funding_bar = result.equity_curve.iloc[2]

    # Position is LONG and funding_rate is positive -> longs pay -> equity should drop by
    # notional * funding_rate relative to what it would've been without funding, i.e. the
    # bar-over-bar equity change should be strictly negative from the funding charge alone
    # (bar2 has no stop hit and signal doesn't change, so funding is the only equity driver).
    assert equity_after_funding_bar < equity_before_funding

    trade = result.trades[0]
    assert trade.funding_usdt < 0  # longs paid
    assert trade.funding_usdt == pytest.approx(equity_after_funding_bar - equity_before_funding, rel=1e-6)


def test_no_trade_when_flat_throughout():
    rows = [(100, 101, 99, 100, 0)] * 5
    bars = _bars(rows)
    result = run_backtest(bars, _empty_funding(), BacktestConfig())
    assert result.trades == []
    assert (result.equity_curve == 10_000.0).all()
