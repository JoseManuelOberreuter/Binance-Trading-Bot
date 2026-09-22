import pandas as pd
import pytest

from xrp_futures.backtest.engine import Trade
from xrp_futures.backtest.swing_capture import (
    identify_major_swings, split_trades_by_swing_participation, swing_capture_analysis,
)


def _bars(closes):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="1D", tz="UTC")
    return pd.DataFrame({"open_time": idx, "close": closes})


def test_identify_major_swings_single_clean_uptrend():
    # 100 -> 200 is a +100% up-leg, well above a 30% threshold
    closes = [100, 110, 120, 140, 160, 180, 200]
    bars = _bars(closes)
    swings = identify_major_swings(bars, min_move_pct=0.30)
    assert len(swings) == 1
    assert swings[0].direction == 1
    assert swings[0].start_price == 100
    assert swings[0].end_price == 200
    assert swings[0].pct_move == pytest.approx(1.0)


def test_identify_major_swings_single_clean_downtrend():
    closes = [200, 180, 160, 140, 120, 100]
    bars = _bars(closes)
    swings = identify_major_swings(bars, min_move_pct=0.30)
    assert len(swings) == 1
    assert swings[0].direction == -1
    assert swings[0].pct_move == pytest.approx((100 - 200) / 200)


def test_identify_major_swings_up_then_down():
    # up to 200 (+100%), then down to 100 (-50%) -> two swings
    closes = [100, 130, 160, 200, 180, 150, 120, 100]
    bars = _bars(closes)
    swings = identify_major_swings(bars, min_move_pct=0.30)
    assert len(swings) == 2
    assert swings[0].direction == 1
    assert swings[0].start_price == 100 and swings[0].end_price == 200
    assert swings[1].direction == -1
    assert swings[1].start_price == 200 and swings[1].end_price == 100


def test_identify_major_swings_ignores_moves_below_threshold():
    # only a 10% wiggle -> below a 30% threshold, no swings detected
    closes = [100, 105, 95, 102, 98, 101]
    bars = _bars(closes)
    swings = identify_major_swings(bars, min_move_pct=0.30)
    assert swings == []


def test_identify_major_swings_empty_and_single_bar():
    assert identify_major_swings(_bars([]), min_move_pct=0.3) == []
    assert identify_major_swings(_bars([100]), min_move_pct=0.3) == []


def _trade(entry_time, exit_time, side, net_pnl):
    return Trade(entry_time, exit_time, side, 100, 100, 1000, net_pnl, 0, 0, net_pnl, "signal")


def test_swing_capture_full_participation_scores_near_1():
    swings = identify_major_swings(_bars([100, 130, 160, 200]), min_move_pct=0.3)
    assert len(swings) == 1
    swing = swings[0]
    # a trade open for the whole swing, LONG (matches direction=1), capturing the full
    # theoretical benchmark move at the reference notional
    trade = _trade(swing.start_time, swing.end_time, 1, net_pnl=10_000 * swing.pct_move)
    result = swing_capture_analysis(swings, [trade], reference_notional=10_000)
    assert result["aggregate_capture_ratio"] == pytest.approx(1.0)


def test_swing_capture_wrong_side_trade_is_excluded():
    idx = pd.date_range("2024-01-01", periods=4, freq="1D", tz="UTC")
    bars = _bars([100, 130, 160, 200])
    bars["open_time"] = idx
    swings = identify_major_swings(bars, min_move_pct=0.3)
    swing = swings[0]
    short_trade = _trade(swing.start_time, swing.end_time, -1, net_pnl=-500)  # wrong side
    result = swing_capture_analysis(swings, [short_trade], reference_notional=10_000)
    assert result["per_swing"][0]["n_trades"] == 0
    assert result["per_swing"][0]["captured_pnl"] == 0.0


def test_swing_capture_no_trades_gives_zero_captured():
    idx = pd.date_range("2024-01-01", periods=4, freq="1D", tz="UTC")
    bars = _bars([100, 130, 160, 200])
    bars["open_time"] = idx
    swings = identify_major_swings(bars, min_move_pct=0.3)
    result = swing_capture_analysis(swings, [], reference_notional=10_000)
    assert result["total_captured_pnl"] == 0.0
    assert result["aggregate_capture_ratio"] == pytest.approx(0.0)


def test_swing_capture_partial_overlap_still_counts():
    idx = pd.date_range("2024-01-01", periods=4, freq="1D", tz="UTC")
    bars = _bars([100, 130, 160, 200])
    bars["open_time"] = idx
    swings = identify_major_swings(bars, min_move_pct=0.3)
    swing = swings[0]
    # trade only overlaps the middle of the swing, not the whole thing
    partial_trade = _trade(idx[1], idx[2], 1, net_pnl=1000)
    result = swing_capture_analysis(swings, [partial_trade], reference_notional=10_000)
    assert result["per_swing"][0]["n_trades"] == 1
    assert result["per_swing"][0]["captured_pnl"] == 1000


