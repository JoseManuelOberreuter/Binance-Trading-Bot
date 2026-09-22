"""
STAGE 3 — structural failure classification. Pure diagnosis: no strategy change,
no new parameters, no new indicators added to the strategy. Donchian 40 /
Chandelier(5, 4x) stay exactly as frozen.

For each of the 19 major (>=30%) OOS swings, classifies the DOMINANT reason the
strategy failed to capture it well, using an OBJECTIVE, reproducible decision tree
(documented inline in `classify`) over quantities already computed elsewhere this
session — not a per-swing subjective judgment call.

Categories (A-G exactly as specified, plus OK for swings that were reasonably
captured — not every swing is a "failure"):
  A) never entered in the correct direction at all
  B) entered correctly, but exited too soon (had a real MFE, gave it back)
  C) whipsawed — entered/exited/re-entered repeatedly on noise
  D) entered too late — most of the move had already happened
  E) too fast for Donchian(40) to ever confirm in time
  F) multi-phase move — strategy flipped out of position and lost a transition
  G) other / doesn't cleanly fit the above
  OK) captured a meaningful share (>=40%) with little friction — not a failure

Usage:
  python -m xrp_futures.scripts.run_stage3_failure_classification
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from rich.console import Console

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig
from xrp_futures.backtest.swing_capture import identify_major_swings, trades_in_swing
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward
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
LATE_ENTRY_THRESHOLD = 0.50     # >=50% of the move already happened before entry -> D
GOOD_CAPTURE_THRESHOLD = 0.40   # >=40% captured -> OK, not a failure
WHIPSAW_STOPS_THRESHOLD = 3     # >=3 stop-outs -> C
PREMATURE_EXIT_MFE_THRESHOLD = 0.30  # a trade reached >=30% MFE but total capture stayed low -> B
FAST_MOVE_DURATION_DAYS = 20    # <=20 days (40 bars on 12h) -> plausible Donchian(40) can't react -> E

FROZEN_PARAMS = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=DONCHIAN_WINDOW)
FROZEN_CONFIG = BacktestConfig(
    initial_equity=10_000.0, risk_pct=RISK_PCT, use_vol_targeting=False,
    stop_type="chandelier", chandelier_window=CHANDELIER_WINDOW, chandelier_multiplier=CHANDELIER_MULTIPLIER,
    trailing_enabled=True, cost_model=BASE,
)


def strategy_fn(bars):
    return strat_a.generate(bars, FROZEN_PARAMS)


def price_at_or_before(bars: pd.DataFrame, ts) -> float:
    return bars[bars["open_time"] <= ts].iloc[-1]["close"]


def classify(swing, trades: list, all_trades_in_window: list, signal_df: pd.DataFrame) -> tuple[str, str]:
    """Returns (category_letter_or_OK, human-readable reason)."""
    window_signal = signal_df[(signal_df["open_time"] >= swing.start_time) & (signal_df["open_time"] <= swing.end_time)]
    duration_days = (swing.end_time - swing.start_time).days

    if not trades:
        ever_signaled_correctly = (window_signal["signal"] == swing.direction).any()
        if not ever_signaled_correctly:
            if duration_days <= FAST_MOVE_DURATION_DAYS:
                return "E", f"Donchian(40) nunca confirmó la dirección correcta; movimiento de solo {duration_days}d, " \
                            f"probablemente insuficiente para que se forme el breakout"
            return "A", f"Donchian(40) nunca confirmó la dirección correcta en {duration_days}d, pese a haber tiempo"
        return "A", "la señal correcta apareció en algún momento pero nunca se abrió una posición (caso raro)"

    first_entry = trades[0].entry_time
    last_exit = trades[-1].exit_time
    price_at_entry = trades[0].entry_price
    price_at_exit = trades[-1].exit_price

    elapsed_at_entry = swing.direction * (price_at_entry - swing.start_price) / (swing.end_price - swing.start_price) \
        if swing.end_price != swing.start_price else 0.0
    remaining_after_exit = swing.direction * (swing.end_price - price_at_exit) / price_at_exit

    price_capture_total = sum(t.side * (t.exit_price - t.entry_price) / swing.start_price for t in trades)
    total_capture = price_capture_total / (swing.pct_move * swing.direction) if swing.pct_move != 0 else 0.0

    n_stops = sum(1 for t in trades if t.exit_reason == "stop")
    max_mfe = max(t.mfe_pct for t in trades)

    if elapsed_at_entry >= LATE_ENTRY_THRESHOLD:
        return "D", f"{elapsed_at_entry*100:.0f}% del movimiento ya había ocurrido antes de la primera entrada correcta"

    if total_capture >= GOOD_CAPTURE_THRESHOLD:
        return "OK", f"capturó {total_capture*100:.0f}% del movimiento con {len(trades)} operación(es) — sin fallo relevante"

    # multi-phase check: did the OPPOSITE-direction signal fire between two of our own trades?
    lost_transition = False
    for i in range(len(trades) - 1):
        between = signal_df[(signal_df["open_time"] > trades[i].exit_time) & (signal_df["open_time"] < trades[i + 1].entry_time)]
        if (between["signal"] == -swing.direction).any():
            lost_transition = True
            break

    if n_stops >= WHIPSAW_STOPS_THRESHOLD:
        return "C", f"{n_stops} stops/reentradas por ruido antes de terminar el movimiento (captura final {total_capture*100:.0f}%)"

    if max_mfe >= PREMATURE_EXIT_MFE_THRESHOLD and total_capture < 0.20:
        return "B", f"llegó a tener {max_mfe*100:.0f}% de MFE no realizado pero terminó capturando solo {total_capture*100:.0f}%"

    if lost_transition:
        return "F", "la señal se invirtió entre operaciones (movimiento multi-fase) y la estrategia perdió la transición"

    return "G", f"ninguna causa objetiva domina claramente (captura {total_capture*100:.0f}%, {len(trades)} operaciones, " \
                f"{n_stops} stops, MFE máx {max_mfe*100:.0f}%)"


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

    signal_df = strategy_fn(xrp_bars)[["open_time", "signal"]]

    console.print("[dim]Running walk-forward to get the pooled OOS trade set ...[/dim]")
    steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, FROZEN_CONFIG, TIMEFRAME, SPEC_WINDOWS)
    oos_trades = []
    for step in steps:
        oos_trades.extend(step.test_result.trades)

    results = []
    for swing in swings:
        trades = trades_in_swing(oos_trades, swing)
        all_in_window = [t for t in oos_trades if swing.start_time <= t.entry_time <= swing.end_time]
        category, reason = classify(swing, trades, all_in_window, signal_df)

        price_capture_total = sum(t.side * (t.exit_price - t.entry_price) / swing.start_price for t in trades)
        total_capture = price_capture_total / (swing.pct_move * swing.direction) if swing.pct_move != 0 and trades else 0.0
        max_mfe = max((t.mfe_pct for t in trades), default=0.0)
        mae_of_best = next((t.mae_pct for t in trades if t.mfe_pct == max_mfe), 0.0) if trades else 0.0

        results.append(dict(
            start=swing.start_time.date(), end=swing.end_time.date(), direction=swing.direction,
            pct_move=swing.pct_move, category=category, reason=reason, n_trades=len(trades),
            total_capture=total_capture, max_mfe=max_mfe, mae_of_best=mae_of_best,
            first_entry=trades[0].entry_time.date() if trades else None,
            first_exit=trades[0].exit_time.date() if trades else None,
        ))

    console.print(f"\n{'Start':<12}{'Dir':<6}{'Move':>7}{'Cat':>5}{'Capture':>9}{'Trades':>7}{'MaxMFE':>8}{'MAEbest':>9}")
    for r in results:
        console.print(f"{str(r['start']):<12}{'LONG' if r['direction']==1 else 'SHORT':<6}{r['pct_move']*100:>6.0f}%"
                      f"{r['category']:>5}{r['total_capture']*100:>8.0f}%{r['n_trades']:>7}"
                      f"{r['max_mfe']*100:>7.0f}%{r['mae_of_best']*100:>8.0f}%")
        console.print(f"             -> {r['reason']}")

    console.print(f"\n{'='*100}")
    console.print("[bold]Tabla resumen — tipo de fallo:[/bold]\n")
    console.print(f"{'Categoría':<45}{'N':>4}{'% del total':>12}{'Pérdida potencial aprox.':>26}")
    labels = {
        "A": "A) Nunca entró en dirección correcta",
        "B": "B) Entró bien, salió demasiado pronto",
        "C": "C) Ruido — múltiples stops/reentradas",
        "D": "D) Entró demasiado tarde",
        "E": "E) Demasiado rápido para Donchian(40)",
        "F": "F) Multi-fase, perdió la transición",
        "G": "G) Otro motivo, no concluyente",
        "OK": "OK) Capturó razonablemente, sin fallo",
    }
    total = len(results)
    for cat, label in labels.items():
        bucket = [r for r in results if r["category"] == cat]
        if not bucket:
            continue
        # "potential loss" proxy: for failures, the gap between max_mfe (what was on the
        # table) and total_capture (what was kept) — 0 for A/E (nothing was ever on the table)
        potential = sum(max(r["max_mfe"] - r["total_capture"], 0) for r in bucket if r["category"] not in ("A", "E"))
        pct_of_total = len(bucket) / total * 100
        console.print(f"{label:<45}{len(bucket):>4}{pct_of_total:>11.0f}%{potential*100:>25.0f}pp*")
    console.print("\n[dim]*pp = suma de (MFE máximo no realizado - captura final) en puntos porcentuales del "
                  "precio, sobre los movimientos de esa categoría — una medida aproximada de \"cuánto quedó sobre "
                  "la mesa\", no un $ real.[/dim]")


if __name__ == "__main__":
    main()
