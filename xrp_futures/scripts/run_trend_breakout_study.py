"""
Trend Following / Breakout study — a deliberately different evaluation philosophy
from the earlier Sharpe-driven sweeps: not "does this win consistently or handle
sideways markets well", but "does it lose small while waiting, and capture a real
share of XRP's big moves when they happen". Point of comparison: the current best
Sharpe-oriented variant (12h, EMA10/50, momentum 14d, veto mode, BTC regime).

Candidates (coarse on purpose — NOT a fine parameter search, per instructions):
  - baseline_prior_best   : the reference variant from the previous round
  - donchian_10/20/40/60  : pure Donchian breakout, no filter, Chandelier trailing stop
  - donchian20_adx_filter : Donchian(20) gated by XRP's own ADX trend regime
  - donchian20_vol_filter : Donchian(20) gated by XRP's own volatility regime
  - donchian20_btc_filter : Donchian(20) gated by the BTC regime

All candidates use the SAME sizing (use_vol_targeting=False, fixed risk_pct) and the
SAME Chandelier trailing stop so the comparison isolates ENTRY LOGIC and FILTERS —
not sizing or stop-style confounds (a lesson from an earlier run where Strategy D's
stop type, not its sizing, explained a misleading result).

For each candidate: walk-forward OOS metrics (Sharpe/CAGR/MaxDD/PF/avg win-loss/
duration/MFE/top-N contribution, LONG vs SHORT split) via evaluate_fixed_config_
walk_forward, PLUS a full-history run for the swing-capture analysis (needs the
whole series to find XRP's major moves) — clearly reported as a separate, descriptive
analysis of one fixed candidate, not another optimization pass.

Usage:
  python -m xrp_futures.scripts.run_trend_breakout_study
"""

from __future__ import annotations

from datetime import date

from rich import box
from rich.console import Console
from rich.table import Table

from xrp_futures.backtest.costs import BASE
from xrp_futures.backtest.engine import BacktestConfig, run_backtest
from xrp_futures.backtest.metrics import compute_metrics
from xrp_futures.backtest.swing_capture import identify_major_swings, swing_capture_analysis
from xrp_futures.backtest.walk_forward import SPEC_WINDOWS, evaluate_fixed_config_walk_forward
from xrp_futures.data import loader
from xrp_futures.strategies import strategy_a_trend as strat_a
from xrp_futures.strategies import strategy_b_momentum as strat_b
from xrp_futures.strategies import strategy_c_trend_momentum as strat_c
from xrp_futures.strategies.regime import BULL, align_regime_to, compute_btc_regime
from xrp_futures.strategies.xrp_regime import (
    RANGING, TRENDING, apply_regime_filter, compute_adx_trend_regime, compute_volatility_regime,
)

console = Console()
TIMEFRAME = "12h"
DONCHIAN_WINDOWS = [10, 20, 40, 60]
REFERENCE_DONCHIAN = 20  # classic Turtle-style default — a fixed choice, not a search result
MIN_SWING_MOVE_PCT = 0.30

CHANDELIER_CONFIG = BacktestConfig(
    initial_equity=10_000.0, risk_pct=0.005, use_vol_targeting=False,
    stop_type="chandelier", chandelier_window=10, chandelier_multiplier=3.0,
    trailing_enabled=True, cost_model=BASE,
)


