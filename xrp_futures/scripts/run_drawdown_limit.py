"""
Drawdown-limit study: which risk setup keeps the 8-asset portfolio's worst
mark-to-market drawdown at or under 30% (the live kill-switch level), and earns
the most while doing it?

Shared-capital portfolio engine with the live exposure cap. Levers, alone and
combined: lower fixed risk, a drawdown throttle (less risk while under water), and
the BTC-regime risk scaling from run_regime_risk.py. The binding number is the
worst drawdown chained from 2020; also reported is the worst drawdown starting
from each year, since a strategy that only stays under 30% from one lucky start
date is no good.

Usage:
  python -m xrp_futures.scripts.run_drawdown_limit
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from rich.console import Console
from rich.table import Table

from xrp_futures.backtest.engine import as_mark_to_market
from xrp_futures.backtest.metrics import compute_metrics
from xrp_futures.backtest.portfolio_engine import run_portfolio_backtest
from xrp_futures.config.live import DEFAULT as LIVE
from xrp_futures.scripts.run_multi_asset_portfolio import TIMEFRAME, YEARS
from xrp_futures.scripts.run_regime_risk import START_CAPITAL, load_with_regime, variant_config

console = Console()

LIMIT = LIVE.kill_switch_drawdown
SOFT = ((0.10, 0.5), (0.20, 0.25))
HARD = ((0.08, 0.5), (0.15, 0.25), (0.22, 0.1))
# name: ((with, neutral, against) risk on total, drawdown steps)
VARIANTS = {
    "Fixed 0.6% (live)": ((0.006,) * 3, None),
    "Fixed 0.5%": ((0.005,) * 3, None),
    "Fixed 0.45%": ((0.0045,) * 3, None),
    "Fixed 0.4%": ((0.004,) * 3, None),
    "Fixed 0.3%": ((0.003,) * 3, None),
    "0.6% + soft throttle": ((0.006,) * 3, SOFT),
    "0.8% + soft throttle": ((0.008,) * 3, SOFT),
    "0.8% + hard throttle": ((0.008,) * 3, HARD),
    "1.0% + hard throttle": ((0.010,) * 3, HARD),
    "Regime 1.0/0.5/0.25 + soft": ((0.010, 0.005, 0.0025), SOFT),
    "Regime 1.0/0.5/0.25 + hard": ((0.010, 0.005, 0.0025), HARD),
    "Regime 0.8/0.4/0.2": ((0.008, 0.004, 0.002), None),
}


def run(bars, fundings, risks, steps, start: date, end: date):
    lo, hi = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    sliced = {s: b[(b["open_time"] >= lo) & (b["open_time"] < hi)] for s, b in bars.items()}
    sliced = {s: b for s, b in sliced.items() if len(b) > 1}
    r = run_portfolio_backtest(sliced, {s: fundings[s] for s in sliced}, variant_config(risks),
                               max_total_notional_pct=LIVE.max_total_notional_pct, drawdown_risk_steps=steps)
    return as_mark_to_market(r.result)


def max_dd(result) -> float:
    return compute_metrics(result, TIMEFRAME)["max_drawdown"]


def main() -> None:
    bars, fundings = load_with_regime()
    end = date(YEARS[-1], 12, 31)
    t = Table(title=f"$1,000 at the start of each year, 8 assets, shared capital + live cap, mark-to-market "
                    f"(target: worst drawdown <= {LIMIT:.0%})")
    for col in ["Variant", *map(str, YEARS), "2023-25 chained", "2020-26 chained", "Worst DD from 2020",
                "Worst DD from any year", f"<= {LIMIT:.0%}?"]:
        t.add_column(col, justify="right")
    for name, (risks, steps) in VARIANTS.items():
        per_year = [run(bars, fundings, risks, steps, date(y, 1, 1), date(y, 12, 31)).equity_curve.iloc[-1]
                    for y in YEARS]
        mid = run(bars, fundings, risks, steps, date(2023, 1, 1), date(2025, 12, 31)).equity_curve.iloc[-1]
        from_start = {y: run(bars, fundings, risks, steps, date(y, 1, 1), end) for y in YEARS[:-1]}
        full = from_start[YEARS[0]]
        worst_any = min(max_dd(r) for r in from_start.values())
        ok = "yes" if worst_any >= -LIMIT else "no"
        t.add_row(name, *[f"{v:,.0f}" for v in per_year], f"{mid:,.0f}", f"{full.equity_curve.iloc[-1]:,.0f}",
                  f"{max_dd(full)*100:.0f}%", f"{worst_any*100:.0f}%", ok)
    console.print(t)


if __name__ == "__main__":
    main()
