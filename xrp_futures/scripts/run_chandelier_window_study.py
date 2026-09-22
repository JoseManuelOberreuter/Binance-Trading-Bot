"""
Controlled Chandelier WINDOW study — follow-up to run_trailing_stop_study.py, which
found (robustly, across both Donchian 40 and 60) that widening the Chandelier
multiplier from 2x to 4x steadily improved win/loss ratio, PF and CAGR without
worsening MaxDD, with 4x as a promising zone. A first window sweep {5,10,15,20,30}
then found window=5 clearly best on every metric, with a monotonic decline toward
30 — this round zooms into the short end, {2,3,4,5,6,7}, as a ROBUSTNESS CHECK: is
5 a real, stable zone or an isolated lucky peak? Multiplier fixed at 4x throughout.
No entry-logic changes, no filters, no simultaneous Donchian optimization.

Also reports how much of net PnL comes from trades that actually overlapped a
major (>=30%) XRP swing in the correct direction ("big-move trades") vs. every
other trade (chop/noise during non-trending stretches) — directly answers "does
it lose a little in the chop and make it back (and then some) on the big moves".

Usage:
  python -m xrp_futures.scripts.run_chandelier_window_study
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from rich import box
from rich.console import Console
from rich.table import Table

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig, run_backtest
from xrp_futures.backtest.metrics import compute_metrics, trade_stats
from xrp_futures.backtest.swing_capture import (
    identify_major_swings, split_trades_by_swing_participation, swing_capture_analysis,
)
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward
from xrp_futures.data import loader
from xrp_futures.strategies import strategy_a_trend as strat_a

console = Console()
TIMEFRAME = "12h"
DONCHIAN_WINDOWS = [40, 60]
CHANDELIER_MULTIPLIER = 4.0  # fixed — the promising zone from the prior study
CHANDELIER_WINDOWS = [2, 3, 4, 5, 6, 7]  # the ONE variable this round — robustness check around the short end
MIN_SWING_MOVE_PCT = 0.30

BASE_CONFIG = BacktestConfig(
    initial_equity=10_000.0, risk_pct=0.005, use_vol_targeting=False,
    stop_type="chandelier", chandelier_multiplier=CHANDELIER_MULTIPLIER,
    trailing_enabled=True, cost_model=BASE,
)


def summarize_walk_forward_extended(steps) -> dict:
    sharpes = [s.test_metrics["sharpe"] for s in steps]
    cagrs = [s.test_metrics["cagr"] for s in steps]
    mdds = [s.test_metrics["max_drawdown"] for s in steps]
    pfs = [s.test_metrics["profit_factor"] for s in steps if s.test_metrics["profit_factor"] not in (0.0, float("inf"))]
    total_trades = sum(s.test_metrics["total_trades"] for s in steps)
    avg_win = [s.test_metrics["avg_win_usdt"] for s in steps if s.test_metrics["total_trades"] > 0]
    avg_loss = [s.test_metrics["avg_loss_usdt"] for s in steps if s.test_metrics["total_trades"] > 0]
    avg_dur = [s.test_metrics["avg_duration_hours"] for s in steps if s.test_metrics["total_trades"] > 0]
    return dict(
        avg_sharpe=sum(sharpes) / len(sharpes),
        avg_cagr=sum(cagrs) / len(cagrs),
        worst_mdd=min(mdds),
        avg_pf=(sum(pfs) / len(pfs)) if pfs else 0.0,
        total_trades=total_trades,
        avg_win_usdt=(sum(avg_win) / len(avg_win)) if avg_win else 0.0,
        avg_loss_usdt=(sum(avg_loss) / len(avg_loss)) if avg_loss else 0.0,
        avg_duration_hours=(sum(avg_dur) / len(avg_dur)) if avg_dur else 0.0,
    )


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)
    swings = identify_major_swings(xrp_bars, min_move_pct=MIN_SWING_MOVE_PCT)
    console.print(f"[dim]{len(xrp_bars)} bars at {TIMEFRAME} | {len(swings)} major swings (>= "
                  f"{MIN_SWING_MOVE_PCT:.0%}) | Chandelier multiplier fixed at {CHANDELIER_MULTIPLIER}x[/dim]\n")

    wf_table = Table(title=f"Walk-Forward OOS — Chandelier window sweep ({CHANDELIER_MULTIPLIER}x ATR fixed)", box=box.SIMPLE_HEAVY)
    for col in ["Donchian", "ChanWin", "Sharpe", "CAGR", "MaxDD", "PF", "Trades", "AvgWin", "AvgLoss", "Dur(h)"]:
        wf_table.add_column(col, justify="right" if col not in ("Donchian", "ChanWin") else "left")

    mfe_table = Table(title="MFE give-back & capture (full history)", box=box.SIMPLE_HEAVY)
    for col in ["Donchian", "ChanWin", "AvgMFE%", "AvgRealized%", "CaptureEff%", "TimeInMove%"]:
        mfe_table.add_column(col, justify="right" if col not in ("Donchian", "ChanWin") else "left")

    split_table = Table(title="Big-move trades vs. chop/noise trades (full history net PnL)", box=box.SIMPLE_HEAVY)
    for col in ["Donchian", "ChanWin", "BigMove N", "BigMove Net$", "Other N", "Other Net$", "Total Net$"]:
        split_table.add_column(col, justify="right" if col not in ("Donchian", "ChanWin") else "left")

    results = []

    for donchian_window in DONCHIAN_WINDOWS:
        params = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=donchian_window)

        def strategy_fn(bars, params=params):
            return strat_a.generate(bars, params)

        for chan_window in CHANDELIER_WINDOWS:
            console.print(f"[dim]Running Donchian {donchian_window}, Chandelier window {chan_window} ...[/dim]")
            config = replace(BASE_CONFIG, chandelier_window=chan_window)

            steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, config, TIMEFRAME, SPEC_WINDOWS)
            summary = summarize_walk_forward_extended(steps)
            wf_table.add_row(
                str(donchian_window), str(chan_window), f"{summary['avg_sharpe']:.2f}", f"{summary['avg_cagr']*100:+.1f}%",
                f"{summary['worst_mdd']*100:.1f}%", f"{summary['avg_pf']:.2f}", str(summary["total_trades"]),
                f"${summary['avg_win_usdt']:.0f}", f"${summary['avg_loss_usdt']:.0f}", f"{summary['avg_duration_hours']:.0f}",
            )

            signal_df = strategy_fn(xrp_bars)
            full_result = run_backtest(signal_df, funding, config)
            full_metrics = compute_metrics(full_result, TIMEFRAME)
            capture = swing_capture_analysis(swings, full_result.trades, config.initial_equity)
            eff = full_metrics["mfe_capture_efficiency"]
            mfe_table.add_row(
                str(donchian_window), str(chan_window),
                f"{full_metrics['avg_mfe_pct']*100:.1f}%", f"{full_metrics['avg_realized_pct']*100:.1f}%",
                f"{eff*100:.0f}%" if eff is not None else "n/a",
                f"{capture['aggregate_time_in_move_pct']*100:.0f}%",
            )

            big_move_trades, other_trades = split_trades_by_swing_participation(full_result.trades, swings)
            big_stats, other_stats = trade_stats(big_move_trades), trade_stats(other_trades)
            split_table.add_row(
                str(donchian_window), str(chan_window),
                str(big_stats["total_trades"]), f"${big_stats['net_pnl_usdt']:,.0f}",
                str(other_stats["total_trades"]), f"${other_stats['net_pnl_usdt']:,.0f}",
                f"${big_stats['net_pnl_usdt'] + other_stats['net_pnl_usdt']:,.0f}",
            )

            results.append(dict(
                donchian=donchian_window, chan_window=chan_window, sharpe=summary["avg_sharpe"],
                cagr=summary["avg_cagr"], mdd=summary["worst_mdd"], pf=summary["avg_pf"],
            ))

    console.print()
    console.print(wf_table)
    console.print()
    console.print(mfe_table)
    console.print()
    console.print(split_table)

    console.print(f"\n[bold]Robust zone check: which Chandelier windows give Sharpe > 0 AND PF > 1 "
                  f"for BOTH Donchian 40 and 60 (at fixed {CHANDELIER_MULTIPLIER}x)?[/bold]")
    for chan_window in CHANDELIER_WINDOWS:
        rows = [r for r in results if r["chan_window"] == chan_window]
        good = all(r["sharpe"] > 0 and r["pf"] > 1 for r in rows)
        detail = ", ".join(f"D{r['donchian']}: Sharpe={r['sharpe']:.2f} PF={r['pf']:.2f}" for r in rows)
        status = "[green]robust — good on both[/green]" if good else "[yellow]not robust on both[/yellow]"
        console.print(f"  window={chan_window:>3}  {status}  ({detail})")

    console.print(f"\n[dim]Single variable changed: Chandelier window. Multiplier fixed at "
                  f"{CHANDELIER_MULTIPLIER}x, Donchian window, sizing, costs, slippage, funding, and "
                  f"walk-forward windows all held fixed from the prior study.[/dim]")


if __name__ == "__main__":
    main()