def build_candidates(xrp_bars, btc_regime_bars):
    """Returns {name: strategy_fn} where strategy_fn(bars) -> signal_df."""
    candidates = {}

    # Reference: prior best Sharpe-oriented variant (12h, EMA10/50, mom14, veto, BTC regime)
    prior_best_params = strat_c.StrategyCParams(
        timeframe=TIMEFRAME,
        trend=strat_a.StrategyAParams(timeframe=TIMEFRAME, ema_fast=10, ema_slow=50),
        momentum=strat_b.StrategyBParams(timeframe=TIMEFRAME, lookback_days=14, form="vol_adjusted"),
        mode="veto", use_btc_regime=True,
    )

    def baseline_fn(bars):
        regime = align_regime_to(bars, btc_regime_bars)
        return strat_c.generate(bars, prior_best_params, btc_regime=regime)
    candidates["baseline_prior_best"] = baseline_fn

    for window in DONCHIAN_WINDOWS:
        params = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=window)

        def fn(bars, params=params):
            return strat_a.generate(bars, params)
        candidates[f"donchian_{window}"] = fn

    ref_params = strat_a.StrategyAParams(timeframe=TIMEFRAME, method="donchian", donchian_window=REFERENCE_DONCHIAN)

    def donchian_adx_fn(bars):
        signal_df = strat_a.generate(bars, ref_params)
        adx_regime = compute_adx_trend_regime(bars, adx_window=14, adx_threshold=25.0)
        signal_df["signal"] = apply_regime_filter(signal_df["signal"], adx_regime, TRENDING)
        return signal_df
    candidates["donchian20_adx_filter"] = donchian_adx_fn

    def donchian_vol_fn(bars):
        signal_df = strat_a.generate(bars, ref_params)
        vol_regime = compute_volatility_regime(bars, timeframe=TIMEFRAME, vol_window=30, median_window=90)
        signal_df["signal"] = apply_regime_filter(signal_df["signal"], vol_regime, 1)  # HIGH_VOL
        return signal_df
    candidates["donchian20_vol_filter"] = donchian_vol_fn

    def donchian_btc_fn(bars):
        signal_df = strat_a.generate(bars, ref_params)
        regime = align_regime_to(bars, btc_regime_bars)
        signal = signal_df["signal"].copy()
        signal[(signal == -1) & (regime.values == BULL)] = 0  # only block SHORTs during a BTC bull, mirrors strat_c's gate
        signal_df["signal"] = signal
        return signal_df
    candidates["donchian20_btc_filter"] = donchian_btc_fn

    return candidates


def summarize_walk_forward_extended(steps) -> dict:
    sharpes = [s.test_metrics["sharpe"] for s in steps]
    cagrs = [s.test_metrics["cagr"] for s in steps]
    mdds = [s.test_metrics["max_drawdown"] for s in steps]
    pfs = [s.test_metrics["profit_factor"] for s in steps if s.test_metrics["profit_factor"] not in (0.0, float("inf"))]
    total_trades = sum(s.test_metrics["total_trades"] for s in steps)
    avg_win = [s.test_metrics["avg_win_usdt"] for s in steps if s.test_metrics["total_trades"] > 0]
    avg_loss = [s.test_metrics["avg_loss_usdt"] for s in steps if s.test_metrics["total_trades"] > 0]
    avg_dur = [s.test_metrics["avg_duration_hours"] for s in steps if s.test_metrics["total_trades"] > 0]
    avg_mfe = [s.test_metrics["avg_mfe_pct"] for s in steps if s.test_metrics["total_trades"] > 0]
    return dict(
        avg_sharpe=sum(sharpes) / len(sharpes),
        min_sharpe=min(sharpes),
        avg_cagr=sum(cagrs) / len(cagrs),
        worst_mdd=min(mdds),
        avg_pf=(sum(pfs) / len(pfs)) if pfs else 0.0,
        total_trades=total_trades,
        avg_win_usdt=(sum(avg_win) / len(avg_win)) if avg_win else 0.0,
        avg_loss_usdt=(sum(avg_loss) / len(avg_loss)) if avg_loss else 0.0,
        avg_duration_hours=(sum(avg_dur) / len(avg_dur)) if avg_dur else 0.0,
        avg_mfe_pct=(sum(avg_mfe) / len(avg_mfe)) if avg_mfe else 0.0,
    )


