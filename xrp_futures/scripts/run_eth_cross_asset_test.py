"""
Cross-asset robustness check: does the EXACT frozen config (Donchian 40,
Chandelier 5x4, 12h, risk 1%/trade) — tuned/validated on XRPUSDT — hold up at
all on a different instrument (ETHUSDT), with zero parameter changes?

Not a search for ETH-specific parameters. If this looks bad, that's a real
and useful finding (config may be XRP-idiosyncratic), not something to fix
by retuning for ETH.

Usage:
  python -m xrp_futures.scripts.run_eth_cross_asset_test
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from rich.console import Console

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig
from xrp_futures.backtest.swing_capture import detailed_swing_report, identify_major_swings
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward, summarize_walk_forward
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


def run_for_symbol(symbol: str) -> None:
    console.print(f"\n{'='*100}")
    console.print(f"[bold]{symbol}[/bold]")
    start = date(2020, 1, 1)
    bars_1h = loader.load_klines_1h(symbol, start)
    funding = loader.load_funding(symbol, start)
    bars = loader.resample_ohlcv(bars_1h, TIMEFRAME)
    console.print(f"  Data available: {bars['open_time'].min().date()} -> {bars['open_time'].max().date()} "
                  f"({len(bars)} bars at {TIMEFRAME})")

    steps = evaluate_fixed_config_walk_forward(bars, funding, strategy_fn, FROZEN_CONFIG, TIMEFRAME, SPEC_WINDOWS)
    summary = summarize_walk_forward(steps)

    console.print(f"  n_windows={summary['n_windows']}  avg_sharpe={summary['avg_test_sharpe']:.2f}  "
                  f"min_sharpe={summary['min_test_sharpe']:.2f}  avg_cagr={summary['avg_test_cagr']*100:.1f}%  "
                  f"worst_mdd={summary['worst_test_max_drawdown']*100:.1f}%  "
                  f"all_windows_positive_sharpe={summary['all_windows_positive_sharpe']}")

    console.print("\n  Per-window:")
    for s in steps:
        console.print(f"    {s.window.label:<18} sharpe={s.test_metrics['sharpe']:>6.2f}  "
                       f"cagr={s.test_metrics['cagr']*100:>6.1f}%  mdd={s.test_metrics['max_drawdown']*100:>6.1f}%  "
                       f"trades={len(s.test_result.trades)}")

    oos_trades = []
    for step in steps:
        oos_trades.extend(step.test_result.trades)

    oos_start_ts = pd.Timestamp(OOS_START, tz="UTC")
    bars_oos = bars[bars["open_time"] >= oos_start_ts].reset_index(drop=True)
    swings = identify_major_swings(bars_oos, min_move_pct=MIN_SWING_MOVE_PCT)
    report = detailed_swing_report(swings, oos_trades)
    n_captured = sum(1 for r in report if r["n_trades"] > 0)
    avg_capture = sum(r["capture_of_move_pct"] for r in report if r["n_trades"] > 0) / n_captured if n_captured else 0.0
    console.print(f"\n  {len(swings)} major (>=30%) OOS swings; {n_captured} had at least one matching-direction trade; "
                  f"avg capture when traded = {avg_capture*100:.0f}%")


def main() -> None:
    for symbol in ["XRPUSDT", "ETHUSDT"]:
        run_for_symbol(symbol)


if __name__ == "__main__":
    main()
