"""
STAGE 3b — investigate the two "A" swings from Stage 3 (2024-07-31 SHORT -30%,
2025-02-28 LONG +45%) where the classifier found "correct signal appeared, but
no matching-direction trade ever overlapped the swing".

Hypothesis under test: this is a METHODOLOGY artifact of
evaluate_fixed_config_walk_forward(), not a real strategy failure. That function
re-runs run_backtest() separately per window on `bars up to window.test_end`,
then slices the result to trades whose ENTRY_TIME falls inside [test_start,
test_end). A real trade that opened during one window's TRAIN period and only
matured (or got exited) during the following window's TEST period can fall
through the cracks two different ways:
  - In the window it belongs to (test period): its entry_time is before that
    window's test_start -> stripped out by slice_result_by_time.
  - In the previous window (whose bars end at that window's test_end): the
    trade gets artificially force-closed as "end_of_data" at the window
    boundary, truncating it before it reaches the swing at all.

This is pure diagnosis: no strategy/parameter change. We just run ONE
continuous backtest over the full history (no window slicing) and check
whether a real matching-direction trade actually overlaps each swing there.

Usage:
  python -m xrp_futures.scripts.run_stage3b_category_a_boundary_check
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from rich.console import Console

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig, run_backtest
from xrp_futures.backtest.swing_capture import identify_major_swings, trades_in_swing
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward
from xrp_futures.data import loader
from xrp_futures.strategies import strategy_a_trend as strat_a

console = Console()

TIMEFRAME = "12h"
DONCHIAN_WINDOW = 40
CHANDELIER_WINDOW = 5
CHANDELIER_MULTIPLIER = 4.0
RISK_PCT = 0.01
MIN_SWING_MOVE_PCT = 0.30
OOS_START = date(2023, 1, 1)

TARGET_SWINGS = [date(2024, 7, 31), date(2025, 2, 28)]

FROZEN_PARAMS = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=DONCHIAN_WINDOW)
FROZEN_CONFIG = BacktestConfig(
    initial_equity=10_000.0, risk_pct=RISK_PCT, use_vol_targeting=False,
    stop_type="chandelier", chandelier_window=CHANDELIER_WINDOW, chandelier_multiplier=CHANDELIER_MULTIPLIER,
    trailing_enabled=True, cost_model=BASE,
)


def strategy_fn(bars):
    return strat_a.generate(bars, FROZEN_PARAMS)


def window_for(ts: pd.Timestamp):
    for w in SPEC_WINDOWS:
        test_start_ts = pd.Timestamp(w.test_start, tz="UTC")
        test_end_ts = pd.Timestamp(w.test_end, tz="UTC") + pd.Timedelta(days=1)
        if test_start_ts <= ts < test_end_ts:
            return w
    return None


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)

    oos_start_ts = pd.Timestamp(OOS_START, tz="UTC")
    xrp_bars_oos = xrp_bars[xrp_bars["open_time"] >= oos_start_ts].reset_index(drop=True)
    swings = identify_major_swings(xrp_bars_oos, min_move_pct=MIN_SWING_MOVE_PCT)

    targets = []
    for target_start in TARGET_SWINGS:
        target_ts = pd.Timestamp(target_start, tz="UTC")
        candidates = [s for s in swings if abs((s.start_time - target_ts).days) <= 3]
        if candidates:
            targets.append(max(candidates, key=lambda s: abs(s.pct_move)))
    console.print(f"[dim]Matched {len(targets)}/{len(TARGET_SWINGS)} target swings[/dim]")

    console.print("[dim]Re-running the pooled walk-forward version (same as Stage 3) for reference ...[/dim]")
    signal_df = strategy_fn(xrp_bars)[["open_time", "signal"]]
    steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, FROZEN_CONFIG, TIMEFRAME, SPEC_WINDOWS)
    pooled_oos_trades = []
    for step in steps:
        pooled_oos_trades.extend(step.test_result.trades)

    console.print("[dim]Running ONE continuous backtest over the full history (no window slicing) ...[/dim]")
    full_signal_bars = strategy_fn(xrp_bars)
    continuous_result = run_backtest(full_signal_bars, funding, FROZEN_CONFIG)
    continuous_trades = continuous_result.trades
    console.print(f"[dim]Continuous run: {len(continuous_trades)} trades total (vs {len(pooled_oos_trades)} pooled walk-forward)[/dim]")

    for swing in targets:
        console.print(f"\n{'='*100}")
        console.print(f"[bold]Swing: {swing.start_time.date()} -> {swing.end_time.date()}, "
                      f"{'LONG' if swing.direction==1 else 'SHORT'} {swing.pct_move*100:+.0f}%[/bold]")

        w = window_for(swing.start_time)
        console.print(f"  Falls in walk-forward window: {w.label if w else 'none'} "
                      f"(test {w.test_start}..{w.test_end})" if w else "  Falls in no walk-forward test window")

        # 1) What the pooled walk-forward version saw (should reproduce Stage 3's "0 trades")
        pooled_matches = trades_in_swing(pooled_oos_trades, swing)
        console.print(f"\n  Pooled walk-forward trades overlapping this swing (correct direction): {len(pooled_matches)}")
        for t in pooled_matches:
            console.print(f"    {t.entry_time.date()} -> {t.exit_time.date()}  {t.exit_reason}  net={t.net_pnl:+.1f}")

        # 2) What the single continuous backtest saw
        continuous_matches = trades_in_swing(continuous_trades, swing)
        console.print(f"\n  CONTINUOUS (no window slicing) trades overlapping this swing (correct direction): {len(continuous_matches)}")
        for t in continuous_matches:
            crosses_boundary = w is not None and t.entry_time < pd.Timestamp(w.test_start, tz="UTC")
            console.print(f"    {t.entry_time.date()} -> {t.exit_time.date()}  {t.exit_reason}  net={t.net_pnl:+.1f}"
                          f"  entry_before_test_start={crosses_boundary}")

        # 3) Any trade at all (either side) whose life overlaps the swing window, continuous run
        console.print(f"\n  ALL continuous trades (any side) touching [{swing.start_time.date()}, {swing.end_time.date()}]:")
        any_side = [t for t in continuous_trades if t.entry_time < swing.end_time and t.exit_time > swing.start_time]
        if not any_side:
            console.print("    (none)")
        for t in any_side:
            console.print(f"    side={'LONG' if t.side==1 else 'SHORT':<5} {t.entry_time.date()} -> {t.exit_time.date()}"
                          f"  {t.exit_reason}  net={t.net_pnl:+.1f}")

        # 4) Signal value at swing start (sanity check the direction really was signaled)
        sig_at_start = signal_df[signal_df["open_time"] <= swing.start_time].iloc[-1]["signal"]
        console.print(f"\n  Signal value at swing start: {sig_at_start} (swing direction: {swing.direction})")


if __name__ == "__main__":
    main()
