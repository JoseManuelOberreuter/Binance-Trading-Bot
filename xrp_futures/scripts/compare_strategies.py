"""
Run Buy&Hold vs Strategy A/B/C/D (§18/§23) over real cached XRPUSDT data with a
SINGLE default parameter set each (not tuned/optimized — see design_decisions.md
for why no parameter here should be treated as final) and print/save a comparison.

This is a starting point for judging §33's acceptance bar, not a final verdict:
run walk_forward/monte_carlo/stress_test on whichever candidate looks promising
before drawing conclusions.

Usage:
  python -m xrp_futures.scripts.compare_strategies
  python -m xrp_futures.scripts.compare_strategies --timeframe 6h --start 2020-01-01
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig, run_backtest
from xrp_futures.backtest.metrics import compute_metrics
from xrp_futures.data import loader
from xrp_futures.reports.report import print_comparison_table, save_comparison_json
from xrp_futures.risk.engine import RiskEngine, RiskEngineConfig
from xrp_futures.strategies import strategy_a_trend as strat_a
from xrp_futures.strategies import strategy_b_momentum as strat_b
from xrp_futures.strategies import strategy_c_trend_momentum as strat_c
from xrp_futures.strategies import strategy_d_full as strat_d
from xrp_futures.strategies.benchmark import buy_and_hold_result
from xrp_futures.strategies.regime import align_regime_to, compute_btc_regime


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeframe", default="6h", choices=["1h", "4h", "6h", "12h", "1d"])
    parser.add_argument("--start", type=_parse_date, default=date(2020, 1, 1))
    parser.add_argument("--end", type=_parse_date, default=date.today())
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--risk-pct", type=float, default=0.005)
    args = parser.parse_args()

    print(f"Loading XRPUSDT + BTCUSDT {args.timeframe} data [{args.start} .. {args.end}] ...")
    xrp_1h = loader.load_klines_1h("XRPUSDT", args.start, args.end)
    btc_1h = loader.load_klines_1h("BTCUSDT", args.start, args.end)
    funding = loader.load_funding("XRPUSDT", args.start, args.end)
    xrp_bars = loader.resample_ohlcv(xrp_1h, args.timeframe)
    btc_bars = loader.resample_ohlcv(btc_1h, args.timeframe)
    print(f"  {len(xrp_bars)} XRPUSDT bars, {len(funding)} funding settlements")

    regime_bars = compute_btc_regime(btc_bars, timeframe=args.timeframe)
    btc_regime = align_regime_to(xrp_bars, regime_bars)

    ablation_config = BacktestConfig(
        initial_equity=args.initial_equity, risk_pct=args.risk_pct,
        use_vol_targeting=False, stop_type="atr", atr_multiplier=2.5,
        trailing_enabled=True, cost_model=BASE,
    )

    results: dict[str, dict] = {}

    print("Running Buy & Hold ...")
    bh_result = buy_and_hold_result(xrp_bars, args.initial_equity)
    results["Buy & Hold"] = compute_metrics(bh_result, args.timeframe)

    print("Running Strategy A (trend only) ...")
    a_params = strat_a.StrategyAParams(timeframe=args.timeframe)
    a_signal = strat_a.generate(xrp_bars, a_params)
    a_result = run_backtest(a_signal, funding, ablation_config)
    results["A: Trend"] = compute_metrics(a_result, args.timeframe)

    print("Running Strategy B (momentum only) ...")
    b_params = strat_b.StrategyBParams(timeframe=args.timeframe)
    b_signal = strat_b.generate(xrp_bars, b_params)
    b_result = run_backtest(b_signal, funding, ablation_config)
    results["B: Momentum"] = compute_metrics(b_result, args.timeframe)

    print("Running Strategy C (trend + momentum) ...")
    c_params = strat_c.StrategyCParams(timeframe=args.timeframe)
    c_signal = strat_c.generate(xrp_bars, c_params)
    c_result = run_backtest(c_signal, funding, ablation_config)
    results["C: Trend+Momentum"] = compute_metrics(c_result, args.timeframe)

    print("Running Strategy C + BTC regime filter ...")
    c_regime_params = strat_c.StrategyCParams(timeframe=args.timeframe, use_btc_regime=True)
    c_regime_signal = strat_c.generate(xrp_bars, c_regime_params, btc_regime=btc_regime)
    c_regime_result = run_backtest(c_regime_signal, funding, ablation_config)
    results["C + BTC regime"] = compute_metrics(c_regime_result, args.timeframe)

    print("Running Strategy D (trend + momentum + vol targeting + risk engine) ...")
    d_config = strat_d.recommended_backtest_config(
        initial_equity=args.initial_equity, risk_pct=args.risk_pct, cost_model=BASE,
    )
    d_signal = strat_d.generate(xrp_bars, c_params)  # same entry signal as C
    d_risk_engine = RiskEngine(RiskEngineConfig())
    d_result = run_backtest(d_signal, funding, d_config, risk_engine=d_risk_engine)
    results["D: Full"] = compute_metrics(d_result, args.timeframe)

    print()
    print_comparison_table(results, title=f"XRPUSDT {args.timeframe} — {args.start} to {args.end}")
    out_path = save_comparison_json(results)
    print(f"\nSaved to {out_path}")
    print("\nReminder: single unoptimized parameter set per strategy — not a final verdict. "
          "Run walk_forward / monte_carlo / stress_test on any promising candidate before trusting it.")


if __name__ == "__main__":
    main()
