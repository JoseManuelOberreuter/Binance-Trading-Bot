"""
STAGE 2 diagnostic (retrospective only — the frozen strategy is NOT modified here):
for the three named moves from Stage 1 (+145% 2023, +533% 2024, +101% 2025), look
at what Trend Strength scored AT THE EXACT MOMENT of every exit during that move,
and how much of the move was still left afterward. This is the direct test of the
hypothesis: "a trend-strength read could distinguish a normal pullback inside a
still-strong trend from an actual trend end."

Trend Strength itself (trend_strength.py) uses fixed, conventional thresholds
chosen before looking at these swings — nothing here is fit to make the diagnostic
look better.

Usage:
  python -m xrp_futures.scripts.run_stage2_trend_strength_diagnostic
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from rich import box
from rich.console import Console
from rich.table import Table

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig
from xrp_futures.backtest.swing_capture import identify_major_swings, trades_in_swing
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward
from xrp_futures.data import loader
from xrp_futures.strategies import strategy_a_trend as strat_a
from xrp_futures.strategies.trend_strength import TrendStrengthParams, compute_trend_strength

console = Console()

TIMEFRAME = "12h"
DONCHIAN_WINDOW = 40
CHANDELIER_WINDOW = 5
CHANDELIER_MULTIPLIER = 4.0
RISK_PCT = 0.01
MIN_SWING_MOVE_PCT = 0.30
OOS_START = date(2023, 1, 1)

# The three named swings, identified by their approximate start dates from Stage 1.
TARGET_SWING_STARTS = [date(2023, 1, 6), date(2024, 8, 5), date(2025, 4, 7)]

FROZEN_PARAMS = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=DONCHIAN_WINDOW)
FROZEN_CONFIG = BacktestConfig(
    initial_equity=10_000.0, risk_pct=RISK_PCT, use_vol_targeting=False,
    stop_type="chandelier", chandelier_window=CHANDELIER_WINDOW, chandelier_multiplier=CHANDELIER_MULTIPLIER,
    trailing_enabled=True, cost_model=BASE,
)


def strategy_fn(bars):
    return strat_a.generate(bars, FROZEN_PARAMS)


def _lookup_at_or_before(ts_df: pd.DataFrame, time_col: str, target_time) -> pd.Series:
    subset = ts_df[ts_df[time_col] <= target_time]
    return subset.iloc[-1]


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)

    oos_start_ts = pd.Timestamp(OOS_START, tz="UTC")
    xrp_bars_oos = xrp_bars[xrp_bars["open_time"] >= oos_start_ts].reset_index(drop=True)
    swings = identify_major_swings(xrp_bars_oos, min_move_pct=MIN_SWING_MOVE_PCT)

    console.print("[dim]Computing Trend Strength (fixed, non-fitted thresholds) ...[/dim]")
    ts_df = compute_trend_strength(xrp_bars, TrendStrengthParams(timeframe=TIMEFRAME))

    console.print("[dim]Running walk-forward to get the pooled OOS trade set ...[/dim]")
    steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, FROZEN_CONFIG, TIMEFRAME, SPEC_WINDOWS)
    oos_trades = []
    for step in steps:
        oos_trades.extend(step.test_result.trades)
    console.print(f"[dim]{len(oos_trades)} OOS trades pooled[/dim]\n")

    targets = []
    for target_start in TARGET_SWING_STARTS:
        target_ts = pd.Timestamp(target_start, tz="UTC")
        candidates = [s for s in swings if abs((s.start_time - target_ts).days) <= 3]
        if candidates:
            targets.append(max(candidates, key=lambda s: abs(s.pct_move)))

    for swing in targets:
        trades = trades_in_swing(oos_trades, swing)
        console.print(f"\n[bold]{'='*90}[/bold]")
        console.print(f"[bold]{swing.start_time.date()} -> {swing.end_time.date()}: "
                      f"XRP {swing.pct_move*100:+.0f}% ({'LONG' if swing.direction==1 else 'SHORT'} swing) "
                      f"| {len(trades)} trades during this move[/bold]")

        table = Table(box=box.SIMPLE_HEAVY)
        for col in ["#", "Exit date", "Exit reason", "Exit price", "TS score", "ADX", "EMA slope",
                    "Bars/extreme", "Mom30d", "Move remaining after exit"]:
            table.add_column(col, justify="right" if col != "Exit reason" else "left")

        for i, t in enumerate(trades, 1):
            row = _lookup_at_or_before(ts_df, "open_time", t.exit_time)
            remaining = swing.direction * (swing.end_price - t.exit_price) / t.exit_price
            table.add_row(
                str(i), t.exit_time.strftime("%Y-%m-%d"), t.exit_reason, f"${t.exit_price:,.3f}",
                f"{int(row['trend_strength'])}/4", f"{row['adx']:.0f}", f"{row['ema_slope']*100:+.1f}%",
                f"{int(row['bars_since_extreme'])}", f"{row['momentum_30d']*100:+.0f}%",
                f"{remaining*100:+.0f}%",
            )
        console.print(table)

        # Direct hypothesis check: exits where a LOT of the move remained (premature-looking)
        # vs. what Trend Strength said at that moment.
        premature_exits = []
        for t in trades[:-1]:  # exclude the final exit of the swing, which SHOULD end it
            row = _lookup_at_or_before(ts_df, "open_time", t.exit_time)
            remaining = swing.direction * (swing.end_price - t.exit_price) / t.exit_price
            if remaining > 0.15:  # more than 15% of the move (relative to exit price) was still ahead
                premature_exits.append((t, row, remaining))

        if premature_exits:
            console.print(f"\n  [yellow]Exits with >15% of the move still ahead ({len(premature_exits)}):[/yellow]")
            for t, row, remaining in premature_exits:
                flag = "[red]still scored TRENDING (>=3)[/red]" if row["trend_strength"] >= 3 else \
                       "[green]already scored weak/no-trend (<=1)[/green]" if row["trend_strength"] <= 1 else \
                       "[yellow]scored 'normal' (2)[/yellow]"
                console.print(f"    {t.exit_time.date()} ({t.exit_reason}): {remaining*100:+.0f}% of move still "
                              f"ahead, Trend Strength was {int(row['trend_strength'])}/4 -> {flag}")
        else:
            console.print("\n  [dim]No exits with substantial move remaining — nothing premature-looking here.[/dim]")


if __name__ == "__main__":
    main()
