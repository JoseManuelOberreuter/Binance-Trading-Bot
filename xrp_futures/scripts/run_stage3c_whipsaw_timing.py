"""
STAGE 3c — for the swings Stage 3 classified as "C" (whipsaw: >=3 stops before
capturing the move), when exactly do the stop-outs happen relative to (a) each
trade's own entry and (b) the swing's start? Distinguishes two different
underlying problems that would need different fixes:
  - stops cluster in the first few bars after EACH entry -> the trailing stop
    is simply too tight for the noise right after entering (a per-trade
    "grace period" / wider initial trail could help).
  - stops are NOT concentrated early (spread through the trade's life, or take
    many bars) -> the trailing stop is fine per-trade, the problem is more
    that the strategy re-enters into the same noisy patch repeatedly.

Pure diagnosis: reuses the exact classification from Stage 3 (imports its
`classify` function directly so the C-category set is identical, not
re-derived) and the same frozen config. No strategy/parameter change.

Usage:
  python -m xrp_futures.scripts.run_stage3c_whipsaw_timing
"""

from __future__ import annotations

from datetime import date

import numpy as np
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

BARS_PER_DAY = 2  # 12h timeframe


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)

    oos_start_ts = pd.Timestamp(OOS_START, tz="UTC")
    xrp_bars_oos = xrp_bars[xrp_bars["open_time"] >= oos_start_ts].reset_index(drop=True)
    swings = identify_major_swings(xrp_bars_oos, min_move_pct=MIN_SWING_MOVE_PCT)

    signal_df = strategy_fn(xrp_bars)[["open_time", "signal"]]

    console.print("[dim]Running walk-forward to get the pooled OOS trade set (identical to Stage 3) ...[/dim]")
    steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, FROZEN_CONFIG, TIMEFRAME, SPEC_WINDOWS)
    oos_trades = []
    for step in steps:
        oos_trades.extend(step.test_result.trades)

    c_swings = []
    for swing in swings:
        trades = trades_in_swing(oos_trades, swing)
        all_in_window = [t for t in oos_trades if swing.start_time <= t.entry_time <= swing.end_time]
        category, reason = classify(swing, trades, all_in_window, signal_df)
        if category == "C":
            c_swings.append((swing, trades))

    console.print(f"\n[bold]{len(c_swings)} swings classified 'C' (whipsaw) in Stage 3[/bold]\n")

    all_stop_bars_since_entry = []
    all_stop_bars_since_swing_start = []
    first_trade_stop_bars = []
    later_trade_stop_bars = []

    for swing, trades in c_swings:
        console.print(f"{'='*100}")
        console.print(f"[bold]{swing.start_time.date()} -> {swing.end_time.date()}  "
                      f"{'LONG' if swing.direction==1 else 'SHORT'} {swing.pct_move*100:+.0f}%[/bold]")
        console.print(f"{'#':<3}{'Entry':<12}{'Exit':<12}{'Reason':<8}{'BarsHeld':>9}{'BarsFromSwingStart':>20}{'MAE':>7}{'MFE':>7}")
        for i, t in enumerate(trades, 1):
            bars_held = t.duration_hours / 12.0
            bars_from_start = (t.entry_time - swing.start_time).total_seconds() / 3600.0 / 12.0
            console.print(f"{i:<3}{t.entry_time.date().isoformat():<12}{t.exit_time.date().isoformat():<12}"
                          f"{t.exit_reason:<8}{bars_held:>8.1f}b{bars_from_start:>19.1f}b"
                          f"{t.mae_pct*100:>6.1f}%{t.mfe_pct*100:>6.1f}%")
            if t.exit_reason == "stop":
                all_stop_bars_since_entry.append(bars_held)
                all_stop_bars_since_swing_start.append(bars_from_start)
                if i == 1:
                    first_trade_stop_bars.append(bars_held)
                else:
                    later_trade_stop_bars.append(bars_held)

    console.print(f"\n{'='*100}")
    console.print("[bold]AGGREGATE — how many bars does a stopped-out trade last before being stopped?[/bold]\n")
    if all_stop_bars_since_entry:
        arr = np.array(all_stop_bars_since_entry)
        console.print(f"  All stop-outs (n={len(arr)}): mean={arr.mean():.1f} bars ({arr.mean()/BARS_PER_DAY:.1f}d), "
                      f"median={np.median(arr):.1f} bars, min={arr.min():.1f}, max={arr.max():.1f}")
        pct_within_3 = (arr <= 3).mean() * 100
        pct_within_5 = (arr <= 5).mean() * 100
        console.print(f"  Stopped within 3 bars (1.5d) of entering: {pct_within_3:.0f}%")
        console.print(f"  Stopped within 5 bars (2.5d) of entering: {pct_within_5:.0f}%")
    else:
        console.print("  (no stop-out trades found)")

    if first_trade_stop_bars:
        f_arr = np.array(first_trade_stop_bars)
        console.print(f"\n  FIRST trade of each swing that got stopped (n={len(f_arr)}): "
                      f"mean={f_arr.mean():.1f} bars, median={np.median(f_arr):.1f} bars")
    if later_trade_stop_bars:
        l_arr = np.array(later_trade_stop_bars)
        console.print(f"  LATER (2nd+) trades that got stopped (n={len(l_arr)}): "
                      f"mean={l_arr.mean():.1f} bars, median={np.median(l_arr):.1f} bars")

    console.print("\n[dim]Interpretation guide: if most stop-outs happen within ~3-5 bars (1.5-2.5 days) of "
                  "entry, the trailing stop is likely cutting into normal post-entry noise before the move has "
                  "room to show itself. If stop timing is spread out / takes many bars, the issue is less about "
                  "trail tightness right after entry and more about the stop being generally too tight throughout.[/dim]")


if __name__ == "__main__":
    main()
