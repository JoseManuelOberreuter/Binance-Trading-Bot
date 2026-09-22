from __future__ import annotations

import pandas as pd

from xrp_futures.indicators.trend import donchian_breakout_signal
from xrp_futures.strategies.base import persist_signal
from xrp_futures.strategies.reversal_confirmation import confirmed_donchian_signal


def _bars(closes):
    idx = pd.RangeIndex(len(closes))
    close = pd.Series(closes, index=idx, dtype=float)
    return close, close, close  # close == high == low: breakout purely on close level


def test_confirm_bars_1_matches_frozen_persist_signal_baseline():
    closes = [10] * 5 + [11, 9, 12, 13, 8, 14, 15] + [15] * 5
    close, high, low = _bars(closes)
    window = 3

    baseline = persist_signal(donchian_breakout_signal(close, high, low, window))
    confirmed = confirmed_donchian_signal(close, high, low, window, confirm_bars=1)

    pd.testing.assert_series_equal(baseline.astype(int), confirmed.astype(int), check_names=False)


def test_single_bar_opposite_breakout_is_filtered_out_with_confirm_bars_2():
    # Sustained uptrend, then ONE bar dips below the lower band (a fakeout),
    # then resumes upward. confirm_bars=2 should NOT flip the signal negative
    # on that single dip bar, unlike the frozen baseline (confirm_bars=1).
    closes = [10, 11, 12, 13, 14, 15, 16, 9, 17, 18, 19]
    close, high, low = _bars(closes)
    window = 3

    baseline = persist_signal(donchian_breakout_signal(close, high, low, window))
    confirmed = confirmed_donchian_signal(close, high, low, window, confirm_bars=2)

    assert (baseline == -1).any(), "test setup should produce a fakeout flip in the baseline"
    assert not (confirmed == -1).any(), "a single-bar dip must not flip the confirmed signal"


def test_sustained_opposite_breakout_still_flips_with_confirm_bars_2():
    # A real, sustained reversal (2+ consecutive bars beyond the opposite band)
    # must still be accepted, just one bar later than the frozen baseline.
    closes = [10, 11, 12, 13, 14, 15, 9, 8, 7, 6]
    close, high, low = _bars(closes)
    window = 3

    confirmed = confirmed_donchian_signal(close, high, low, window, confirm_bars=2)
    assert (confirmed == -1).any(), "a genuine 2-bar-plus reversal must still flip the signal"
