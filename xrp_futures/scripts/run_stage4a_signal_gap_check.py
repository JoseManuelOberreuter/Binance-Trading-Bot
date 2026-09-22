"""
STAGE 4a — before designing any "permanence" mechanism, confirm WHY re-entry
in a "C" swing's own direction is often delayed by many bars/days after a
stop-out, given that the engine re-enters on the very next bar whenever the
persisted signal is already back to the swing's direction (no cooldown exists
in run_backtest — see engine.py step 2/4).

Two competing explanations:
  (a) the persisted Donchian(40) signal genuinely FLIPS to the opposite
      direction for a while (a real, if short-lived, countertrend breakout)
      before flipping back — i.e. a real "fakeout reversal", possibly with
      its own (losing) opposite-side trade in between.
  (b) something else prevents immediate re-entry despite signal staying the
      same direction (e.g. notional degenerating to ~0 from a very tight
      stop distance).

Pure diagnosis, no strategy change.

Usage:
  python -m xrp_futures.scripts.run_stage4a_signal_gap_check
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from rich.console import Console

from xrp_futures.backtest.swing_capture import identify_major_swings, trades_in_swing
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward
from xrp_futures.data import loader
from xrp_futures.scripts.run_stage3_failure_classification import (
    FROZEN_CONFIG,
    MIN_SWING_MOVE_PCT,
    OOS_START,
    TIMEFRAME,
    classify,
    strategy_fn,
)

console = Console()


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)

    oos_start_ts = pd.Timestamp(OOS_START, tz="UTC")
    xrp_bars_oos = xrp_bars[xrp_bars["open_time"] >= oos_start_ts].reset_index(drop=True)
    swings = identify_major_swings(xrp_bars_oos, min_move_pct=MIN_SWING_MOVE_PCT)

    full_signal_bars = strategy_fn(xrp_bars)
    signal_series = full_signal_bars.set_index("open_time")["signal"]

    console.print("[dim]Running walk-forward to get the pooled OOS trade set (identical to Stage 3) ...[/dim]")
    steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, FROZEN_CONFIG, TIMEFRAME, SPEC_WINDOWS)
    oos_trades = []
    for step in steps:
        oos_trades.extend(step.test_result.trades)
    oos_trades.sort(key=lambda t: t.entry_time)

    c_swings = []
    for swing in swings:
        trades = trades_in_swing(oos_trades, swing)
        all_in_window = [t for t in oos_trades if swing.start_time <= t.entry_time <= swing.end_time]
        category, reason = classify(swing, trades, all_in_window, signal_series.reset_index().rename(columns={"index": "open_time"}))
        if category == "C":
            c_swings.append((swing, trades))

    n_gaps = 0
    n_gaps_with_opposite_flip = 0
    n_gaps_with_opposite_trade = 0
    n_gaps_unexplained = 0

    for swing, trades in c_swings:
        console.print(f"\n{'='*100}")
        console.print(f"[bold]{swing.start_time.date()} -> {swing.end_time.date()}  "
                      f"{'LONG' if swing.direction==1 else 'SHORT'} {swing.pct_move*100:+.0f}%[/bold]")
        for i in range(len(trades) - 1):
            exit_t, next_entry_t = trades[i].exit_time, trades[i + 1].entry_time
            gap_bars = (next_entry_t - exit_t).total_seconds() / 3600.0 / 12.0
            if gap_bars <= 1.5:
                continue  # immediate re-entry, nothing to explain
            n_gaps += 1
            window_sig = signal_series[(signal_series.index > exit_t) & (signal_series.index < next_entry_t)]
            flipped_opposite = (window_sig == -swing.direction).any()
            opposite_trades = [
                t for t in oos_trades
                if t.side == -swing.direction and t.entry_time < next_entry_t and t.exit_time > exit_t
            ]
            if flipped_opposite:
                n_gaps_with_opposite_flip += 1
            if opposite_trades:
                n_gaps_with_opposite_trade += 1
            if not flipped_opposite and not opposite_trades:
                n_gaps_unexplained += 1

            console.print(f"  Gap: {exit_t.date()} -> {next_entry_t.date()} ({gap_bars:.0f} bars / {gap_bars/2:.0f}d) | "
                          f"signal flipped opposite during gap: {flipped_opposite} | "
                          f"opposite-side trade(s) in gap: {len(opposite_trades)}")
            for ot in opposite_trades:
                console.print(f"    opposite trade: {ot.entry_time.date()} -> {ot.exit_time.date()}  "
                              f"{ot.exit_reason}  net={ot.net_pnl:+.1f}")

    console.print(f"\n{'='*100}")
    console.print(f"[bold]Summary — gaps > 1.5 bars between same-direction trades within C-swings: {n_gaps}[/bold]")
    console.print(f"  Explained by a genuine opposite-direction signal flip: {n_gaps_with_opposite_flip}")
    console.print(f"  ... of which had an actual opposite-side trade open:  {n_gaps_with_opposite_trade}")
    console.print(f"  Unexplained (signal stayed same direction the whole gap): {n_gaps_unexplained}")


if __name__ == "__main__":
    main()
