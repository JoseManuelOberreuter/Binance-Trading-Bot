import pandas as pd
import pytest

from xrp_futures.backtest.costs import CostModel
from xrp_futures.backtest.engine import BacktestConfig, run_backtest


def _bars(rows):
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


def test_mfe_tracks_best_favorable_price_for_long():
    # entry at bar1 open=100. bar2 high=115 (best favorable so far). bar3 high=110 (lower,
    # should not reduce MFE). bar4 signal flips flat -> exit. MFE should reflect the 115 peak.
    rows = [
        (100, 101, 99, 100, 1),
        (100, 101, 99, 100, 1),
        (100, 115, 99, 105, 1),
        (105, 110, 104, 106, 0),
        (106, 107, 105, 106, 0),
    ]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="pct", pct_stop=0.5)  # wide, never triggers
    result = run_backtest(bars, _empty_funding(), config)

    assert len(result.trades) == 1
    trade = result.trades[0]
    entry_fill = 100 * 1.0005
    expected_mfe = (115 - entry_fill) / entry_fill
    assert trade.mfe_pct == pytest.approx(expected_mfe, rel=1e-6)


def test_mfe_tracks_best_favorable_price_for_short():
    rows = [
        (100, 101, 99, 100, -1),
        (100, 101, 99, 100, -1),
        (100, 101, 85, 90, -1),   # best favorable (lowest low) for a short is 85
        (90, 95, 89, 94, -1),
        (94, 96, 93, 95, 0),
    ]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="pct", pct_stop=0.5)
    result = run_backtest(bars, _empty_funding(), config)

    assert len(result.trades) == 1
    trade = result.trades[0]
    entry_fill = 100 * (1 - 0.0005)
    expected_mfe = -1 * (85 - entry_fill) / entry_fill
    assert trade.mfe_pct == pytest.approx(expected_mfe, rel=1e-6)


def test_mfe_includes_the_stop_hit_bar_itself():
    # entry at 100. bar2 spikes favorably to 120 THEN reverses and hits the stop within
    # the same bar (low=90). MFE should still capture the 120 high before the reversal.
    rows = [
        (100, 101, 99, 100, 1),
        (100, 120, 90, 95, 1),  # favorable spike to 120, then crashes to 90 (below any reasonable stop)
    ]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="pct", pct_stop=0.05)  # 5% stop -> will be hit by the 90 low
    result = run_backtest(bars, _empty_funding(), config)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stop"
    entry_fill = 100 * 1.0005
    expected_mfe = (120 - entry_fill) / entry_fill
    assert trade.mfe_pct == pytest.approx(expected_mfe, rel=1e-6)


def test_duration_hours_matches_entry_exit_gap():
    rows = [
        (100, 101, 99, 100, 1),
        (100, 101, 99, 100, 1),
        (100, 101, 99, 100, 1),
        (100, 101, 99, 100, 0),
    ]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="pct", pct_stop=0.5)
    result = run_backtest(bars, _empty_funding(), config)

    trade = result.trades[0]
    # entry at bar1 (index1, hour=1), exit at bar3 (index3, hour=3) -> 2 hours
    assert trade.duration_hours == pytest.approx(2.0)


def test_entry_stop_distance_pct_matches_initial_atr_stop():
    # entry fills at 100*1.0005 (slippage), ATR stop = entry - 2*ATR(=2.0) -> mult=2.0
    rows = [
        (100, 101, 99, 100, 1),
        (100, 101, 99, 100, 1),
        (100, 101, 99, 100, 0),
    ]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="atr", atr_multiplier=2.0, trailing_enabled=False)
    result = run_backtest(bars, _empty_funding(), config)

    trade = result.trades[0]
    entry_fill = 100 * 1.0005
    stop_price = entry_fill - 2.0 * 2.0  # atr=2.0 (fixed in _bars), multiplier=2.0
    expected = abs(entry_fill - stop_price) / entry_fill
    assert trade.entry_stop_distance_pct == pytest.approx(expected, rel=1e-6)


def test_entry_stop_distance_pct_unaffected_by_later_trailing():
    # trailing should tighten pos.stop_price over time, but entry_stop_distance_pct
    # must stay pinned to the INITIAL distance, not whatever the stop trailed to.
    rows = [
        (100, 101, 99, 100, 1),
        (100, 105, 99, 104, 1),   # favorable move -> chandelier-style ATR trail tightens
        (104, 110, 103, 109, 1),
        (109, 110, 90, 91, 0),    # eventually exits flat (wide enough stop shouldn't trigger here)
    ]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="atr", atr_multiplier=5.0, trailing_enabled=True)
    result = run_backtest(bars, _empty_funding(), config)

    trade = result.trades[0]
    entry_fill = 100 * 1.0005
    initial_stop = entry_fill - 5.0 * 2.0
    expected = abs(entry_fill - initial_stop) / entry_fill
    assert trade.entry_stop_distance_pct == pytest.approx(expected, rel=1e-6)


def test_mae_tracks_worst_adverse_price_for_long():
    rows = [
        (100, 101, 99, 100, 1),
        (100, 101, 99, 100, 1),
        (100, 102, 90, 95, 1),    # worst adverse excursion so far: low=90
        (95, 98, 92, 96, 1),      # low=92, not worse than 90
        (96, 100, 95, 99, 0),
    ]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="pct", pct_stop=0.5)  # wide, never triggers
    result = run_backtest(bars, _empty_funding(), config)

    trade = result.trades[0]
    entry_fill = 100 * 1.0005
    expected_mae = (90 - entry_fill) / entry_fill  # negative
    assert trade.mae_pct == pytest.approx(expected_mae, rel=1e-6)
    assert trade.mae_pct < 0


def test_mae_tracks_worst_adverse_price_for_short():
    rows = [
        (100, 101, 99, 100, -1),
        (100, 101, 99, 100, -1),
        (100, 115, 98, 105, -1),  # worst adverse for a short: high=115
        (105, 110, 100, 108, -1),
        (108, 109, 105, 106, 0),
    ]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="pct", pct_stop=0.5)
    result = run_backtest(bars, _empty_funding(), config)

    trade = result.trades[0]
    entry_fill = 100 * (1 - 0.0005)
    expected_mae = -1 * (115 - entry_fill) / entry_fill  # negative
    assert trade.mae_pct == pytest.approx(expected_mae, rel=1e-6)
    assert trade.mae_pct < 0


def test_mae_zero_when_price_never_moves_against_position():
    # long entry, price only ever rises -> mae should stay ~0 (entry itself is the "worst").
    # Lows kept above the slippage-adjusted entry fill (100*1.0005) so slippage alone
    # doesn't create a spurious adverse dip.
    rows = [
        (100, 101, 100.1, 100, 1),
        (100, 105, 100.1, 104, 1),
        (104, 110, 104, 109, 0),
    ]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="pct", pct_stop=0.5)
    result = run_backtest(bars, _empty_funding(), config)

    trade = result.trades[0]
    assert trade.mae_pct == pytest.approx(0.0, abs=1e-9)
