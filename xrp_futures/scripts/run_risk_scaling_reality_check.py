"""
Reality check: what does it actually take, in terms of risk_pct, to approach
a 50% CAGR target on the frozen signal (Donchian 40 / Chandelier 5x4, 12h)?
Signal and stops are UNCHANGED — only risk_pct (position sizing) varies, to
show concretely how CAGR and drawdown scale together, per year, rather than
arguing about it in the abstract.

Not a recommendation of any of these risk_pct values — most are well past
what "extremely strict risk management" (the project's own founding premise)
would tolerate. Purely diagnostic.

Usage:
  python -m xrp_futures.scripts.run_risk_scaling_reality_check
"""

from __future__ import annotations

from datetime import date

from rich.console import Console

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward, summarize_walk_forward
from xrp_futures.data import loader
from xrp_futures.strategies import strategy_a_trend as strat_a

console = Console()

TIMEFRAME = "12h"
DONCHIAN_WINDOW = 40
CHANDELIER_WINDOW = 5
CHANDELIER_MULTIPLIER = 4.0
RISK_PCT_LEVELS = [0.01, 0.02, 0.04, 0.07, 0.10]

FROZEN_PARAMS = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=DONCHIAN_WINDOW)


def strategy_fn(bars):
    return strat_a.generate(bars, FROZEN_PARAMS)


def config_for(risk_pct: float) -> BacktestConfig:
    return BacktestConfig(
        initial_equity=10_000.0, risk_pct=risk_pct, use_vol_targeting=False,
        stop_type="chandelier", chandelier_window=CHANDELIER_WINDOW, chandelier_multiplier=CHANDELIER_MULTIPLIER,
        trailing_enabled=True, cost_model=BASE,
    )


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)

    year_labels = ["2023", "2024", "2025", "2026 (parcial)"]

    console.print(f"\n{'='*110}")
    console.print("[bold]CAGR por año, por nivel de risk_pct[/bold]\n")
    header = f"{'risk_pct':>10}" + "".join(f"{y:>18}" for y in year_labels) + f"{'Promedio':>12}{'Peor MDD':>12}"
    console.print(header)

    for risk_pct in RISK_PCT_LEVELS:
        config = config_for(risk_pct)
        steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, config, TIMEFRAME, SPEC_WINDOWS)
        summary = summarize_walk_forward(steps)
        cagrs = [s.test_metrics["cagr"] for s in steps]
        row = f"{risk_pct*100:>9.0f}%" + "".join(f"{c*100:>17.1f}%" for c in cagrs)
        row += f"{summary['avg_test_cagr']*100:>11.1f}%{summary['worst_test_max_drawdown']*100:>11.1f}%"
        console.print(row)

    console.print("\n[dim]Nota: el motor de backtest no impone un tope de liquidación aparte del stop propio de la "
                  "estrategia -- estos MaxDD son sobre el equity, no consideran si el margen aislado hubiera forzado "
                  "una liquidacion antes de que el stop propio actuara. A partir de cierto risk_pct/leverage implicito "
                  "eso se vuelve una posibilidad real, no solo un numero de drawdown mas grande.[/dim]")


if __name__ == "__main__":
    main()
