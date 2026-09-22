import pandas as pd
import pytest

from xrp_futures.risk import stops


def test_atr_stop_long_is_below_entry():
    price = stops.atr_stop_price(entry_price=100.0, atr_value=2.0, multiplier=2.0, side=1)
    assert price == pytest.approx(96.0)


def test_atr_stop_short_is_above_entry():
    price = stops.atr_stop_price(entry_price=100.0, atr_value=2.0, multiplier=2.0, side=-1)
    assert price == pytest.approx(104.0)


def test_percentage_stop_long_and_short():
    assert stops.percentage_stop_price(100.0, pct=0.02, side=1) == pytest.approx(98.0)
    assert stops.percentage_stop_price(100.0, pct=0.02, side=-1) == pytest.approx(102.0)


def test_chandelier_exit_long_trails_below_recent_high():
    high = pd.Series([100, 105, 110, 108, 107])
    low = high - 2
    atr_series = pd.Series([2.0] * 5)
    exit_level = stops.chandelier_exit_series(high, low, atr_series, window=3, multiplier=1.0, side=1)
    # window=3 rolling max at last bar covers [110,108,107] -> 110 - 1*2 = 108
    assert exit_level.iloc[-1] == pytest.approx(108.0)


def test_chandelier_exit_short_trails_above_recent_low():
    low = pd.Series([100, 95, 90, 92, 93])
    high = low + 2
    atr_series = pd.Series([2.0] * 5)
    exit_level = stops.chandelier_exit_series(high, low, atr_series, window=3, multiplier=1.0, side=-1)
    # window=3 rolling min at last bar covers [90,92,93] -> 90 + 1*2 = 92
    assert exit_level.iloc[-1] == pytest.approx(92.0)


def test_trailing_stop_only_moves_favorably_for_long():
    stop = 95.0
    stop = stops.trailing_stop_update(stop, candidate_stop=97.0, side=1)
    assert stop == 97.0
    # a worse candidate must not loosen the stop
    stop = stops.trailing_stop_update(stop, candidate_stop=90.0, side=1)
    assert stop == 97.0


def test_trailing_stop_only_moves_favorably_for_short():
    stop = 105.0
    stop = stops.trailing_stop_update(stop, candidate_stop=103.0, side=-1)
    assert stop == 103.0
    stop = stops.trailing_stop_update(stop, candidate_stop=110.0, side=-1)
    assert stop == 103.0


def test_stop_distance_pct():
    assert stops.stop_distance_pct(entry_price=100.0, stop_price=98.0) == pytest.approx(0.02)
    assert stops.stop_distance_pct(entry_price=100.0, stop_price=102.0) == pytest.approx(0.02)
