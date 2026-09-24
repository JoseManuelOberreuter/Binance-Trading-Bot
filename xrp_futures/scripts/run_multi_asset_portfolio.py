"""
Multi-asset diversification test: the EXACT frozen config (Donchian 40,
Chandelier 5x4, 12h, LONG+SHORT, no filters) run unchanged on a fixed universe
of liquid USDⓈ-M perpetuals, combined as an equal-weight portfolio of isolated
sleeves (backtest/portfolio.py). No per-asset tuning, no asset selection by
backtest result — the universe is fixed up front.

Also extends the evaluation back to 2020-2022 (years the frozen config's OOS
walk-forward never scored), since trend-following needs more than 4 yearly
windows to say anything.

Caveat (survivorship): the universe is "large coins that are still liquid
today", which is chosen with hindsight. It is not selected on strategy
performance, but it is not a point-in-time universe either.

Usage:
  python -m xrp_futures.scripts.run_multi_asset_portfolio
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd
from rich.console import Console
from rich.table import Table

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig, as_mark_to_market, run_backtest
from xrp_futures.backtest.metrics import compute_metrics
from xrp_futures.backtest.portfolio import combine_sleeves, sleeve_return_correlation
from xrp_futures.backtest.walk_forward import slice_result_by_time
from xrp_futures.data import loader
from xrp_futures.strategies import strategy_a_trend as strat_a

console = Console()

TIMEFRAME = "12h"
UNIVERSE = ["XRPUSDT", "ETHUSDT", "BTCUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "LTCUSDT", "LINKUSDT"]
YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
ORIGINAL_OOS_YEARS = [2023, 2024, 2025, 2026]  # the 4 SPEC_WINDOWS test years
# Sleeve risk_pct values. Per-trade risk on TOTAL capital ~= sleeve_risk / N,
# so sleeve_risk = N * 1% (8% for 8 assets) matches the XRP-only baseline's 1%/trade.
SLEEVE_RISKS = [0.08, 0.16]  # = 1% and 2% per trade on total capital with 8 sleeves
START_CAPITAL = 1_000.0

FROZEN_PARAMS = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=40)
FROZEN_CONFIG = BacktestConfig(
    initial_equity=10_000.0, risk_pct=0.01, use_vol_targeting=False,
    stop_type="chandelier", chandelier_window=5, chandelier_multiplier=4.0,
    trailing_enabled=True, cost_model=BASE,
)


def year_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def load_symbol(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    klines, funding = loader.read_cached(symbol)  # cache only: no network on re-runs
    bars = loader.resample_ohlcv(klines, TIMEFRAME)
    return strat_a.generate(bars, FROZEN_PARAMS), funding


def yearly_metrics(result, years) -> dict[int, dict]:
    out = {}
    for y in years:
        sub = slice_result_by_time(result, *year_bounds(y))
        if len(sub.equity_curve) > 30:  # skip years with ~no data (asset not listed yet)
            out[y] = compute_metrics(sub, TIMEFRAME)
    return out


def pct(x: float) -> str:
    return f"{x*100:+.1f}%"


def summarize(per_year: dict[int, dict], years) -> tuple[float, float, float]:
    rows = [per_year[y] for y in years if y in per_year]
    if not rows:
        return float("nan"), float("nan"), float("nan")
    return (sum(r["cagr"] for r in rows) / len(rows),
            sum(r["sharpe"] for r in rows) / len(rows),
            min(r["max_drawdown"] for r in rows))


def main() -> None:
    signals, fundings = {}, {}
    for symbol in UNIVERSE:
        signals[symbol], fundings[symbol] = load_symbol(symbol)
        b = signals[symbol]
        console.print(f"  {symbol:<9} {b['open_time'].min().date()} -> {b['open_time'].max().date()} ({len(b)} bars)")

    # --- Standalone per-asset, baseline sizing (1%/trade) -------------------------------
    # All metrics use mark-to-market equity (what the account actually shows).
    standalone = {s: as_mark_to_market(run_backtest(signals[s], fundings[s], FROZEN_CONFIG)) for s in UNIVERSE}

    t = Table(title="Per asset, standalone (1%/trade, CAGR per calendar year; 2026 partial, annualized)")
    t.add_column("Asset")
    for y in YEARS:
        t.add_column(str(y), justify="right")
    t.add_column("Avg 23-26", justify="right")
    t.add_column("Avg all", justify="right")
    t.add_column("Sharpe all", justify="right")
    t.add_column("Worst MDD", justify="right")
    for s in UNIVERSE:
        py = yearly_metrics(standalone[s], YEARS)
        oos_c, _, _ = summarize(py, ORIGINAL_OOS_YEARS)
        all_c, all_s, all_m = summarize(py, YEARS)
        t.add_row(s, *[pct(py[y]["cagr"]) if y in py else "—" for y in YEARS],
                  pct(oos_c), pct(all_c), f"{all_s:.2f}", pct(all_m))
    console.print(t)

    corr = sleeve_return_correlation(standalone)
    ct = Table(title="Weekly sleeve-return correlation (standalone, 1%/trade)")
    ct.add_column("")
    short = [s.replace("USDT", "") for s in UNIVERSE]
    for name in short:
        ct.add_column(name, justify="right")
    for s, name in zip(UNIVERSE, short):
        ct.add_row(name, *[f"{corr.loc[s, o]:.2f}" for o in UNIVERSE])
    console.print(ct)
    mask = [[i != j for j in range(len(UNIVERSE))] for i in range(len(UNIVERSE))]
    avg_corr = corr.where(pd.DataFrame(mask, index=corr.index, columns=corr.columns)).stack().mean()
    console.print(f"  average pairwise correlation: {avg_corr:.2f}")

    # --- Equal-weight portfolio, sleeve-risk sweep -------------------------------------
    pt = Table(title=f"Equal-weight portfolio of {len(UNIVERSE)} sleeves (CAGR per calendar year)")
    pt.add_column("Variant")
    pt.add_column("Risk/trade on total", justify="right")
    for y in YEARS:
        pt.add_column(str(y), justify="right")
    pt.add_column("Avg 23-26", justify="right")
    pt.add_column("Sharpe 23-26", justify="right")
    pt.add_column("Avg all", justify="right")
    pt.add_column("Sharpe all", justify="right")
    pt.add_column("Worst MDD", justify="right")

    xrp_py = yearly_metrics(standalone["XRPUSDT"], YEARS)
    oc, os_, _ = summarize(xrp_py, ORIGINAL_OOS_YEARS)
    ac, as_, am = summarize(xrp_py, YEARS)
    pt.add_row("XRP only (baseline)", "1.00%", *[pct(xrp_py[y]["cagr"]) if y in xrp_py else "—" for y in YEARS],
               pct(oc), f"{os_:.2f}", pct(ac), f"{as_:.2f}", pct(am))

    n = len(UNIVERSE)
    dollar_tables = []
    for risk in SLEEVE_RISKS:
        cfg = replace(FROZEN_CONFIG, risk_pct=risk)
        sleeves = standalone if risk == FROZEN_CONFIG.risk_pct else {
            s: as_mark_to_market(run_backtest(signals[s], fundings[s], cfg)) for s in UNIVERSE
        }
        py = {}
        for y in YEARS:
            port = combine_sleeves(sleeves, *year_bounds(y))
            py[y] = compute_metrics(port, TIMEFRAME)
        oc, os_, _ = summarize(py, ORIGINAL_OOS_YEARS)
        ac, as_, am = summarize(py, YEARS)
        pt.add_row(f"Portfolio, sleeve risk {risk*100:.0f}%", f"{risk/n*100:.2f}%",
                   *[pct(py[y]["cagr"]) for y in YEARS], pct(oc), f"{os_:.2f}", pct(ac), f"{as_:.2f}", pct(am))
        dollar_tables.append(dollar_table(sleeves, risk / n))
    console.print(pt)
    for dt in dollar_tables:
        console.print(dt)


def dollar_table(sleeves, total_risk: float) -> Table:
    """Year-by-year USD path from START_CAPITAL (actual, not annualized, returns),
    with each asset's contribution and the in-year mark-to-market max drawdown."""
    dt = Table(title=f"USD path from ${START_CAPITAL:,.0f}, {total_risk*100:.0f}% risk/trade on total (mark-to-market)")
    for col in ["Year", "Start", "End", "Gain", "Return", *[s.replace("USDT", "") for s in UNIVERSE],
                "Trades", "Win %", "Max DD"]:
        dt.add_column(col, justify="right")
    cap = START_CAPITAL
    n = len(sleeves)
    for y in YEARS:
        start, end = year_bounds(y)
        port = combine_sleeves(sleeves, start, end, cap)
        final = port.equity_curve.iloc[-1]
        contrib = []
        for s in UNIVERSE:
            eq = slice_result_by_time(sleeves[s], start, end).equity_curve
            contrib.append(f"{cap / n * (eq.iloc[-1] / eq.iloc[0] - 1):+,.0f}" if len(eq) else "—")
        wins = sum(t.net_pnl > 0 for t in port.trades)
        dt.add_row(str(y), f"{cap:,.0f}", f"{final:,.0f}", f"{final - cap:+,.0f}", pct(final / cap - 1), *contrib,
                   str(len(port.trades)), f"{wins / max(len(port.trades), 1):.0%}",
                   pct(compute_metrics(port, TIMEFRAME)["max_drawdown"]))
        cap = final
    dt.add_row("Total", f"{START_CAPITAL:,.0f}", f"{cap:,.0f}", f"{cap - START_CAPITAL:+,.0f}",
               pct(cap / START_CAPITAL - 1), *[""] * len(UNIVERSE), "", "", "")
    return dt


if __name__ == "__main__":
    main()
