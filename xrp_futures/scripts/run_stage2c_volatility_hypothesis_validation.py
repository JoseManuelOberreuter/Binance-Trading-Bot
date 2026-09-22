"""
STAGE 2c — validate the "volatility expansion at trend start" hypothesis from
Stage 2b against ALL 20 major (>=30%) OOS swings, not just the 3 named ones.
Pure analysis: no new filter, no parameter change, no strategy modification.

Hypothesis under test: "when a big move starts with already-expanded volatility,
the system needs fewer stops/re-entries to find the main leg; when it starts from
low/normal volatility, the ATR-based Chandelier lags and produces more whipsaws."

No assumption that the initial breakout direction or a capitulation flush is a buy
signal — this only checks whether INITIAL VOLATILITY LEVEL correlates with how many
stops occur before the move is meaningfully captured.

Usage:
  python -m xrp_futures.scripts.run_stage2c_volatility_hypothesis_validation
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from rich.console import Console

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig
from xrp_futures.backtest.swing_capture import identify_major_swings, trades_in_swing
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward
from xrp_futures.data import loader
from xrp_futures.indicators import volatility as vol_mod
from xrp_futures.strategies import strategy_a_trend as strat_a

console = Console()

TIMEFRAME = "12h"
DONCHIAN_WINDOW = 40
CHANDELIER_WINDOW = 5
CHANDELIER_MULTIPLIER = 4.0
RISK_PCT = 0.01
MIN_SWING_MOVE_PCT = 0.30
OOS_START = date(2023, 1, 1)
CAPTURE_THRESHOLD = 0.20

# Fixed, round-number buckets for initial volatility expansion — chosen for this
# grouping step, not fit to make any particular grouping look better.
VOL_BUCKETS = [
    ("baja/normal (<1.2x)", 0.0, 1.2),
    ("moderada (1.2-1.6x)", 1.2, 1.6),
    ("muy expandida (>1.6x)", 1.6, float("inf")),
]

FROZEN_PARAMS = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=DONCHIAN_WINDOW)
FROZEN_CONFIG = BacktestConfig(
    initial_equity=10_000.0, risk_pct=RISK_PCT, use_vol_targeting=False,
    stop_type="chandelier", chandelier_window=CHANDELIER_WINDOW, chandelier_multiplier=CHANDELIER_MULTIPLIER,
    trailing_enabled=True, cost_model=BASE,
)


def strategy_fn(bars):
    return strat_a.generate(bars, FROZEN_PARAMS)


def pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None
    xa, ya = np.array(x), np.array(y)
    if xa.std() == 0 or ya.std() == 0:
        return None
    return float(np.corrcoef(xa, ya)[0, 1])


def analyze_swing(swing, bars: pd.DataFrame, atr_pct: pd.Series, vol_expansion: pd.Series, oos_trades: list) -> dict:
    swing_bars = bars[bars["open_time"] >= swing.start_time]
    if swing_bars.empty:
        return None
    start_idx = swing_bars.index[0]

    atr_at_start = atr_pct.loc[start_idx] if start_idx in atr_pct.index else np.nan
    vol_exp_at_start = vol_expansion.loc[start_idx] if start_idx in vol_expansion.index else np.nan

    first_bar = bars.loc[start_idx]
    upper20 = bars["high"].shift(1).rolling(20).max().loc[start_idx]
    lower20 = bars["low"].shift(1).rolling(20).min().loc[start_idx]
    breakout_dir = "UP" if first_bar["close"] > upper20 else ("DOWN" if first_bar["close"] < lower20 else "-")

    first10 = swing_bars.head(10)
    first20 = swing_bars.head(20)
    avg_vol_exp_10 = vol_expansion.loc[first10.index].mean()
    avg_vol_exp_20 = vol_expansion.loc[first20.index].mean()

    trades = trades_in_swing(oos_trades, swing)
    n_stops_before_capture = None
    days_to_capture = None
    first_capture_found = False
    stops_so_far = 0
    for t in trades:
        price_capture = t.side * (t.exit_price - t.entry_price) / swing.start_price
        capture_of_move = price_capture / (swing.pct_move * swing.direction) if swing.pct_move != 0 else 0
        if capture_of_move > CAPTURE_THRESHOLD:
            n_stops_before_capture = stops_so_far
            days_to_capture = (t.entry_time - swing.start_time).days
            first_capture_found = True
            break
        if t.exit_reason == "stop":
            stops_so_far += 1

    avg_mfe_first3 = np.mean([t.mfe_pct for t in trades[:3]]) if trades else None

    total_captured_price = sum(
        t.side * (t.exit_price - t.entry_price) / swing.start_price for t in trades
    )
    total_capture_of_move = (
        total_captured_price / (swing.pct_move * swing.direction) if swing.pct_move != 0 else None
    )

    return dict(
        start=swing.start_time.date(), end=swing.end_time.date(), pct_move=swing.pct_move,
        direction=swing.direction, atr_at_start=atr_at_start, vol_exp_at_start=vol_exp_at_start,
        breakout_dir=breakout_dir, n_stops_before_capture=n_stops_before_capture,
        capture_found=first_capture_found, days_to_capture=days_to_capture,
        avg_mfe_first3=avg_mfe_first3, avg_vol_exp_10=avg_vol_exp_10, avg_vol_exp_20=avg_vol_exp_20,
        n_trades=len(trades), total_capture_of_move=total_capture_of_move,
    )


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)

    oos_start_ts = pd.Timestamp(OOS_START, tz="UTC")
    xrp_bars_oos = xrp_bars[xrp_bars["open_time"] >= oos_start_ts].reset_index(drop=True)
    swings = identify_major_swings(xrp_bars_oos, min_move_pct=MIN_SWING_MOVE_PCT)
    console.print(f"[dim]{len(swings)} major swings found[/dim]")

    atr14 = vol_mod.atr(xrp_bars["high"], xrp_bars["low"], xrp_bars["close"], window=14)
    atr_pct = atr14 / xrp_bars["close"]
    vol_expansion = atr_pct / atr_pct.rolling(60, min_periods=30).median()

    console.print("[dim]Running walk-forward to get the pooled OOS trade set ...[/dim]")
    steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, FROZEN_CONFIG, TIMEFRAME, SPEC_WINDOWS)
    oos_trades = []
    for step in steps:
        oos_trades.extend(step.test_result.trades)

    rows = [analyze_swing(s, xrp_bars, atr_pct, vol_expansion, oos_trades) for s in swings]
    rows = [r for r in rows if r is not None]

    console.print(f"\n{'Start':<12}{'Dir':<6}{'Move':>8}{'VolExp0':>9}{'Brk':>5}{'Stops<cap':>10}"
                  f"{'Days<cap':>9}{'MFE1-3':>8}{'VolExp10':>9}{'VolExp20':>9}{'Trades':>7}{'TotCap%':>9}")
    for r in rows:
        stops_s = str(r["n_stops_before_capture"]) if r["capture_found"] else "never"
        days_s = str(r["days_to_capture"]) if r["capture_found"] else "n/a"
        mfe_s = f"{r['avg_mfe_first3']*100:.0f}%" if r["avg_mfe_first3"] is not None else "n/a"
        totcap_s = f"{r['total_capture_of_move']*100:+.0f}%" if r["total_capture_of_move"] is not None else "n/a"
        console.print(f"{str(r['start']):<12}{'LONG' if r['direction']==1 else 'SHORT':<6}"
                      f"{r['pct_move']*100:>7.0f}%{r['vol_exp_at_start']:>8.2f}x{r['breakout_dir']:>5}"
                      f"{stops_s:>10}{days_s:>9}{mfe_s:>8}{r['avg_vol_exp_10']:>8.2f}x{r['avg_vol_exp_20']:>8.2f}x"
                      f"{r['n_trades']:>7}{totcap_s:>9}")

    # --- Grouped comparison ---
    console.print(f"\n{'='*100}")
    console.print("[bold]Agrupado por volatilidad inicial:[/bold]\n")
    tradeable = [r for r in rows if r["capture_found"] and not np.isnan(r["vol_exp_at_start"])]
    for label, lo, hi in VOL_BUCKETS:
        bucket = [r for r in tradeable if lo <= r["vol_exp_at_start"] < hi]
        if not bucket:
            console.print(f"  {label}: sin datos (0 movimientos)")
            continue
        avg_stops = np.mean([r["n_stops_before_capture"] for r in bucket])
        avg_days = np.mean([r["days_to_capture"] for r in bucket])
        avg_totcap = np.mean([r["total_capture_of_move"] for r in bucket if r["total_capture_of_move"] is not None])
        console.print(f"  {label}: n={len(bucket)} | stops promedio antes de captura={avg_stops:.1f} | "
                      f"días promedio hasta captura={avg_days:.0f} | captura total promedio del movimiento={avg_totcap*100:+.0f}%")

    not_captured = [r for r in rows if not r["capture_found"]]
    console.print(f"\n  Movimientos que NUNCA tuvieron una operación con >20% de captura: {len(not_captured)}/{len(rows)}")

    # --- Correlations ---
    console.print(f"\n{'='*100}")
    console.print("[bold]Correlaciones (Pearson) — ratio de expansión de volatilidad inicial vs:[/bold]\n")
    vol_vals = [r["vol_exp_at_start"] for r in tradeable]
    stops_vals = [r["n_stops_before_capture"] for r in tradeable]
    days_vals = [r["days_to_capture"] for r in tradeable]
    capture_vals = [r["total_capture_of_move"] for r in tradeable if r["total_capture_of_move"] is not None]
    vol_vals_for_capture = [r["vol_exp_at_start"] for r in tradeable if r["total_capture_of_move"] is not None]

    r_stops = pearson(vol_vals, stops_vals)
    r_days = pearson(vol_vals, days_vals)
    r_capture = pearson(vol_vals_for_capture, capture_vals)

    console.print(f"  vs. número de stops antes de captura: r = {r_stops:.2f}" if r_stops is not None else "  vs. stops: n/a (muestra insuficiente)")
    console.print(f"  vs. días hasta captura >20%:          r = {r_days:.2f}" if r_days is not None else "  vs. días: n/a")
    console.print(f"  vs. % del movimiento finalmente capturado: r = {r_capture:.2f}" if r_capture is not None else "  vs. captura total: n/a")
    console.print(f"\n  (n={len(tradeable)} movimientos con al menos una operación de captura >20%; "
                  f"|r|<0.3 débil, 0.3-0.5 moderada, >0.5 fuerte — regla informal, no un test de significancia formal)")


if __name__ == "__main__":
    main()
