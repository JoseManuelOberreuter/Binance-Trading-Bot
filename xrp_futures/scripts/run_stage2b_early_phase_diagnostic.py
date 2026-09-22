"""
STAGE 2b — pure analysis, NOTHING is modified (no new metric, no strategy change):
why did the noisy/whipsaw phase at the start of a big move last 9 trades in 2023
but only 3 in 2024 and 2025? Compares the first ~10-15 bars/trades of each of the
three named swings across every variable requested, then distills a direct
side-by-side comparison.

Everything here reuses indicators already built (trend.py, volatility.py,
trend_strength.py) — Trend Strength itself is computed but NOT altered.

Usage:
  python -m xrp_futures.scripts.run_stage2b_early_phase_diagnostic
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
from xrp_futures.indicators import trend as trend_mod
from xrp_futures.indicators import volatility as vol_mod
from xrp_futures.strategies import strategy_a_trend as strat_a
from xrp_futures.strategies.trend_strength import TrendStrengthParams, compute_trend_strength

console = Console()

TIMEFRAME = "12h"
DONCHIAN_WINDOW = 40
CHANDELIER_WINDOW = 5
CHANDELIER_MULTIPLIER = 4.0
RISK_PCT = 0.01
MIN_SWING_MOVE_PCT = 0.30
OOS_START = date(2023, 1, 1)
TARGET_SWING_STARTS = [date(2023, 1, 6), date(2024, 8, 5), date(2025, 4, 7)]
N_EARLY_BARS = 15
N_EARLY_TRADES = 10
CAPTURE_THRESHOLD = 0.20  # "captured >20% of the move" trigger

FROZEN_PARAMS = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=DONCHIAN_WINDOW)
FROZEN_CONFIG = BacktestConfig(
    initial_equity=10_000.0, risk_pct=RISK_PCT, use_vol_targeting=False,
    stop_type="chandelier", chandelier_window=CHANDELIER_WINDOW, chandelier_multiplier=CHANDELIER_MULTIPLIER,
    trailing_enabled=True, cost_model=BASE,
)


def strategy_fn(bars):
    return strat_a.generate(bars, FROZEN_PARAMS)


def build_full_indicator_frame(bars: pd.DataFrame) -> pd.DataFrame:
    out = compute_trend_strength(bars, TrendStrengthParams(timeframe=TIMEFRAME))
    ema50 = trend_mod.ema(bars["close"], 50)
    upper, lower = trend_mod.donchian_channel(bars["high"], bars["low"], 20)
    atr14 = vol_mod.atr(bars["high"], bars["low"], bars["close"], window=14)
    atr_pct = atr14 / bars["close"]
    out["price_vs_ema_pct"] = (bars["close"] - ema50) / ema50
    out["donchian_upper"] = upper
    out["donchian_lower"] = lower
    out["is_breakout_up"] = bars["close"] > upper
    out["is_breakout_down"] = bars["close"] < lower
    out["atr_pct"] = atr_pct
    out["vol_expansion"] = atr_pct / atr_pct.rolling(60, min_periods=30).median()
    return out


def _row_at_or_before(df: pd.DataFrame, ts) -> pd.Series:
    return df[df["open_time"] <= ts].iloc[-1]


def _price_advance_after(bars: pd.DataFrame, from_time, direction: int, n_bars: int = 5) -> float | None:
    after = bars[bars["open_time"] > from_time].head(n_bars)
    if after.empty:
        return None
    ref = bars[bars["open_time"] <= from_time].iloc[-1]["close"]
    return direction * (after["close"].iloc[-1] - ref) / ref


def analyze_swing(label: str, swing, ind: pd.DataFrame, bars: pd.DataFrame, oos_trades: list) -> dict:
    console.print(f"\n{'='*100}")
    console.print(f"[bold]{label}: {swing.start_time.date()} -> {swing.end_time.date()}, "
                  f"XRP {swing.pct_move*100:+.0f}%[/bold]\n")

    swing_ind = ind[ind["open_time"] >= swing.start_time].reset_index(drop=True)
    early_bars = swing_ind.head(N_EARLY_BARS)

    console.print(f"[bold]First {len(early_bars)} bars from swing start:[/bold]")
    console.print(f"{'Date':<12}{'Close':>10}{'TS':>4}{'ADX':>6}{'EMAslope':>10}{'Px-vsEMA':>10}"
                  f"{'Donch.brk':>11}{'ATR%':>7}{'VolExp':>8}{'Mom30d':>8}")
    for _, r in early_bars.iterrows():
        brk = "UP" if r["is_breakout_up"] else ("DOWN" if r["is_breakout_down"] else "-")
        console.print(f"{r['open_time'].strftime('%Y-%m-%d'):<12}{r['close']:>10.4f}{int(r['trend_strength']):>4}"
                      f"{r['adx']:>6.0f}{r['ema_slope']*100:>9.1f}%{r['price_vs_ema_pct']*100:>9.1f}%"
                      f"{brk:>11}{r['atr_pct']*100:>6.1f}%{r['vol_expansion']:>8.2f}{r['momentum_30d']*100:>7.0f}%")

    trades = trades_in_swing(oos_trades, swing)
    early_trades = trades[:N_EARLY_TRADES]
    console.print(f"\n[bold]First {len(early_trades)} trades:[/bold]")
    console.print(f"{'#':<3}{'Entry':<12}{'Exit':<12}{'Reason':<8}{'MAE':>7}{'MFE':>7}{'Adv.after(5 bars)':>20}")
    stops_before_big_capture = 0
    first_capture_trade_idx = None
    for i, t in enumerate(early_trades, 1):
        price_capture = t.side * (t.exit_price - t.entry_price) / swing.start_price
        capture_of_move = price_capture / (swing.pct_move * swing.direction)
        adv = _price_advance_after(bars, t.exit_time, swing.direction, n_bars=5)
        adv_str = f"{adv*100:+.1f}%" if adv is not None else "n/a"
        console.print(f"{i:<3}{t.entry_time.strftime('%Y-%m-%d'):<12}{t.exit_time.strftime('%Y-%m-%d'):<12}"
                      f"{t.exit_reason:<8}{t.mae_pct*100:>6.1f}%{t.mfe_pct*100:>6.1f}%{adv_str:>20}")
        if t.exit_reason == "stop" and first_capture_trade_idx is None:
            stops_before_big_capture += 1
        if capture_of_move > CAPTURE_THRESHOLD and first_capture_trade_idx is None:
            first_capture_trade_idx = i

    # Bars until Trend Strength first reaches >=3 from swing start
    ts_ge3 = swing_ind[swing_ind["trend_strength"] >= 3]
    bars_to_ts3 = int((ts_ge3.iloc[0]["open_time"] - swing.start_time).total_seconds() / 3600 / 12) if not ts_ge3.empty else None

    days_to_capture = None
    if first_capture_trade_idx is not None:
        days_to_capture = (early_trades[first_capture_trade_idx - 1].entry_time - swing.start_time).days

    early_stop_trades = [t for t in early_trades[:stops_before_big_capture] if t.exit_reason == "stop"]
    avg_mae_early = (sum(t.mae_pct for t in early_stop_trades) / len(early_stop_trades)) if early_stop_trades else None
    advances = [_price_advance_after(bars, t.exit_time, swing.direction, 5) for t in early_stop_trades]
    advances = [a for a in advances if a is not None]
    avg_advance_after_stop = (sum(advances) / len(advances)) if advances else None

    atr_at_start = swing_ind.iloc[0]["atr_pct"]
    vol_exp_at_start = swing_ind.iloc[0]["vol_expansion"]

    summary = dict(
        label=label, n_stops_before_capture=stops_before_big_capture,
        bars_to_ts_ge3=bars_to_ts3, days_to_20pct_capture=days_to_capture,
        avg_mae_early_stops=avg_mae_early, avg_price_advance_after_stop=avg_advance_after_stop,
        atr_pct_at_start=atr_at_start, vol_expansion_at_start=vol_exp_at_start,
        first_trade_was_breakout=bool(early_bars.iloc[0]["is_breakout_up"] or early_bars.iloc[0]["is_breakout_down"]) if len(early_bars) else None,
    )
    return summary


def main() -> None:
    console.print("[dim]Loading XRPUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)

    oos_start_ts = pd.Timestamp(OOS_START, tz="UTC")
    xrp_bars_oos = xrp_bars[xrp_bars["open_time"] >= oos_start_ts].reset_index(drop=True)
    swings = identify_major_swings(xrp_bars_oos, min_move_pct=MIN_SWING_MOVE_PCT)

    console.print("[dim]Computing indicators (unmodified Trend Strength + extra descriptive variables) ...[/dim]")
    ind = build_full_indicator_frame(xrp_bars)

    console.print("[dim]Running walk-forward to get the pooled OOS trade set ...[/dim]")
    steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, strategy_fn, FROZEN_CONFIG, TIMEFRAME, SPEC_WINDOWS)
    oos_trades = []
    for step in steps:
        oos_trades.extend(step.test_result.trades)

    targets = []
    for target_start in TARGET_SWING_STARTS:
        target_ts = pd.Timestamp(target_start, tz="UTC")
        candidates = [s for s in swings if abs((s.start_time - target_ts).days) <= 3]
        if candidates:
            targets.append(max(candidates, key=lambda s: abs(s.pct_move)))

    summaries = []
    labels = ["2023 (+145%)", "2024 (+533%)", "2025 (+101%)"]
    for label, swing in zip(labels, targets):
        summaries.append(analyze_swing(label, swing, ind, xrp_bars, oos_trades))

    console.print(f"\n{'='*100}")
    console.print("[bold]DISTILLED COMPARISON[/bold]\n")
    console.print(f"{'Metric':<38}{'2023 (+145%)':>18}{'2024 (+533%)':>18}{'2025 (+101%)':>18}")
    def fmt(v, pct=False, suffix=""):
        if v is None:
            return "n/a"
        return f"{v*100:+.1f}%{suffix}" if pct else f"{v}{suffix}"
    rows = [
        ("Stops before big capture trade", "n_stops_before_capture", False, ""),
        ("Bars to Trend Strength >= 3", "bars_to_ts_ge3", False, " bars"),
        ("Days to first >20%-capture trade", "days_to_20pct_capture", False, " days"),
        ("Avg MAE of early stopped trades", "avg_mae_early_stops", True, ""),
        ("Avg price advance after a stop (5 bars)", "avg_price_advance_after_stop", True, ""),
        ("ATR% at swing start", "atr_pct_at_start", True, ""),
        ("Vol expansion ratio at swing start", "vol_expansion_at_start", False, "x"),
        ("First bar of swing already a breakout?", "first_trade_was_breakout", False, ""),
    ]
    for metric_label, key, pct, suffix in rows:
        vals = [fmt(s[key], pct, suffix) for s in summaries]
        console.print(f"{metric_label:<38}{vals[0]:>18}{vals[1]:>18}{vals[2]:>18}")


if __name__ == "__main__":
    main()
