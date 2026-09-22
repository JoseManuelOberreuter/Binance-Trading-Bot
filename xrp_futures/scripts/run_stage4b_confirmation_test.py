"""
STAGE 4b — conceptual test of the Option 2 permanence mechanism (multi-bar
reversal confirmation, see strategies/reversal_confirmation.py) chosen over
Option 1 (Trend Strength reversal filter).

Donchian(40) and Chandelier(5, 4x) stay exactly frozen — only how a REVERSAL
is confirmed changes. confirm_bars=1 is included as a control: it must
reproduce the frozen baseline's OOS numbers exactly (same trades, same
metrics) — if it doesn't, something is wrong with the wiring, not the idea.

confirm_bars=2 and 3 are round, small, non-optimized values (1 and 1.5 days
on 12h bars) chosen for a first robustness look, not a parameter search for
the "best" N. No conclusion should be drawn about an optimal N from this.

For each variant, reports:
  - pooled OOS walk-forward metrics (CAGR, Sharpe, MaxDD, trade count)
  - aggregate capture of the 19 major OOS swings
  - Stage 3 failure-category distribution (does the C-category shrink?)

Usage:
  python -m xrp_futures.scripts.run_stage4b_confirmation_test
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from rich.console import Console

from xrp_futures.backtest.swing_capture import identify_major_swings, trades_in_swing
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward, summarize_walk_forward
from xrp_futures.data import loader
from xrp_futures.scripts.run_stage3_failure_classification import (
    FROZEN_CONFIG,
    MIN_SWING_MOVE_PCT,
    OOS_START,
    TIMEFRAME,
    classify,
    strategy_fn as baseline_strategy_fn,
)
from xrp_futures.strategies.reversal_confirmation import ConfirmedReversalParams
from xrp_futures.strategies.reversal_confirmation import generate as confirmed_generate

console = Console()

CONFIRM_BARS_VARIANTS = [1, 2, 3]  # 1 = control (must match frozen baseline exactly)


def strategy_fn_for(confirm_bars: int):
    params = ConfirmedReversalParams(timeframe=TIMEFRAME, donchian_window=40, confirm_bars=confirm_bars)
    return lambda bars: confirmed_generate(bars, params)


def evaluate_variant(label: str, strategy_fn, xrp_bars, funding, swings) -> dict:
    signal_df = strategy_fn(xrp_bars)[["open_time", "signal"]]

    steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, FROZEN_CONFIG, TIMEFRAME, SPEC_WINDOWS)
    oos_trades = []
    for step in steps:
        oos_trades.extend(step.test_result.trades)
    wf_summary = summarize_walk_forward(steps)

    categories = {}
    total_capture_sum = 0.0
    n_swings_with_trades = 0
    for swing in swings:
        trades = trades_in_swing(oos_trades, swing)
        all_in_window = [t for t in oos_trades if swing.start_time <= t.entry_time <= swing.end_time]
        category, _ = classify(swing, trades, all_in_window, signal_df)
        categories[category] = categories.get(category, 0) + 1
        if trades:
            n_swings_with_trades += 1
            price_capture = sum(t.side * (t.exit_price - t.entry_price) / swing.start_price for t in trades)
            total_capture_sum += price_capture / (swing.pct_move * swing.direction) if swing.pct_move != 0 else 0.0

    return dict(
        label=label, n_trades=len(oos_trades),
        avg_test_sharpe=wf_summary.get("avg_test_sharpe"), avg_test_cagr=wf_summary.get("avg_test_cagr"),
        worst_test_max_drawdown=wf_summary.get("worst_test_max_drawdown"),
        categories=categories, avg_capture_when_traded=total_capture_sum / n_swings_with_trades if n_swings_with_trades else None,
        n_swings_with_trades=n_swings_with_trades,
    )


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)

    oos_start_ts = pd.Timestamp(OOS_START, tz="UTC")
    xrp_bars_oos = xrp_bars[xrp_bars["open_time"] >= oos_start_ts].reset_index(drop=True)
    swings = identify_major_swings(xrp_bars_oos, min_move_pct=MIN_SWING_MOVE_PCT)
    console.print(f"[dim]{len(swings)} major swings found[/dim]\n")

    console.print("[dim]Evaluating baseline (frozen strategy_a_trend, for the control comparison) ...[/dim]")
    baseline_result = evaluate_variant("baseline (frozen)", baseline_strategy_fn, xrp_bars, funding, swings)

    results = [baseline_result]
    for cb in CONFIRM_BARS_VARIANTS:
        console.print(f"[dim]Evaluating confirm_bars={cb} ...[/dim]")
        results.append(evaluate_variant(f"confirm_bars={cb}", strategy_fn_for(cb), xrp_bars, funding, swings))

    console.print(f"\n{'='*100}")
    console.print("[bold]OOS walk-forward summary[/bold]\n")
    console.print(f"{'Variant':<20}{'Trades':>8}{'AvgSharpe':>11}{'AvgCAGR':>10}{'WorstMDD':>10}{'SwingsTraded':>13}{'AvgCapture':>12}")
    for r in results:
        cagr_s = f"{r['avg_test_cagr']*100:.1f}%" if r['avg_test_cagr'] is not None else "n/a"
        mdd_s = f"{r['worst_test_max_drawdown']*100:.1f}%" if r['worst_test_max_drawdown'] is not None else "n/a"
        cap_s = f"{r['avg_capture_when_traded']*100:.0f}%" if r['avg_capture_when_traded'] is not None else "n/a"
        console.print(f"{r['label']:<20}{r['n_trades']:>8}{r['avg_test_sharpe']:>11.2f}{cagr_s:>10}{mdd_s:>10}"
                      f"{r['n_swings_with_trades']:>13}{cap_s:>12}")

    console.print(f"\n{'='*100}")
    console.print("[bold]Failure-category distribution per variant[/bold]\n")
    all_cats = ["A", "B", "C", "D", "E", "F", "G", "OK"]
    console.print(f"{'Variant':<20}" + "".join(f"{c:>5}" for c in all_cats))
    for r in results:
        console.print(f"{r['label']:<20}" + "".join(f"{r['categories'].get(c, 0):>5}" for c in all_cats))

    control = next(r for r in results if r["label"] == "confirm_bars=1")
    baseline = baseline_result
    matches = (control["n_trades"] == baseline["n_trades"] and control["categories"] == baseline["categories"])
    console.print(f"\n[bold]{'OK' if matches else 'MISMATCH'}[/bold]: confirm_bars=1 control "
                  f"{'reproduces' if matches else 'does NOT reproduce'} the frozen baseline "
                  f"({control['n_trades']} vs {baseline['n_trades']} trades)")


if __name__ == "__main__":
    main()
