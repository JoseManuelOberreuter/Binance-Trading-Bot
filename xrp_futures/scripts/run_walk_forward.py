"""
Wider walk-forward sweep on Strategy C: expanded parameter grid (EMA pairs x
momentum lookbacks x "and"/"veto" combination mode, §25) across multiple
timeframes. This follows up on a first walk-forward run that showed "and" mode
+ 6h alone had ~zero OOS Sharpe — this run tests whether the relaxed "veto" mode
and/or a different timeframe do better, without assuming either will.

1h is deliberately excluded from the sweep here (its 4x larger bar count makes
each backtest ~4x slower — with a 12-candidate grid across 4 windows that adds
several extra minutes for the timeframe the spec's own overtrading concern (§7)
makes least likely to be the answer). Add it explicitly with --timeframes if needed.

Usage:
  python -m xrp_futures.scripts.run_walk_forward
  python -m xrp_futures.scripts.run_walk_forward --timeframes 4h 6h 12h 1d
"""

from __future__ import annotations

import argparse
from datetime import date

from rich import box
from rich.console import Console
from rich.table import Table

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig
from xrp_futures.backtest.monte_carlo import MonteCarloConfig, run_monte_carlo
from xrp_futures.backtest.stress_test import default_scenarios, run_stress_scenario
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, run_walk_forward, summarize_walk_forward
from xrp_futures.data import loader
from xrp_futures.strategies import strategy_b_momentum as strat_b
from xrp_futures.strategies import strategy_c_trend_momentum as strat_c
from xrp_futures.strategies.regime import align_regime_to, compute_btc_regime
from xrp_futures.strategies.strategy_a_trend import StrategyAParams

console = Console()

DEFAULT_TIMEFRAMES = ["4h", "6h", "12h", "1d"]
EMA_PAIRS = [(10, 50), (20, 100), (50, 200)]
MOMENTUM_DAYS = [14, 30]
MODES = ["and", "veto"]


def _build_param_grid(timeframe: str) -> list[strat_c.StrategyCParams]:
    grid = []
    for ema_fast, ema_slow in EMA_PAIRS:
        for days in MOMENTUM_DAYS:
            for mode in MODES:
                grid.append(strat_c.StrategyCParams(
                    timeframe=timeframe,
                    trend=StrategyAParams(timeframe=timeframe, ema_fast=ema_fast, ema_slow=ema_slow),
                    momentum=strat_b.StrategyBParams(timeframe=timeframe, lookback_days=days, form="vol_adjusted"),
                    mode=mode,
                    use_btc_regime=True,
                ))
    return grid


