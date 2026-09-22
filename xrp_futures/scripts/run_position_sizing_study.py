"""
POSITION SIZING study — signal 100% frozen, only risk-per-trade % varies.

  Frozen signal: XRPUSDT Futures, 12h, Donchian(40), Chandelier(window=5, trailing
  multiplier=4x ATR), LONG+SHORT, no filters. use_vol_targeting=False throughout —
  sizing is risk-per-trade only: notional = equity * risk_pct / stop_distance_pct,
  i.e. driven by the REAL distance to the stop, never by a nominal leverage dial.

  IMPORTANT, exact mechanic (unchanged from every prior run in this phase — nothing
  new is being altered here): BacktestConfig.atr_multiplier (default 2.0, never
  overridden anywhere in this phase) governs the INITIAL stop distance used for
  sizing; chandelier_multiplier=4.0 only governs the TRAILING stop once it engages
  (once a candidate computed from the rolling high/low overtakes the initial stop).
  So the risk distance every position is actually SIZED against is ~2x ATR, not 4x —
  precisely stated here since it directly determines every number below.

Risk-per-trade levels: 0.25%, 0.50%, 0.75%, 1.00%, 1.50%, 2.00% — NOT a search for
the max-CAGR level. The goal is to see how risk/return/ruin SCALE, and find a
defensible zone, not a single "best" number.

For each level (walk-forward OOS, BASE costs unless noted):
  CAGR, Sharpe, MaxDD, worst single year, worst single OOS window, Profit Factor,
  portfolio volatility, P(loss) and MaxDD-percentile distribution via Monte Carlo
  on the pooled OOS trade sequence, effective notional/equity ratio (how much
  account exposure risk_pct actually consumes), and big-move-vs-chop net PnL.

Liquidation risk is computed ONCE (not per level): since entries/stops come from
the frozen signal alone, entry_stop_distance_pct is IDENTICAL across every risk
level — risk_pct changes position SIZE ($), never the % distance to the stop, so
it does not change liquidation safety by itself. What it changes is how much
margin a given position consumes for whatever leverage the exchange account is
actually configured with (a separate, later-phase decision) — reported as the
effective notional/equity ratio instead.

Stress sensitivity per level uses two combined tiers (moderate, severe) rather
than re-decomposing slippage/fees/funding separately at every risk level — that
single-factor decomposition was already done once (run_final_validation.py) and
risk_pct only scales notional, it doesn't interact with per-unit cost rates.

Usage:
  python -m xrp_futures.scripts.run_position_sizing_study
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from rich import box
from rich.console import Console
from rich.table import Table

from xrp_futures.backtest.costs import BASE, CostModel
from xrp_futures.backtest.engine import BacktestConfig, run_backtest
from xrp_futures.backtest.metrics import compute_metrics, per_year_returns, trade_stats
from xrp_futures.backtest.monte_carlo import MonteCarloConfig, run_monte_carlo_from_returns, trade_returns_pct
from xrp_futures.backtest.stress_test import apply_funding_shock
from xrp_futures.backtest.swing_capture import identify_major_swings, split_trades_by_swing_participation
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward
from xrp_futures.data import loader
from xrp_futures.risk.engine import approx_liquidation_distance_pct, liquidation_gate
from xrp_futures.strategies import strategy_a_trend as strat_a

console = Console()

# ---- FROZEN signal — do not vary ----
TIMEFRAME = "12h"
DONCHIAN_WINDOW = 40
CHANDELIER_WINDOW = 5
CHANDELIER_MULTIPLIER = 4.0
MIN_SWING_MOVE_PCT = 0.30

FROZEN_PARAMS = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=DONCHIAN_WINDOW)
RISK_LEVELS = [0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02]

REFERENCE_LEVERAGES = [2, 3, 5]  # exchange leverage DIAL, illustrative only — not what's swept here
MIN_LIQ_BUFFER_PCT = 0.30


@dataclass(frozen=True)
class StressTier:
    name: str
    cost_model: CostModel
    funding_multiplier: float


STRESS_TIERS = [
    StressTier("Baseline", BASE, 1.0),
    StressTier("Moderate stress", CostModel(taker_fee_pct=0.0008, slippage_pct=0.0015), 3.0),
    StressTier("Severe combined", CostModel(taker_fee_pct=0.0015, slippage_pct=0.0035), 5.0),
]


def base_config(risk_pct: float) -> BacktestConfig:
    return BacktestConfig(
        initial_equity=10_000.0, risk_pct=risk_pct, use_vol_targeting=False,
        stop_type="chandelier", chandelier_window=CHANDELIER_WINDOW, chandelier_multiplier=CHANDELIER_MULTIPLIER,
        trailing_enabled=True, cost_model=BASE,
    )


def strategy_fn(bars):
    return strat_a.generate(bars, FROZEN_PARAMS)


def run_scenario(xrp_bars, funding, config, timeframe):
    """One walk-forward + Monte Carlo + full-history pass for a given config/funding."""
    steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, config, timeframe, SPEC_WINDOWS)
    sharpes = [s.test_metrics["sharpe"] for s in steps]
    cagrs = [s.test_metrics["cagr"] for s in steps]
    mdds = [s.test_metrics["max_drawdown"] for s in steps]
    pfs = [s.test_metrics["profit_factor"] for s in steps if s.test_metrics["profit_factor"] not in (0.0, float("inf"))]

    pooled_returns = []
    for step in steps:
        pooled_returns.extend(trade_returns_pct(step.test_result))
    mc = run_monte_carlo_from_returns(pooled_returns, MonteCarloConfig(n_samples=5000, seed=42))

    signal_df = strategy_fn(xrp_bars)
    full_result = run_backtest(signal_df, funding, config)
    full_metrics = compute_metrics(full_result, timeframe)

    return dict(
        steps=steps, sharpe=sum(sharpes) / len(sharpes), cagr=sum(cagrs) / len(cagrs),
        worst_window_cagr=min(cagrs), mdd=min(mdds), pf=(sum(pfs) / len(pfs)) if pfs else 0.0,
        mc=mc, full_result=full_result, full_metrics=full_metrics,
    )


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    base_funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)
    swings = identify_major_swings(xrp_bars, min_move_pct=MIN_SWING_MOVE_PCT)
    console.print(f"[dim]{len(xrp_bars)} bars at {TIMEFRAME} | {len(swings)} major swings (>= "
                  f"{MIN_SWING_MOVE_PCT:.0%})[/dim]")
    console.print(f"[dim]FROZEN signal: Donchian={DONCHIAN_WINDOW}, Chandelier window={CHANDELIER_WINDOW}, "
                  f"trailing mult={CHANDELIER_MULTIPLIER}x (initial stop mult=2.0x, unchanged default)[/dim]\n")

    core_table = Table(title="Core metrics by risk-per-trade level (baseline costs, walk-forward OOS)", box=box.SIMPLE_HEAVY)
    for col in ["Risk%", "Sharpe", "CAGR", "MaxDD", "WorstYear", "WorstWindow", "PF", "Vol%", "AvgNot/Eq", "MaxNot/Eq"]:
        core_table.add_column(col, justify="right" if col != "Risk%" else "left")

    mc_table = Table(title="Monte Carlo on pooled OOS trades, by risk level (baseline costs)", box=box.SIMPLE_HEAVY)
    for col in ["Risk%", "N", "P(loss)", "P(ruin)", "MaxDD p5", "MaxDD p50", "MaxDD p95", "FinalMult p50"]:
        mc_table.add_column(col, justify="right" if col != "Risk%" else "left")

    split_table = Table(title="Big-move vs. chop net PnL by risk level (baseline costs, full history)", box=box.SIMPLE_HEAVY)
    for col in ["Risk%", "BigMove Net$", "Other Net$", "Total Net$"]:
        split_table.add_column(col, justify="right" if col != "Risk%" else "left")

    stress_table = Table(title="Final return under baseline / moderate / severe stress, by risk level", box=box.SIMPLE_HEAVY)
    for col in ["Risk%", "Baseline CAGR", "Baseline Sharpe", "Moderate CAGR", "Moderate Sharpe",
                "Severe CAGR", "Severe Sharpe"]:
        stress_table.add_column(col, justify="right" if col != "Risk%" else "left")

    liq_checked = False

    for risk_pct in RISK_LEVELS:
        label = f"{risk_pct * 100:.2f}%"
        console.print(f"[dim]Running risk={label} ...[/dim]")
        config = base_config(risk_pct)

        baseline = run_scenario(xrp_bars, base_funding, config, TIMEFRAME)
        vol_pct = baseline["full_metrics"]["annualized_volatility"]
        worst_year = min(per_year_returns(baseline["full_result"].equity_curve), default=0.0)

        notionals = [t.notional_usdt for t in baseline["full_result"].trades]
        avg_not_eq = (sum(notionals) / len(notionals) / config.initial_equity) if notionals else 0.0
        max_not_eq = (max(notionals) / config.initial_equity) if notionals else 0.0

        core_table.add_row(
            label, f"{baseline['sharpe']:.2f}", f"{baseline['cagr']*100:+.1f}%", f"{baseline['mdd']*100:.1f}%",
            f"{worst_year*100:+.1f}%", f"{baseline['worst_window_cagr']*100:+.1f}%", f"{baseline['pf']:.2f}",
            f"{vol_pct*100:.1f}%", f"{avg_not_eq:.2f}x", f"{max_not_eq:.2f}x",
        )

        mc = baseline["mc"]
        if mc.n_trades == 0:
            mc_table.add_row(label, "0", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a")
        else:
            prob_loss = float((mc.final_equity_multiples < 1.0).mean())
            mc_table.add_row(
                label, str(mc.n_trades), f"{prob_loss:.1%}", f"{mc.prob_of_ruin:.1%}",
                f"{mc.percentiles[5]['max_drawdown']:.1%}", f"{mc.percentiles[50]['max_drawdown']:.1%}",
                f"{mc.percentiles[95]['max_drawdown']:.1%}", f"{mc.percentiles[50]['final_multiple']:.2f}x",
            )

        big_move, other = split_trades_by_swing_participation(baseline["full_result"].trades, swings)
        big_stats, other_stats = trade_stats(big_move), trade_stats(other)
        split_table.add_row(
            label, f"${big_stats['net_pnl_usdt']:,.0f}", f"${other_stats['net_pnl_usdt']:,.0f}",
            f"${big_stats['net_pnl_usdt'] + other_stats['net_pnl_usdt']:,.0f}",
        )

        if not liq_checked:
            liq_checked = True
            distances = [t.entry_stop_distance_pct for t in baseline["full_result"].trades]
            console.print(f"\n[bold]Liquidation risk (computed once — identical at every risk level, "
                          f"since risk_pct changes $ size, not stop % distance):[/bold]")
            console.print(f"  Entry stop distance: min={min(distances):.1%}  avg={sum(distances)/len(distances):.1%}  "
                          f"max={max(distances):.1%}  (n={len(distances)} trades)")
            for lev in REFERENCE_LEVERAGES:
                liq_dist = approx_liquidation_distance_pct(lev)
                violations = sum(1 for d in distances
                                  if not liquidation_gate(d, lev, MIN_LIQ_BUFFER_PCT))
                console.print(f"  Reference leverage {lev}x: liquidation ~{liq_dist:.1%} away | "
                              f"{violations}/{len(distances)} trades would violate a {MIN_LIQ_BUFFER_PCT:.0%} "
                              f"safety buffer" + (" [green](none)[/green]" if violations == 0 else " [yellow](!!)[/yellow]"))
            console.print()

        stress_results = {"Baseline": baseline}
        for tier in STRESS_TIERS[1:]:
            funding = apply_funding_shock(base_funding, tier.funding_multiplier)
            stressed_config = replace(config, cost_model=tier.cost_model)
            stress_results[tier.name] = run_scenario(xrp_bars, funding, stressed_config, TIMEFRAME)

        stress_table.add_row(
            label,
            f"{stress_results['Baseline']['cagr']*100:+.1f}%", f"{stress_results['Baseline']['sharpe']:.2f}",
            f"{stress_results['Moderate stress']['cagr']*100:+.1f}%", f"{stress_results['Moderate stress']['sharpe']:.2f}",
            f"{stress_results['Severe combined']['cagr']*100:+.1f}%", f"{stress_results['Severe combined']['sharpe']:.2f}",
        )

    console.print()
    console.print(core_table)
    console.print()
    console.print(mc_table)
    console.print()
    console.print(split_table)
    console.print()
    console.print(stress_table)
    console.print("\n[dim]Signal 100% frozen throughout (Donchian 40, Chandelier window=5, trailing mult=4x, "
                  "12h). Only risk_pct varies. AvgNot/Eq and MaxNot/Eq are notional/initial_equity — the "
                  "effective account exposure each level actually uses, distinct from the exchange leverage "
                  "dial. No percentage was chosen for maximum CAGR — this is a scaling map, not a search.[/dim]")


if __name__ == "__main__":
    main()
