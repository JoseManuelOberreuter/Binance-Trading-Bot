"""
Controlled trailing-stop width study — follow-up to run_trend_breakout_study.py's
finding that Donchian 40/60 were the only Sharpe-positive breakout variants, but
average win/loss was only ~2x (not the strong asymmetry expected of a system meant
to let winners run), suggesting the trailing stop may be cutting trends short.

ONE variable changes here: Chandelier trailing multiplier, {2, 3 (current baseline),
4, 5} x ATR. Everything else — Donchian window (40, 60 only), Chandelier window (10,
unchanged), sizing, costs, slippage, funding, walk-forward windows — is held fixed,
so any pattern that emerges is attributable to trailing width alone, not confounded
with anything else. No new filters, no parameter search for "the best" combo —
the question is whether there's a MONOTONIC, robust relationship as trailing widens.

Usage:
  python -m xrp_futures.scripts.run_trailing_stop_study
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from rich import box
from rich.console import Console
from rich.table import Table

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig, run_backtest
from xrp_futures.backtest.metrics import compute_metrics
from xrp_futures.backtest.swing_capture import identify_major_swings, swing_capture_analysis
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward
from xrp_futures.data import loader
from xrp_futures.strategies import strategy_a_trend as strat_a

console = Console()
TIMEFRAME = "12h"
DONCHIAN_WINDOWS = [40, 60]
CHANDELIER_MULTIPLIERS = [2.0, 3.0, 4.0, 5.0]  # 3.0 = the prior study's baseline
CHANDELIER_WINDOW = 10  # unchanged from the prior study — not a variable here
MIN_SWING_MOVE_PCT = 0.30

BASE_CONFIG = BacktestConfig(
    initial_equity=10_000.0, risk_pct=0.005, use_vol_targeting=False,
    stop_type="chandelier", chandelier_window=CHANDELIER_WINDOW,
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
    return dict(
        avg_sharpe=sum(sharpes) / len(sharpes),
        avg_cagr=sum(cagrs) / len(cagrs),
        worst_mdd=min(mdds),
        avg_pf=(sum(pfs) / len(pfs)) if pfs else 0.0,
        total_trades=total_trades,
        avg_win_usdt=(sum(avg_win) / len(avg_win)) if avg_win else 0.0,
        avg_loss_usdt=(sum(avg_loss) / len(avg_loss)) if avg_loss else 0.0,
    )


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)
    swings = identify_major_swings(xrp_bars, min_move_pct=MIN_SWING_MOVE_PCT)
    console.print(f"[dim]{len(xrp_bars)} bars at {TIMEFRAME} | {len(swings)} major swings (>= "
                  f"{MIN_SWING_MOVE_PCT:.0%})[/dim]\n")

    wf_table = Table(title="Walk-Forward OOS — trailing width sweep (Chandelier window=10 fixed)", box=box.SIMPLE_HEAVY)
    for col in ["Donchian", "Trail(xATR)", "Sharpe", "CAGR", "MaxDD", "PF", "Trades", "AvgWin", "AvgLoss", "Win/Loss"]:
        wf_table.add_column(col, justify="right" if col not in ("Donchian", "Trail(xATR)") else "left")

    mfe_table = Table(title="MFE give-back & big-move capture (full history)", box=box.SIMPLE_HEAVY)
    for col in ["Donchian", "Trail(xATR)", "AvgMFE%", "AvgRealized%", "CaptureEff%", "TimeInMove%"]:
        mfe_table.add_column(col, justify="right" if col not in ("Donchian", "Trail(xATR)") else "left")

    results = []  # for the closing "robust relationship?" check

    for donchian_window in DONCHIAN_WINDOWS:
        params = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=donchian_window)

        def strategy_fn(bars, params=params):
            return strat_a.generate(bars, params)

        for mult in CHANDELIER_MULTIPLIERS:
            label = f"{mult:.0f}x{' (baseline)' if mult == 3.0 else ''}"
            console.print(f"[dim]Running Donchian {donchian_window}, Chandelier {mult}x ATR ...[/dim]")
            config = replace(BASE_CONFIG, chandelier_multiplier=mult)

            steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, config, TIMEFRAME, SPEC_WINDOWS)
            summary = summarize_walk_forward_extended(steps)
            win_loss_ratio = (summary["avg_win_usdt"] / abs(summary["avg_loss_usdt"])
                               if summary["avg_loss_usdt"] != 0 else 0.0)
            wf_table.add_row(
                str(donchian_window), label, f"{summary['avg_sharpe']:.2f}", f"{summary['avg_cagr']*100:+.1f}%",
                f"{summary['worst_mdd']*100:.1f}%", f"{summary['avg_pf']:.2f}", str(summary["total_trades"]),
                f"${summary['avg_win_usdt']:.0f}", f"${summary['avg_loss_usdt']:.0f}", f"{win_loss_ratio:.2f}",
            )

            signal_df = strategy_fn(xrp_bars)
            full_result = run_backtest(signal_df, funding, config)
            full_metrics = compute_metrics(full_result, TIMEFRAME)
            capture = swing_capture_analysis(swings, full_result.trades, config.initial_equity)
            eff = full_metrics["mfe_capture_efficiency"]
            mfe_table.add_row(
                str(donchian_window), label,
                f"{full_metrics['avg_mfe_pct']*100:.1f}%", f"{full_metrics['avg_realized_pct']*100:.1f}%",
                f"{eff*100:.0f}%" if eff is not None else "n/a",
                f"{capture['aggregate_time_in_move_pct']*100:.0f}%",
            )

            results.append(dict(
                donchian=donchian_window, mult=mult, sharpe=summary["avg_sharpe"], cagr=summary["avg_cagr"],
                mdd=summary["worst_mdd"], pf=summary["avg_pf"], avg_win=summary["avg_win_usdt"],
                avg_loss=summary["avg_loss_usdt"], win_loss_ratio=win_loss_ratio,
                capture_efficiency=eff, time_in_move=capture["aggregate_time_in_move_pct"],
            ))

    console.print()
    console.print(wf_table)
    console.print()
    console.print(mfe_table)

    console.print("\n[bold]Does the relationship hold ROBUSTLY (monotonic across both Donchian windows), "
                  "not just at one lucky point?[/bold]")
    for donchian_window in DONCHIAN_WINDOWS:
        rows = [r for r in results if r["donchian"] == donchian_window]
        rows.sort(key=lambda r: r["mult"])
        win_loss_seq = [r["win_loss_ratio"] for r in rows]
        pf_seq = [r["pf"] for r in rows]
        cagr_seq = [r["cagr"] for r in rows]
        mdd_seq = [r["mdd"] for r in rows]
        monotonic_up = lambda seq: all(b >= a - 1e-9 for a, b in zip(seq, seq[1:]))
        console.print(f"  Donchian {donchian_window}: win/loss ratio {['%.2f' % v for v in win_loss_seq]} "
                       f"{'[green]monotonically non-decreasing[/green]' if monotonic_up(win_loss_seq) else '[yellow]NOT monotonic[/yellow]'}")
        console.print(f"    PF {['%.2f' % v for v in pf_seq]}, CAGR {['%.1f%%' % (v*100) for v in cagr_seq]}, "
                       f"MaxDD {['%.1f%%' % (v*100) for v in mdd_seq]}")

    console.print("\n[dim]Single variable changed: Chandelier multiplier. Donchian window, Chandelier window, "
                  "sizing, costs, slippage, funding, and walk-forward windows all held fixed from the prior "
                  "study.[/dim]")


if __name__ == "__main__":
    main()