def main() -> None:
    console.print("[dim]Loading XRPUSDT + BTCUSDT data ...[/dim]")
    start = date(2020, 1, 1)
    xrp_1h = loader.load_klines_1h("XRPUSDT", start)
    btc_1h = loader.load_klines_1h("BTCUSDT", start)
    funding = loader.load_funding("XRPUSDT", start)
    xrp_bars = loader.resample_ohlcv(xrp_1h, TIMEFRAME)
    btc_bars = loader.resample_ohlcv(btc_1h, TIMEFRAME)
    btc_regime_bars = compute_btc_regime(btc_bars, timeframe=TIMEFRAME)

    candidates = build_candidates(xrp_bars, btc_regime_bars)
    swings = identify_major_swings(xrp_bars, min_move_pct=MIN_SWING_MOVE_PCT)
    console.print(f"[dim]{len(xrp_bars)} bars at {TIMEFRAME} | {len(swings)} major XRP swings "
                  f"(>= {MIN_SWING_MOVE_PCT:.0%}) identified in full history[/dim]\n")

    wf_table = Table(title=f"Walk-Forward OOS summary ({TIMEFRAME}, trend/breakout philosophy)", box=box.SIMPLE_HEAVY)
    for col in ["Candidate", "Sharpe", "CAGR", "MaxDD", "PF", "Trades", "AvgWin", "AvgLoss", "Dur(h)", "MFE%"]:
        wf_table.add_column(col, justify="right" if col != "Candidate" else "left")

    capture_table = Table(title="Big-move capture (full history)", box=box.SIMPLE_HEAVY)
    for col in ["Candidate", "Top5%", "Top10%", "TimeInMove%", "PnL/FullEqBet%",
                "LongTrades", "LongWin%", "ShortTrades", "ShortWin%"]:
        capture_table.add_column(col, justify="right" if col != "Candidate" else "left")

    for name, fn in candidates.items():
        console.print(f"[dim]Running {name} ...[/dim]")
        steps = evaluate_fixed_config_walk_forward(xrp_bars, funding, fn, CHANDELIER_CONFIG, TIMEFRAME, SPEC_WINDOWS)
        summary = summarize_walk_forward_extended(steps)
        wf_table.add_row(
            name, f"{summary['avg_sharpe']:.2f}", f"{summary['avg_cagr']*100:+.1f}%",
            f"{summary['worst_mdd']*100:.1f}%", f"{summary['avg_pf']:.2f}", str(summary["total_trades"]),
            f"${summary['avg_win_usdt']:.0f}", f"${summary['avg_loss_usdt']:.0f}",
            f"{summary['avg_duration_hours']:.0f}", f"{summary['avg_mfe_pct']*100:.1f}%",
        )

        signal_df = fn(xrp_bars)
        full_result = run_backtest(signal_df, funding, CHANDELIER_CONFIG)
        full_metrics = compute_metrics(full_result, TIMEFRAME)
        capture = swing_capture_analysis(swings, full_result.trades, CHANDELIER_CONFIG.initial_equity)
        cap_ratio = capture["aggregate_capture_ratio"]
        time_in_move = capture["aggregate_time_in_move_pct"]
        top5, top10 = full_metrics["top5_contribution_pct"], full_metrics["top10_contribution_pct"]
        long_stats = full_metrics["long_vs_short"]["long"]
        short_stats = full_metrics["long_vs_short"]["short"]
        capture_table.add_row(
            name,
            f"{top5*100:.0f}%" if top5 is not None else "n/a",
            f"{top10*100:.0f}%" if top10 is not None else "n/a",
            f"{time_in_move*100:.0f}%",
            f"{cap_ratio*100:.0f}%" if cap_ratio is not None else "n/a",
            str(long_stats["total_trades"]), f"{long_stats['win_rate']*100:.0f}%",
            str(short_stats["total_trades"]), f"{short_stats['win_rate']*100:.0f}%",
        )

    console.print()
    console.print(wf_table)
    console.print()
    console.print(capture_table)
    console.print(f"\n[dim]{len(swings)} major swings (>= {MIN_SWING_MOVE_PCT:.0%}) found in {TIMEFRAME} history "
                  f"2020-2026. TimeInMove% = fraction of each swing's OWN duration spent correctly positioned "
                  f"(scale-invariant, the primary reading). PnL/FullEqBet% = captured PnL vs. a 100%-of-equity "
                  f"directional bet over the same move — mechanically small for any risk-managed strategy since "
                  f"it never risks near 100% of equity per trade; don't read a low value here as 'missed the move' "
                  f"on its own, check TimeInMove% first.[/dim]")
    console.print("[dim]Reminder: coarse comparison by design (4 Donchian windows + 3 filter variants + 1 "
                  "reference) — not a fine parameter search. Chandelier window/multiplier fixed, not tuned.[/dim]")


if __name__ == "__main__":
    main()
