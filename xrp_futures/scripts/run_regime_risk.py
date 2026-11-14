"""
Regime-scaled risk study: same frozen 8-asset universe, but each entry's risk is
scaled by whether it goes WITH, AGAINST or NEUTRAL to the BTC regime
(strategies/regime.py: close vs EMA200 + EMA slope + 30d momentum), known causally
at the decision bar. Signal, stops and universe are unchanged.

Runs on the SHARED-CAPITAL portfolio engine (backtest/portfolio_engine.py): all 8
symbols trade out of one equity, like the live bot, with the live portfolio
exposure cap (open notional <= max_total_notional_pct x equity; entries over it are
skipped, never downsized). Compared against fixed risk at a similar AVERAGE risk
per trade, so a gain can't come from simply betting more. All figures are
mark-to-market.

Walk-forward: for each test year, the regime variant is picked using ONLY earlier
years (best Calmar = compounded return / worst drawdown), then scored on the test
year it never saw.

Usage:
  python -m xrp_futures.scripts.run_regime_risk
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from xrp_futures.backtest.engine import as_mark_to_market, regime_risk_multiplier
from xrp_futures.backtest.metrics import compute_metrics
from xrp_futures.backtest.portfolio_engine import run_portfolio_backtest
from xrp_futures.config.live import DEFAULT as LIVE
from xrp_futures.data import loader
from xrp_futures.scripts.run_multi_asset_portfolio import FROZEN_CONFIG, TIMEFRAME, UNIVERSE, YEARS, load_symbol
from xrp_futures.strategies.regime import align_regime_to, compute_btc_regime

console = Console()

START_CAPITAL = 1_000.0
CHAIN_YEARS = (2023, 2025)            # recent full years, compounded
WF_TEST_YEARS = [2022, 2023, 2024, 2025, 2026]
# Per-trade risk on total capital: (with regime, neutral, against). Equal values = fixed risk.
VARIANTS = {
    "Fixed 0.6% (live)": (0.006, 0.006, 0.006),
    "Fixed 0.75%": (0.0075, 0.0075, 0.0075),
    "Fixed 0.9%": (0.009, 0.009, 0.009),
    "Regime 1.0/0.5/0.25": (0.010, 0.005, 0.0025),
    "Regime 1.0/0.6/0.3": (0.010, 0.006, 0.003),
    "Regime 1.2/0.6/0.3": (0.012, 0.006, 0.003),
    "Regime 1.2/0.6/0": (0.012, 0.006, 0.0),
    "Regime 1.2/0.8/0.4": (0.012, 0.008, 0.004),
}
CAPS = {f"live cap {LIVE.max_total_notional_pct:g}x total": LIVE.max_total_notional_pct, "no cap": None}


def load_with_regime() -> tuple[dict, dict]:
    btc_klines, _ = loader.read_cached("BTCUSDT")
    btc = compute_btc_regime(loader.resample_ohlcv(btc_klines, TIMEFRAME), TIMEFRAME)
    bars, fundings = {}, {}
    for s in UNIVERSE:
        b, fundings[s] = load_symbol(s)
        b = b.reset_index(drop=True)
        b["regime"] = align_regime_to(b, btc).to_numpy()
        bars[s] = b
    return bars, fundings


def variant_config(risks):
    hi, mid, lo = risks
    base = mid if mid > 0 else hi
    return replace(FROZEN_CONFIG, initial_equity=START_CAPITAL, risk_pct=base,
                   regime_risk_multipliers=None if hi == mid == lo else tuple(r / base for r in risks))


def run_period(bars, fundings, cfg, cap, start: date, end: date):
    lo, hi = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    sliced = {s: b[(b["open_time"] >= lo) & (b["open_time"] < hi)] for s, b in bars.items()}
    sliced = {s: b for s, b in sliced.items() if len(b) > 1}
    return run_portfolio_backtest(sliced, {s: fundings[s] for s in sliced}, cfg, max_total_notional_pct=cap)


def kill_switch_date(mtm: pd.Series, limit: float) -> str:
    dd = mtm / mtm.cummax() - 1
    hit = dd[dd <= -limit]
    return hit.index[0].strftime("%Y-%m") if len(hit) else "never"


def avg_risk(run, bars, cfg) -> float:
    used = []
    for s, trades in run.trades_by_symbol.items():
        regime_at = bars[s].set_index("open_time")["regime"].shift(1)  # decision bar = bar before entry
        for t in trades:
            m = 1.0 if cfg.regime_risk_multipliers is None else \
                regime_risk_multiplier(t.side, regime_at.get(t.entry_time, np.nan), cfg.regime_risk_multipliers)
            used.append(cfg.risk_pct * m)
    return float(np.mean(used)) if used else 0.0


def main() -> None:
    bars, fundings = load_with_regime()
    for cap_name, cap in CAPS.items():
        per_year_all = {}
        t = Table(title=f"$1,000 at the start of each year, 8 assets, shared capital, mark-to-market ({cap_name})")
        for col in ["Variant", "Avg risk", *map(str, YEARS), f"{CHAIN_YEARS[0]}-{CHAIN_YEARS[1]} chained",
                    "2020-26 chained", "Worst DD", f"Kill-switch {LIVE.kill_switch_drawdown:.0%}", "Skipped"]:
            t.add_column(col, justify="right")
        for name, risks in VARIANTS.items():
            cfg = variant_config(risks)
            per_year = {}
            for y in YEARS:
                r = as_mark_to_market(run_period(bars, fundings, cfg, cap, date(y, 1, 1), date(y, 12, 31)).result)
                per_year[y] = (r.equity_curve.iloc[-1], compute_metrics(r, TIMEFRAME)["max_drawdown"])
            per_year_all[name] = per_year
            mid = as_mark_to_market(run_period(bars, fundings, cfg, cap,
                                               date(CHAIN_YEARS[0], 1, 1), date(CHAIN_YEARS[1], 12, 31)).result)
            full_run = run_period(bars, fundings, cfg, cap, date(YEARS[0], 1, 1), date(YEARS[-1], 12, 31))
            full = as_mark_to_market(full_run.result)
            t.add_row(name, f"{avg_risk(full_run, bars, cfg)*100:.2f}%", *[f"{per_year[y][0]:,.0f}" for y in YEARS],
                      f"{mid.equity_curve.iloc[-1]:,.0f}", f"{full.equity_curve.iloc[-1]:,.0f}",
                      f"{compute_metrics(full, TIMEFRAME)['max_drawdown']*100:.0f}%",
                      kill_switch_date(full.equity_curve, LIVE.kill_switch_drawdown), str(full_run.skipped_entries))
        console.print(t)

        regime_names = [v for v in VARIANTS if v.startswith("Regime")]
        wt = Table(title=f"Walk-forward: regime variant picked on earlier years only ({cap_name})")
        for col in ["Test year", "Picked (by Calmar on prior years)", "Picked $", "Fixed 0.6% $", "Fixed 0.75% $"]:
            wt.add_column(col, justify="right")
        chain = {"picked": 1.0, "f06": 1.0, "f075": 1.0}
        for y in WF_TEST_YEARS:
            def calmar(name):
                prior = [per_year_all[name][p] for p in YEARS if p < y]
                growth = np.prod([end / START_CAPITAL for end, _ in prior])
                worst = min(dd for _, dd in prior)
                return (growth - 1) / abs(worst) if worst < 0 else growth
            pick = max(regime_names, key=calmar)
            got = per_year_all[pick][y][0]
            f06, f075 = per_year_all["Fixed 0.6% (live)"][y][0], per_year_all["Fixed 0.75%"][y][0]
            chain["picked"] *= got / START_CAPITAL
            chain["f06"] *= f06 / START_CAPITAL
            chain["f075"] *= f075 / START_CAPITAL
            wt.add_row(str(y), pick, f"{got:,.0f}", f"{f06:,.0f}", f"{f075:,.0f}")
        wt.add_row("Chained", "", *[f"{START_CAPITAL * chain[k]:,.0f}" for k in ("picked", "f06", "f075")])
        console.print(wt)


if __name__ == "__main__":
    main()
