"""
STAGE 1 of the trend-strength research phase: before changing anything, measure
EXACTLY how much of XRP's major historical moves the current frozen strategy
actually captures, during the genuinely out-of-sample period only.

  Frozen benchmark: XRPUSDT Perpetual, 12h, Donchian 40, Chandelier(window=5,
  multiplier=4x ATR), LONG+SHORT, risk=1%/trade, isolated margin, no filters.

OOS period = the union of the 4 walk-forward test windows (2023-01-01 to today) —
contiguous, so major swings are identified on this continuous span, and the
"OOS trades" are the pooled, non-overlapping sliced trades from each walk-forward
window (verified safe: the engine is causal/no-lookahead, so a window's sliced
trades never change regardless of how much further data a later window's backtest
run happens to extend through — see walk_forward.py).

Prints one row per major (>=30%) XRP swing in the OOS period with: dates, XRP's
raw return, when the strategy entered/exited, $ and % (of the move) captured,
MFE, MAE, and time in position — directly answering "if XRP did +100%, how much
did we actually capture?" Swings >=50%/100% are called out explicitly.

Usage:
  python -m xrp_futures.scripts.run_stage1_capture_analysis
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from rich import box
from rich.console import Console
from rich.table import Table

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig
from xrp_futures.backtest.swing_capture import detailed_swing_report, identify_major_swings
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

FROZEN_PARAMS = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=DONCHIAN_WINDOW)
FROZEN_CONFIG = BacktestConfig(
    initial_equity=10_000.0, risk_pct=RISK_PCT, use_vol_targeting=False,
    stop_type="chandelier", chandelier_window=CHANDELIER_WINDOW, chandelier_multiplier=CHANDELIER_MULTIPLIER,
    trailing_enabled=True, cost_model=BASE,
)


def strategy_fn(bars):
    return strat_a.generate(bars, FROZEN_PARAMS)


def _fmt_pct(v: float | None) -> str:
    return f"{v * 100:+.0f}%" if v is not None else "n/a"


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)

    oos_start_ts = pd.Timestamp(OOS_START, tz="UTC")
    xrp_bars_oos = xrp_bars[xrp_bars["open_time"] >= oos_start_ts].reset_index(drop=True)
    swings = identify_major_swings(xrp_bars_oos, min_move_pct=MIN_SWING_MOVE_PCT)
    console.print(f"[dim]OOS period: {OOS_START} to today | {len(xrp_bars_oos)} bars at {TIMEFRAME} | "
                  f"{len(swings)} major XRP swings (>= {MIN_SWING_MOVE_PCT:.0%})[/dim]\n")

    console.print("[dim]Running walk-forward to get the pooled OOS trade set ...[/dim]")
    steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, FROZEN_CONFIG, TIMEFRAME, SPEC_WINDOWS)
    oos_trades = []
    for step in steps:
        oos_trades.extend(step.test_result.trades)
    console.print(f"[dim]{len(oos_trades)} OOS trades pooled across {len(steps)} walk-forward windows[/dim]\n")

    rows = detailed_swing_report(swings, oos_trades)

    table = Table(title=f"XRP major swings vs. strategy capture ({OOS_START} to today, {TIMEFRAME})", box=box.SIMPLE_HEAVY)
    for col in ["Start", "End", "Dir", "XRP move", "Entered", "Exited", "Trades",
                "Captured", "% of move", "MFE", "MAE", "Hours in pos"]:
        table.add_column(col, justify="right" if col not in ("Start", "End", "Dir", "Entered", "Exited") else "left")

    for row in rows:
        flag = " **" if abs(row["pct_move"]) >= 1.0 else (" *" if abs(row["pct_move"]) >= 0.5 else "")
        table.add_row(
            row["start_time"].strftime("%Y-%m-%d"), row["end_time"].strftime("%Y-%m-%d"),
            "LONG" if row["direction"] == 1 else "SHORT",
            _fmt_pct(row["pct_move"]) + flag,
            row["entry_time"].strftime("%Y-%m-%d") if row["entry_time"] is not None else "—",
            row["exit_time"].strftime("%Y-%m-%d") if row["exit_time"] is not None else "—",
            str(row["n_trades"]),
            f"${row['captured_pnl_usdt']:,.0f}",
            _fmt_pct(row["capture_of_move_pct"]),
            _fmt_pct(row["max_mfe_pct"]),
            _fmt_pct(row["worst_mae_pct"]),
            f"{row['total_duration_hours']:.0f}",
        )

    console.print(table)
    console.print("[dim]** = XRP move >= 100%   * = XRP move >= 50%[/dim]\n")

    total_pnl = sum(r["captured_pnl_usdt"] for r in rows)
    captured_rows = [r for r in rows if r["n_trades"] > 0]
    missed_rows = [r for r in rows if r["n_trades"] == 0]
    console.print(f"[bold]Summary:[/bold] {len(rows)} major swings, strategy participated in "
                  f"{len(captured_rows)}, missed entirely {len(missed_rows)}. "
                  f"Total $ captured across all swings: ${total_pnl:,.0f}")

    big_moves = [r for r in rows if abs(r["pct_move"]) >= 0.5]
    if big_moves:
        console.print(f"\n[bold]Moves >= 50% specifically ({len(big_moves)}):[/bold]")
        for r in big_moves:
            cap = _fmt_pct(r["capture_of_move_pct"])
            console.print(f"  {r['start_time'].strftime('%Y-%m-%d')} -> {r['end_time'].strftime('%Y-%m-%d')}: "
                          f"XRP {_fmt_pct(r['pct_move'])}, strategy captured {cap} of it "
                          f"(${r['captured_pnl_usdt']:,.0f}, {r['n_trades']} trades, MFE {_fmt_pct(r['max_mfe_pct'])}, "
                          f"MAE {_fmt_pct(r['worst_mae_pct'])})")

    if missed_rows:
        console.print(f"\n[bold yellow]Swings the strategy never traded at all ({len(missed_rows)}):[/bold yellow]")
        for r in missed_rows:
            console.print(f"  {r['start_time'].strftime('%Y-%m-%d')} -> {r['end_time'].strftime('%Y-%m-%d')}: "
                          f"XRP {_fmt_pct(r['pct_move'])}")


if __name__ == "__main__":
    main()
