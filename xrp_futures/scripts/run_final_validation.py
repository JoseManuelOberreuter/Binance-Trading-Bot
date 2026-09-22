"""
FINAL VALIDATION of the frozen configuration — no more parameter search from here.

  Timeframe: 12h | Donchian: 40 | Chandelier window: 5 | Chandelier multiplier: 4x ATR
  LONG + SHORT | no BTC/ADX/volatility filter

Question this answers: does this EXACT, already-chosen configuration keep a
reasonable statistical edge once realistic variations in cost, execution and trade
SEQUENCE are introduced — not "can we find something better" (that phase is over).

For each of 8 scenarios (baseline + slippage/fees/funding stress at two severities
+ a combined worst case), computes, all under the SAME walk-forward OOS discipline
used throughout this study:
  - Sharpe, CAGR, MaxDD, Profit Factor (OOS, walk-forward)
  - Monte Carlo over the POOLED OOS trade sequence (all 4 windows' trades combined,
    each trade's % return computed against its own window's equity path): MaxDD
    percentiles, probability of ending with a loss, probability of ruin
  - Big-move vs. chop/noise net PnL split (full history), to see whether the
    "small losses while waiting, big gains on real moves" asymmetry survives stress

Usage:
  python -m xrp_futures.scripts.run_final_validation
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from rich import box
from rich.console import Console
from rich.table import Table

from xrp_futures.backtest.costs import BASE, CostModel
from xrp_futures.backtest.engine import BacktestConfig, run_backtest
from xrp_futures.backtest.metrics import compute_metrics, trade_stats
from xrp_futures.backtest.monte_carlo import MonteCarloConfig, run_monte_carlo_from_returns, trade_returns_pct
from xrp_futures.backtest.stress_test import apply_funding_shock
from xrp_futures.backtest.swing_capture import identify_major_swings, split_trades_by_swing_participation
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward
from xrp_futures.data import loader
from xrp_futures.strategies import strategy_a_trend as strat_a

console = Console()

# ---- FROZEN configuration — do not vary these ----
TIMEFRAME = "12h"
DONCHIAN_WINDOW = 40
CHANDELIER_WINDOW = 5
CHANDELIER_MULTIPLIER = 4.0
MIN_SWING_MOVE_PCT = 0.30

FROZEN_PARAMS = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=DONCHIAN_WINDOW)
FROZEN_CONFIG = BacktestConfig(
    initial_equity=10_000.0, risk_pct=0.005, use_vol_targeting=False,
    stop_type="chandelier", chandelier_window=CHANDELIER_WINDOW, chandelier_multiplier=CHANDELIER_MULTIPLIER,
    trailing_enabled=True, cost_model=BASE,
)


@dataclass(frozen=True)
class CostScenario:
    name: str
    cost_model: CostModel
    funding_multiplier: float = 1.0


SCENARIOS = [
    CostScenario("Baseline", BASE, 1.0),
    CostScenario("Slippage 3x (0.15%)", CostModel(taker_fee_pct=BASE.taker_fee_pct, slippage_pct=0.0015), 1.0),
    CostScenario("Slippage 7x (0.35%)", CostModel(taker_fee_pct=BASE.taker_fee_pct, slippage_pct=0.0035), 1.0),
    CostScenario("Fees 2x (0.08%)", CostModel(taker_fee_pct=0.0008, slippage_pct=BASE.slippage_pct), 1.0),
    CostScenario("Fees 3.75x (0.15%)", CostModel(taker_fee_pct=0.0015, slippage_pct=BASE.slippage_pct), 1.0),
    CostScenario("Funding x3 adverse", BASE, 3.0),
    CostScenario("Funding x5 adverse", BASE, 5.0),
    CostScenario("Combined worst-case", CostModel(taker_fee_pct=0.0015, slippage_pct=0.0035), 5.0),
]


def strategy_fn(bars):
    return strat_a.generate(bars, FROZEN_PARAMS)


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    base_funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)
    swings = identify_major_swings(xrp_bars, min_move_pct=MIN_SWING_MOVE_PCT)
    console.print(f"[dim]{len(xrp_bars)} bars at {TIMEFRAME} | {len(swings)} major swings (>= "
                  f"{MIN_SWING_MOVE_PCT:.0%})[/dim]")
    console.print(f"[dim]FROZEN: Donchian={DONCHIAN_WINDOW}, Chandelier window={CHANDELIER_WINDOW}, "
                  f"multiplier={CHANDELIER_MULTIPLIER}x, timeframe={TIMEFRAME}[/dim]\n")

    wf_table = Table(title="OOS metrics under stress (walk-forward, frozen strategy params)", box=box.SIMPLE_HEAVY)
    for col in ["Scenario", "Sharpe", "CAGR", "MaxDD", "PF"]:
        wf_table.add_column(col, justify="right" if col != "Scenario" else "left")

    mc_table = Table(title="Monte Carlo on pooled OOS trades (4 walk-forward windows combined)", box=box.SIMPLE_HEAVY)
    for col in ["Scenario", "N trades", "P(loss)", "P(ruin)", "MaxDD p5", "MaxDD p50", "MaxDD p95",
                "FinalMult p5", "FinalMult p50", "FinalMult p95"]:
        mc_table.add_column(col, justify="right" if col != "Scenario" else "left")

    split_table = Table(title="Big-move vs. chop net PnL (full history)", box=box.SIMPLE_HEAVY)
    for col in ["Scenario", "BigMove N", "BigMove Net$", "Other N", "Other Net$", "Total Net$"]:
        split_table.add_column(col, justify="right" if col != "Scenario" else "left")

    for scenario in SCENARIOS:
        console.print(f"[dim]Running {scenario.name} ...[/dim]")
        config = replace(FROZEN_CONFIG, cost_model=scenario.cost_model)
        funding = (apply_funding_shock(base_funding, scenario.funding_multiplier)
                   if scenario.funding_multiplier != 1.0 else base_funding)

        steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, config, TIMEFRAME, SPEC_WINDOWS)
        sharpes = [s.test_metrics["sharpe"] for s in steps]
        cagrs = [s.test_metrics["cagr"] for s in steps]
        mdds = [s.test_metrics["max_drawdown"] for s in steps]
        pfs = [s.test_metrics["profit_factor"] for s in steps if s.test_metrics["profit_factor"] not in (0.0, float("inf"))]
        wf_table.add_row(
            scenario.name, f"{sum(sharpes)/len(sharpes):.2f}", f"{sum(cagrs)/len(cagrs)*100:+.1f}%",
            f"{min(mdds)*100:.1f}%", f"{(sum(pfs)/len(pfs)) if pfs else 0.0:.2f}",
        )

        pooled_returns = []
        for step in steps:
            pooled_returns.extend(trade_returns_pct(step.test_result))
        mc = run_monte_carlo_from_returns(pooled_returns, MonteCarloConfig(n_samples=5000, seed=42))
        if mc.n_trades == 0:
            mc_table.add_row(scenario.name, "0", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a")
        else:
            prob_loss = float((mc.final_equity_multiples < 1.0).mean())
            mc_table.add_row(
                scenario.name, str(mc.n_trades), f"{prob_loss:.1%}", f"{mc.prob_of_ruin:.1%}",
                f"{mc.percentiles[5]['max_drawdown']:.1%}", f"{mc.percentiles[50]['max_drawdown']:.1%}",
                f"{mc.percentiles[95]['max_drawdown']:.1%}",
                f"{mc.percentiles[5]['final_multiple']:.2f}x", f"{mc.percentiles[50]['final_multiple']:.2f}x",
                f"{mc.percentiles[95]['final_multiple']:.2f}x",
            )

        signal_df = strategy_fn(xrp_bars)
        full_result = run_backtest(signal_df, funding, config)
        big_move_trades, other_trades = split_trades_by_swing_participation(full_result.trades, swings)
        big_stats, other_stats = trade_stats(big_move_trades), trade_stats(other_trades)
        split_table.add_row(
            scenario.name, str(big_stats["total_trades"]), f"${big_stats['net_pnl_usdt']:,.0f}",
            str(other_stats["total_trades"]), f"${other_stats['net_pnl_usdt']:,.0f}",
            f"${big_stats['net_pnl_usdt'] + other_stats['net_pnl_usdt']:,.0f}",
        )

    console.print()
    console.print(wf_table)
    console.print()
    console.print(mc_table)
    console.print()
    console.print(split_table)
    console.print("\n[dim]Frozen strategy parameters unchanged across every row above — only cost/funding "
                  "assumptions vary. P(loss)/P(ruin)/MaxDD percentiles come from resampling the actual pooled "
                  "OOS trade sequence (walk-forward test windows only), not the full-history run.[/dim]")


if __name__ == "__main__":
    main()