def test_time_in_move_pct_full_coverage_is_1():
    swings = identify_major_swings(_bars([100, 130, 160, 200]), min_move_pct=0.3)
    swing = swings[0]
    trade = _trade(swing.start_time, swing.end_time, 1, net_pnl=1000)
    result = swing_capture_analysis(swings, [trade], reference_notional=10_000)
    assert result["per_swing"][0]["time_in_move_pct"] == pytest.approx(1.0)
    assert result["aggregate_time_in_move_pct"] == pytest.approx(1.0)


def test_time_in_move_pct_half_coverage():
    idx = pd.date_range("2024-01-01", periods=5, freq="1D", tz="UTC")
    bars = _bars([100, 130, 160, 200, 220])
    bars["open_time"] = idx
    swings = identify_major_swings(bars, min_move_pct=0.3)
    swing = swings[0]
    # trade covers only the first half of the swing's duration
    midpoint = swing.start_time + (swing.end_time - swing.start_time) / 2
    trade = _trade(swing.start_time, midpoint, 1, net_pnl=500)
    result = swing_capture_analysis(swings, [trade], reference_notional=10_000)
    assert result["per_swing"][0]["time_in_move_pct"] == pytest.approx(0.5, abs=0.01)


def test_time_in_move_pct_zero_when_no_trades():
    swings = identify_major_swings(_bars([100, 130, 160, 200]), min_move_pct=0.3)
    result = swing_capture_analysis(swings, [], reference_notional=10_000)
    assert result["per_swing"][0]["time_in_move_pct"] == 0.0
    assert result["aggregate_time_in_move_pct"] == 0.0


def test_time_in_move_pct_overlapping_trades_not_double_counted():
    swings = identify_major_swings(_bars([100, 130, 160, 200]), min_move_pct=0.3)
    swing = swings[0]
    # two overlapping trades spanning the whole swing -> still capped at 1.0, not 2.0
    trade1 = _trade(swing.start_time, swing.end_time, 1, net_pnl=500)
    trade2 = _trade(swing.start_time, swing.end_time, 1, net_pnl=500)
    result = swing_capture_analysis(swings, [trade1, trade2], reference_notional=10_000)
    assert result["per_swing"][0]["time_in_move_pct"] == pytest.approx(1.0)


def test_split_trades_by_swing_participation():
    swings = identify_major_swings(_bars([100, 130, 160, 200]), min_move_pct=0.3)
    swing = swings[0]  # up-leg, direction=1
    matching_trade = _trade(swing.start_time, swing.end_time, 1, net_pnl=1000)  # LONG, overlaps
    wrong_side_trade = _trade(swing.start_time, swing.end_time, -1, net_pnl=-200)  # SHORT, overlaps but wrong side
    noise_trade = _trade(swing.end_time, swing.end_time + pd.Timedelta(days=10), 1, net_pnl=-50)  # no swing overlap

    big_move, other = split_trades_by_swing_participation(
        [matching_trade, wrong_side_trade, noise_trade], swings
    )
    assert big_move == [matching_trade]
    assert other == [wrong_side_trade, noise_trade]


