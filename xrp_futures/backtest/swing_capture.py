"""
"Does the system lose little while waiting, then capture a real share of XRP's big
moves?" — a direct, custom measurement, not derivable from the standard Sharpe/
CAGR/MaxDD metrics, and the specific thing this round of testing set out to answer.

Method:
  1. identify_major_swings(): a zigzag over closing price — segments history into
     alternating up/down legs, keeping only legs whose magnitude exceeds
     `min_move_pct`. This is what "a big XRP move" means here — transparent and
     parameter-light on purpose (one threshold), not a complex event-detection model.
  2. swing_capture_analysis(): for each major swing, sums the net PnL of trades that
     were (a) in the swing's direction and (b) open at some point during the swing's
     window, and compares that to a fixed reference benchmark — what a full-equity
     directional bet would have earned over the same price move. The ratio is the
     "capture rate": 1.0 would mean the strategy captured as much as being 100% in
     the trade for the whole swing; a strategy that's flat or wrong-footed for most
     of the swing scores near 0 or negative.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from xrp_futures.backtest.engine import Trade


@dataclass(frozen=True)
class Swing:
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    start_price: float
    end_price: float
    direction: int  # +1 up-leg, -1 down-leg
    pct_move: float  # signed: positive for up-legs, negative for down-legs


def identify_major_swings(bars: pd.DataFrame, min_move_pct: float = 0.30) -> list[Swing]:
    """
    Classic zigzag. `direction` means "which leg is currently being tracked from the
    last confirmed pivot": +1 = tracking a rise toward a potential top, -1 = tracking
    a fall toward a potential bottom, 0 = not yet known (start of series — both are
    tracked simultaneously until the first confirmed move settles it).
    """
    if bars.empty or len(bars) < 2:
        return []

    times = bars["open_time"].reset_index(drop=True)
    closes = bars["close"].reset_index(drop=True)

    def emit(swings, p_idx, p_price, e_idx, e_price, dir_):
        # The confirmation checks above measure the REVERSAL magnitude away from the
        # extreme, not the leg's own pivot->extreme magnitude — for most legs these
        # coincide (a pivot is itself a previously-validated extreme, so reaching a
        # new one and then reversing 30%+ from it implies real distance), but for the
        # very FIRST leg the pivot is just the series' arbitrary start bar, which has
        # no such guarantee: a wobble of only ~1% can get "confirmed" purely because
        # price later reversed 30%+ FROM that small extreme. Enforce the actual
        # min_move_pct invariant on the emitted leg itself, not just the trigger.
        pct = (e_price - p_price) / p_price
        if abs(pct) < min_move_pct:
            return
        swings.append(Swing(times[p_idx], times[e_idx], p_price, e_price, dir_, pct))

    swings: list[Swing] = []
    pivot_idx, pivot_price = 0, closes.iloc[0]
    # While direction is unknown (0), track max and min INDEPENDENTLY — sharing one
    # "extreme" variable between them would let a min update clobber a max in progress
    # (or vice versa) before either confirms, corrupting the very first swing detected.
    max_idx, max_price = 0, closes.iloc[0]
    min_idx, min_price = 0, closes.iloc[0]
    extreme_idx = extreme_price = None  # used only once direction is known
    direction = 0

    for i in range(1, len(closes)):
        price = closes.iloc[i]

        if direction == 0:
            if price > max_price:
                max_price, max_idx = price, i
            if price < min_price:
                min_price, min_idx = price, i
            drop = (max_price - price) / max_price
            rise = (price - min_price) / min_price
            if drop >= min_move_pct and max_idx > pivot_idx:
                emit(swings, pivot_idx, pivot_price, max_idx, max_price, 1)
                pivot_idx, pivot_price = max_idx, max_price
                extreme_idx, extreme_price = i, price
                direction = -1
            elif rise >= min_move_pct and min_idx > pivot_idx:
                emit(swings, pivot_idx, pivot_price, min_idx, min_price, -1)
                pivot_idx, pivot_price = min_idx, min_price
                extreme_idx, extreme_price = i, price
                direction = 1
            continue

        if direction == 1:  # tracking a rise: extreme = highest close since pivot
            if price > extreme_price:
                extreme_price, extreme_idx = price, i
            drop = (extreme_price - price) / extreme_price
            if extreme_idx > pivot_idx and drop >= min_move_pct:
                emit(swings, pivot_idx, pivot_price, extreme_idx, extreme_price, 1)
                pivot_idx, pivot_price = extreme_idx, extreme_price
                extreme_idx, extreme_price = i, price
                direction = -1
        else:  # direction == -1: tracking a fall: extreme = lowest close since pivot
            if price < extreme_price:
                extreme_price, extreme_idx = price, i
            rise = (price - extreme_price) / extreme_price
            if extreme_idx > pivot_idx and rise >= min_move_pct:
                emit(swings, pivot_idx, pivot_price, extreme_idx, extreme_price, -1)
                pivot_idx, pivot_price = extreme_idx, extreme_price
                extreme_idx, extreme_price = i, price
                direction = 1

    # Flush whatever leg is still open at the end of the series: a trend that hasn't
    # reversed by min_move_pct YET is still a real move once the data simply ends —
    # a zigzag that only ever emits confirmed (reversed) legs would silently drop the
    # single largest, most recent, still-in-progress move in the entire history.
    if direction == 0:
        candidates = []
        if max_idx > pivot_idx and (max_price - pivot_price) / pivot_price >= min_move_pct:
            candidates.append((max_idx, max_price, 1))
        if min_idx > pivot_idx and (pivot_price - min_price) / pivot_price >= min_move_pct:
            candidates.append((min_idx, min_price, -1))
        if candidates:
            e_idx, e_price, dir_ = max(candidates, key=lambda c: c[0])  # the more recent of the two
            emit(swings, pivot_idx, pivot_price, e_idx, e_price, dir_)
    elif extreme_idx > pivot_idx:
        pct = abs((extreme_price - pivot_price) / pivot_price)
        if pct >= min_move_pct:
            emit(swings, pivot_idx, pivot_price, extreme_idx, extreme_price, direction)

    return swings


def _overlaps(trade: Trade, swing: Swing) -> bool:
    return trade.entry_time < swing.end_time and trade.exit_time > swing.start_time


def trades_in_swing(trades: list[Trade], swing: Swing) -> list[Trade]:
    """All matching-direction trades that overlapped this swing, sorted by entry time."""
    return sorted((t for t in trades if t.side == swing.direction and _overlaps(t, swing)), key=lambda t: t.entry_time)


def split_trades_by_swing_participation(
    trades: list[Trade], swings: list[Swing]
) -> tuple[list[Trade], list[Trade]]:
    """
    Partition trades into (big_move_trades, other_trades): a trade counts as
    "big-move" if it overlaps at least one major swing IN THAT SWING'S DIRECTION
    (a correctly-directed trade riding part of a real trend) — everything else
    (wrong-side trades, and trades that never touch a >=min_move_pct swing at all,
    i.e. noise/chop during non-trending periods) falls into "other". Answers
    "how much comes from actually catching the big moves vs. how much bleeds away
    in between them" — sum net_pnl of each bucket via metrics.trade_stats().
    """
    big_move, other = [], []
    for t in trades:
        matched = any(t.side == s.direction and _overlaps(t, s) for s in swings)
        (big_move if matched else other).append(t)
    return big_move, other


def _merged_overlap_duration(intervals: list[tuple[pd.Timestamp, pd.Timestamp]]) -> pd.Timedelta:
    """Total duration covered by a set of (possibly overlapping) time intervals,
    each counted once even where multiple intervals overlap."""
    if not intervals:
        return pd.Timedelta(0)
    intervals = sorted(intervals)
    total = pd.Timedelta(0)
    cur_start, cur_end = intervals[0]
    for s, e in intervals[1:]:
        if s <= cur_end:
            cur_end = max(cur_end, e)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = s, e
    total += cur_end - cur_start
    return total


def swing_capture_analysis(swings: list[Swing], trades: list[Trade], reference_notional: float) -> dict:
    """
    Two complementary, per-swing AND aggregate views of "did the strategy ride this
    big move":

    - time_in_move_pct: SCALE-INVARIANT — the fraction of the swing's own duration
      during which a matching-direction position was open. Not affected by position
      sizing, so it stays meaningful even for a deliberately conservative (small
      risk-per-trade) strategy. This is the primary, more trustworthy reading.
    - capture_ratio: captured PnL divided by what a FULL-EQUITY (100% of
      `reference_notional`), correctly-directed bet would have earned over the same
      move. Intuitive when it's large, but mechanically SMALL for any strategy that
      (by design, for risk management) never risks anywhere near 100% of equity on
      one move — a low ratio here does not by itself mean the strategy missed the
      move; cross-check against time_in_move_pct before concluding that.
    """
    per_swing = []
    total_captured = 0.0
    total_benchmark = 0.0
    total_swing_duration = pd.Timedelta(0)
    total_covered_duration = pd.Timedelta(0)

    for swing in swings:
        matching = [t for t in trades if t.side == swing.direction and _overlaps(t, swing)]
        captured_pnl = sum(t.net_pnl for t in matching)
        benchmark_pnl = reference_notional * swing.pct_move * swing.direction
        # swing.pct_move is signed already matching direction for up-legs; for down-legs
        # (direction=-1) pct_move is negative too, so pct_move*direction is always positive.
        capture_ratio = (captured_pnl / benchmark_pnl) if abs(benchmark_pnl) > 1e-9 else None

        swing_duration = swing.end_time - swing.start_time
        clipped = [(max(t.entry_time, swing.start_time), min(t.exit_time, swing.end_time)) for t in matching]
        clipped = [(s, e) for s, e in clipped if e > s]
        covered_duration = _merged_overlap_duration(clipped)
        time_in_move_pct = (covered_duration / swing_duration) if swing_duration > pd.Timedelta(0) else 0.0

        per_swing.append(dict(
            start_time=swing.start_time, end_time=swing.end_time, direction=swing.direction,
            pct_move=swing.pct_move, n_trades=len(matching), captured_pnl=captured_pnl,
            benchmark_pnl=benchmark_pnl, capture_ratio=capture_ratio, time_in_move_pct=time_in_move_pct,
        ))
        total_captured += captured_pnl
        total_benchmark += benchmark_pnl
        total_swing_duration += swing_duration
        total_covered_duration += covered_duration

    aggregate_capture_ratio = (total_captured / total_benchmark) if abs(total_benchmark) > 1e-9 else None
    aggregate_time_in_move_pct = (
        total_covered_duration / total_swing_duration if total_swing_duration > pd.Timedelta(0) else 0.0
    )

    return dict(
        n_swings=len(swings),
        per_swing=per_swing,
        total_captured_pnl=total_captured,
        total_benchmark_pnl=total_benchmark,
        aggregate_capture_ratio=aggregate_capture_ratio,
        aggregate_time_in_move_pct=aggregate_time_in_move_pct,
    )


def detailed_swing_report(swings: list[Swing], trades: list[Trade]) -> list[dict]:
    """
    Per-swing narrative report answering "if XRP did +100%, how much of that did we
    actually capture" directly and concretely — one row per major swing:
      start_time, end_time, pct_move (XRP's raw return), direction, n_trades,
      entry_time (first matching trade's entry), exit_time (last matching trade's
      exit), price_capture_pct (see below), capture_of_move_pct (the headline
      number — see below), captured_pnl_usdt, max_mfe_pct, worst_mae_pct,
      total_duration_hours (summed across the swing's trades, NOT wall-clock span —
      can be less than end_time-start_time if the strategy was flat part of the time).

    price_capture_pct: each matching trade's raw price delta (side * (exit-entry))
    expressed as a fraction of the SWING's own starting price (not each trade's own
    entry price), then summed across every trade that touched this swing. Summing on
    a common denominator lets a stopped-out-then-re-entered sequence of trades within
    one swing accumulate toward the swing's total move correctly, and — because
    `side` already makes it positive-for-profit regardless of direction — it is
    ALWAYS positive when the strategy profited in the swing's own direction.

    capture_of_move_pct = price_capture_pct / (pct_move * direction). pct_move is
    the swing's RAW signed return (negative for a down-leg), but price_capture_pct
    is already direction-adjusted (positive = profit); dividing by the raw signed
    pct_move would flip the sign for every SHORT/down-leg swing (a profitable
    short would misleadingly show as a NEGATIVE "% captured"). Multiplying by
    `direction` first makes the denominator a positive magnitude — matching
    swing_capture_analysis()'s benchmark_pnl convention — so this number reads the
    same way for LONG and SHORT swings: positive = captured the move correctly,
    negative = net wrong-footed against it.
    """
    rows = []
    for swing in swings:
        matching = [t for t in trades if t.side == swing.direction and _overlaps(t, swing)]
        matching = sorted(matching, key=lambda t: t.entry_time)

        price_capture_pct = sum(t.side * (t.exit_price - t.entry_price) / swing.start_price for t in matching)
        move_magnitude = swing.pct_move * swing.direction  # always positive
        capture_of_move_pct = (price_capture_pct / move_magnitude) if abs(move_magnitude) > 1e-9 else None
        captured_pnl = sum(t.net_pnl for t in matching)
        total_duration_hours = sum(t.duration_hours for t in matching)

        rows.append(dict(
            start_time=swing.start_time, end_time=swing.end_time,
            start_price=swing.start_price, end_price=swing.end_price,
            direction=swing.direction, pct_move=swing.pct_move,
            n_trades=len(matching),
            entry_time=matching[0].entry_time if matching else None,
            exit_time=matching[-1].exit_time if matching else None,
            price_capture_pct=price_capture_pct,
            capture_of_move_pct=capture_of_move_pct,
            captured_pnl_usdt=captured_pnl,
            max_mfe_pct=max((t.mfe_pct for t in matching), default=0.0),
            worst_mae_pct=min((t.mae_pct for t in matching), default=0.0),
            total_duration_hours=total_duration_hours,
        ))
    return rows