def run_for_timeframe(timeframe, xrp_1h, regime_bars_for, funding, config, args):
    xrp_bars = loader.resample_ohlcv(xrp_1h, timeframe)
    regime_bars = regime_bars_for(timeframe)

    def strategy_fn_factory(params):
        def fn(bars):
            regime_series = align_regime_to(bars, regime_bars)
            return strat_c.generate(bars, params, btc_regime=regime_series)
        return fn

    param_grid = _build_param_grid(timeframe)
    console.print(f"[dim]  {timeframe}: {len(xrp_bars)} bars, {len(param_grid)} param candidates x "
                  f"{len(SPEC_WINDOWS)} windows ...[/dim]")
    steps = run_walk_forward(xrp_bars, funding, param_grid, strategy_fn_factory, config, timeframe)
    summary = summarize_walk_forward(steps)
    return xrp_bars, steps, summary, strategy_fn_factory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeframes", nargs="+", default=DEFAULT_TIMEFRAMES)
    parser.add_argument("--start", default=date(2020, 1, 1))
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--risk-pct", type=float, default=0.005)
    args = parser.parse_args()

    console.print("[dim]Loading XRPUSDT + BTCUSDT 1h base data ...[/dim]")
    xrp_1h = loader.load_klines_1h("XRPUSDT", args.start)
    btc_1h = loader.load_klines_1h("BTCUSDT", args.start)
    funding = loader.load_funding("XRPUSDT", args.start)

    regime_cache: dict[str, object] = {}

    def regime_bars_for(timeframe: str):
        if timeframe not in regime_cache:
            btc_bars = loader.resample_ohlcv(btc_1h, timeframe)
            regime_cache[timeframe] = compute_btc_regime(btc_bars, timeframe=timeframe)
        return regime_cache[timeframe]

    config = BacktestConfig(
        initial_equity=args.initial_equity, risk_pct=args.risk_pct,
        use_vol_targeting=False, stop_type="atr", atr_multiplier=2.5,
        trailing_enabled=True, cost_model=BASE,
    )

    summary_table = Table(title="Walk-Forward OOS summary by timeframe", box=box.SIMPLE_HEAVY)
    summary_table.add_column("Timeframe")
    summary_table.add_column("Avg OOS Sharpe", justify="right")
    summary_table.add_column("Min OOS Sharpe", justify="right")
    summary_table.add_column("Avg OOS CAGR", justify="right")
    summary_table.add_column("Worst OOS MaxDD", justify="right")
    summary_table.add_column("All windows +Sharpe", justify="right")

    per_timeframe_results = {}
    console.print(f"[dim]Sweeping {len(args.timeframes)} timeframes x "
                  f"{len(EMA_PAIRS) * len(MOMENTUM_DAYS) * len(MODES)} param candidates each ...[/dim]\n")

    for tf in args.timeframes:
        xrp_bars, steps, summary, factory = run_for_timeframe(tf, xrp_1h, regime_bars_for, funding, config, args)
        per_timeframe_results[tf] = (xrp_bars, steps, summary, factory)
        summary_table.add_row(
            tf,
            f"{summary['avg_test_sharpe']:.2f}",
            f"{summary['min_test_sharpe']:.2f}",
            f"{summary['avg_test_cagr'] * 100:+.1f}%",
            f"{summary['worst_test_max_drawdown'] * 100:.1f}%",
            str(summary["all_windows_positive_sharpe"]),
        )

    console.print()
    console.print(summary_table)

    best_tf = max(per_timeframe_results, key=lambda tf: per_timeframe_results[tf][2]["avg_test_sharpe"])
    xrp_bars, steps, summary, factory = per_timeframe_results[best_tf]
    console.print(f"\n[bold]Best timeframe by avg OOS Sharpe: {best_tf} "
                  f"(avg {summary['avg_test_sharpe']:.2f})[/bold]")

    detail_table = Table(title=f"Walk-Forward detail — {best_tf}", box=box.SIMPLE_HEAVY)
    detail_table.add_column("Window")
    detail_table.add_column("EMA")
    detail_table.add_column("Mom(d)")
    detail_table.add_column("Mode")
    detail_table.add_column("Train Sharpe", justify="right")
    detail_table.add_column("Test Sharpe", justify="right")
    detail_table.add_column("Test CAGR", justify="right")
    detail_table.add_column("Test Trades", justify="right")
    for step in steps:
        p = step.chosen_params
        detail_table.add_row(
            step.window.label, f"{p.trend.ema_fast}/{p.trend.ema_slow}", str(p.momentum.lookback_days),
            p.mode, f"{step.train_score:.2f}", f"{step.test_metrics['sharpe']:.2f}",
            f"{step.test_metrics['cagr'] * 100:+.1f}%", str(step.test_metrics["total_trades"]),
        )
    console.print(detail_table)

    last_step = steps[-1]
    console.print(f"\n[bold]Monte Carlo on {best_tf}'s most recent OOS window "
                  f"({last_step.window.label}), {last_step.test_metrics['total_trades']} trades:[/bold]")
    mc = run_monte_carlo(last_step.test_result, MonteCarloConfig(n_samples=5000, seed=42))
    if mc.n_trades == 0:
        console.print("  [yellow]No trades in this window.[/yellow]")
    else:
        console.print(f"  Probability of ruin: {mc.prob_of_ruin:.1%}  |  "
                       f"MaxDD 5/50/95th pct: {mc.percentiles[5]['max_drawdown']:.1%} / "
                       f"{mc.percentiles[50]['max_drawdown']:.1%} / {mc.percentiles[95]['max_drawdown']:.1%}  |  "
                       f"Final multiple 5/50/95th: {mc.percentiles[5]['final_multiple']:.2f}x / "
                       f"{mc.percentiles[50]['final_multiple']:.2f}x / {mc.percentiles[95]['final_multiple']:.2f}x")

    console.print(f"\n[bold]Stress test — {best_tf}, {last_step.window.label}'s chosen params, full history:[/bold]")
    fn = factory(last_step.chosen_params)
    for scenario in default_scenarios(xrp_bars):
        result = run_stress_scenario(xrp_bars, funding, fn, config, scenario, best_tf)
        status = "[green]survived[/green]" if result.survived else "[bold red]RUINED[/bold red]"
        console.print(f"  {scenario.name:26s} {status}  min_equity=${result.min_equity:,.0f}  "
                       f"net_pnl=${result.metrics['net_pnl_usdt']:,.0f}")

    console.print(f"\n[dim]Grid: {len(EMA_PAIRS)} EMA pairs x {len(MOMENTUM_DAYS)} momentum lookbacks x "
                  f"{len(MODES)} modes = {len(EMA_PAIRS) * len(MOMENTUM_DAYS) * len(MODES)} candidates, "
                  f"across {len(args.timeframes)} timeframes. Still not exhaustive — "
                  f"ATR multiplier, vol target, and risk-per-trade weren't swept here.[/dim]")


if __name__ == "__main__":
    main()