def test_split_trades_by_swing_participation_no_swings_puts_everything_in_other():
    trade = _trade(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"), 1, 100)
    big_move, other = split_trades_by_swing_participation([trade], [])
    assert big_move == []
    assert other == [trade]


from xrp_futures.backtest.swing_capture import detailed_swing_report


def _trade_full(entry_time, exit_time, side, entry_price, exit_price, net_pnl=0.0,
                 mfe_pct=0.0, mae_pct=0.0, duration_hours=None):
    dur = duration_hours if duration_hours is not None else (exit_time - entry_time).total_seconds() / 3600.0
    return Trade(entry_time, exit_time, side, entry_price, exit_price, 1000,
                 net_pnl, 0, 0, net_pnl, "signal", mfe_pct=mfe_pct, mae_pct=mae_pct, duration_hours=dur)


def test_detailed_swing_report_full_single_trade_capture():
    swings = identify_major_swings(_bars([100, 130, 160, 200]), min_move_pct=0.3)
    swing = swings[0]  # 100 -> 200, pct_move = 1.0
    trade = _trade_full(swing.start_time, swing.end_time, 1, 100, 200, net_pnl=1000,
                         mfe_pct=1.05, mae_pct=-0.02)
    rows = detailed_swing_report(swings, [trade])
    assert len(rows) == 1
    row = rows[0]
    assert row["n_trades"] == 1
    assert row["price_capture_pct"] == pytest.approx(1.0)  # (200-100)/100
    assert row["capture_of_move_pct"] == pytest.approx(1.0)  # full capture of the 100% move
    assert row["max_mfe_pct"] == pytest.approx(1.05)
    assert row["worst_mae_pct"] == pytest.approx(-0.02)
    assert row["entry_time"] == swing.start_time
    assert row["exit_time"] == swing.end_time


def test_detailed_swing_report_partial_capture_two_trades():
    swings = identify_major_swings(_bars([100, 130, 160, 200]), min_move_pct=0.3)
    swing = swings[0]
    mid = swing.start_time + (swing.end_time - swing.start_time) / 2
    # first leg: 100 -> 140 (captures 0.40 of the swing's start-price-relative move)
    trade1 = _trade_full(swing.start_time, mid, 1, 100, 140, net_pnl=400)
    # second leg (re-entry after a stop-out): 150 -> 200 (captures 0.50 more)
    trade2 = _trade_full(mid, swing.end_time, 1, 150, 200, net_pnl=500)
    rows = detailed_swing_report(swings, [trade1, trade2])
    row = rows[0]
    assert row["n_trades"] == 2
    assert row["price_capture_pct"] == pytest.approx(0.40 + 0.50)
    assert row["capture_of_move_pct"] == pytest.approx(0.90)
    assert row["captured_pnl_usdt"] == pytest.approx(900)
    assert row["entry_time"] == swing.start_time  # first trade's entry
    assert row["exit_time"] == swing.end_time      # last trade's exit


def test_detailed_swing_report_no_trades_gives_none_capture():
    swings = identify_major_swings(_bars([100, 130, 160, 200]), min_move_pct=0.3)
    rows = detailed_swing_report(swings, [])
    row = rows[0]
    assert row["n_trades"] == 0
    assert row["price_capture_pct"] == pytest.approx(0.0)
    assert row["capture_of_move_pct"] == pytest.approx(0.0)
    assert row["entry_time"] is None
    assert row["exit_time"] is None


def test_detailed_swing_report_short_swing_profit_shows_positive_capture():
    # Regression: capture_of_move_pct must read POSITIVE for a PROFITABLE short
    # during a down-leg, not negative — dividing direction-adjusted price_capture_pct
    # by the swing's raw (negative) pct_move used to flip the sign.
    swings = identify_major_swings(_bars([200, 170, 140, 100]), min_move_pct=0.3)
    assert len(swings) == 1
    swing = swings[0]
    assert swing.direction == -1
    assert swing.pct_move < 0

    short_trade = _trade_full(swing.start_time, swing.end_time, -1, 200, 100, net_pnl=1000)
    rows = detailed_swing_report(swings, [short_trade])
    row = rows[0]
    assert row["price_capture_pct"] > 0  # profited
    assert row["capture_of_move_pct"] == pytest.approx(1.0)  # full capture, positive, not -1.0
    assert row["capture_of_move_pct"] > 0


def test_detailed_swing_report_short_swing_loss_shows_negative_capture():
    swings = identify_major_swings(_bars([200, 170, 140, 100]), min_move_pct=0.3)
    swing = swings[0]
    # a short that lost money (wrong-footed) during the down-leg
    losing_short = _trade_full(swing.start_time, swing.end_time, -1, 200, 220, net_pnl=-200)
    rows = detailed_swing_report(swings, [losing_short])
    row = rows[0]
    assert row["price_capture_pct"] < 0
    assert row["capture_of_move_pct"] < 0


def test_identify_major_swings_no_sub_threshold_swing_from_small_initial_wobble():
    # Regression: a small (~1%) wobble right at the series start, later "confirmed"
    # only because price eventually reverses 30%+ FROM that small extreme, must NOT
    # be recorded as its own swing — every returned swing must clear min_move_pct on
    # its OWN pivot->extreme magnitude, not just on the magnitude of whatever later
    # move happened to trigger its confirmation.
    closes = [100, 100.3, 100.6, 100.9, 100.5, 100.2, 98.9, 99.5, 100.0, 101.0,
              105, 110, 120, 130, 128, 132, 129, 135]
    bars = _bars(closes)
    swings = identify_major_swings(bars, min_move_pct=0.30)
    for s in swings:
        assert abs(s.pct_move) >= 0.30 - 1e-9
    # specifically: no swing should start at bar 0 with the tiny ~-1.1% dip
    assert not any(s.start_price == pytest.approx(100.0) and abs(s.pct_move) < 0.05 for s in swings)
