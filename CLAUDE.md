# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Two independent systems in this repo:

- **`xrp_futures/`** — active development. A research/backtesting system for a Binance USDⓈ-M Futures trend-following strategy on XRPUSDT Perpetual (LONG/SHORT, low effective leverage, strict risk management). Currently backtest-only: no live order placement, no API keys required (historical data comes from Binance's public `data.binance.vision` dump) — see `xrp_futures/docs/execution_plan.md` for the (designed, not yet built) live/testnet execution + monitoring dashboard plan. Config is **frozen**: Donchian(40) signal, Chandelier(window=5, 4x ATR) trailing stop, 12h timeframe, LONG+SHORT, no filters, risk_pct=1%/trade — see `xrp_futures/docs/project_status.md` for the validated baseline and everything found since, `xrp_futures/docs/research_studies.md` for the detailed per-study methodology/results.
- **`legacy/spot_grid_bot/`** — legacy, frozen. The original spot grid-trading bot for ETH/USDT. Still runs, no longer developed. See `legacy/spot_grid_bot/README.md`.

## Commands

```bash
pip install -r requirements.txt        # shared by both systems
python -m pytest xrp_futures/tests/    # xrp_futures test suite (pure-function + engine tests, no network)

# xrp_futures: download/cache historical XRPUSDT + BTCUSDT data (no API key needed)
python -m xrp_futures.scripts.download_data
python -m xrp_futures.scripts.download_data --start 2020-01-01 --end 2026-09-01
python -m xrp_futures.scripts.download_data --symbols XRPUSDT BTCUSDT --force-refresh

# legacy spot bot (run from its own directory; see legacy/spot_grid_bot/README.md)
cd legacy/spot_grid_bot
python stats.py        # read-only account snapshot
python simulate.py     # grid backtest
python bot.py          # live loop (testnet/production per .env ENVIRONMENT)
```

`.env` at the repo root holds Binance API keys (`BINANCE_API_KEY`/`BINANCE_SECRET`, `TESTNET_API_KEY`/`TESTNET_SECRET`, `ENVIRONMENT=testnet|production`) — shared by both systems (`xrp_futures` doesn't use them yet since it's backtest-only; the legacy bot uses them directly).

## xrp_futures architecture

Data flows: `data/` → `indicators/` → `strategies/` → `backtest/` (with `risk/` cutting across sizing and portfolio-level gating).

- **`data/binance_vision.py`** — downloads historical klines + funding rate from `data.binance.vision` (Binance's public archive, no API key). **Important quirk**: pre-2021 monthly archives ship without a CSV header row while newer ones do; `parse_archive_csv()` detects this explicitly (trusting `pd.read_csv`'s auto-header on a headerless file silently corrupts/drops that data with no error — this happened once already, see the regression test in `tests/test_binance_vision.py`).
- **`data/loader.py`** — parquet cache (`data/cache/`, gitignored) on top of `binance_vision`, with incremental backfill/tail-extension (`_load_incremental`): only fetches the date range actually missing, never re-downloads the full history on every call. Base granularity is 1h; `resample_ohlcv()` derives 4h/6h/12h/1d from it.
- **`indicators/`** — pure functions over `pd.Series`: `trend.py` (EMA, slope, Donchian, high/low breakout, ADX — ADX implemented directly, no external TA dep), `momentum.py` (raw/log/vol-adjusted momentum over day-based lookbacks, converted to bar counts via the timeframe), `volatility.py` (realized vol, ATR).
- **`risk/position_sizing.py`** — `risk_per_trade_notional()` (equity × risk_pct / stop_distance_pct) and `volatility_target_notional()` (equity × target_vol/realized_vol, clamped); `combined_notional()` takes the smaller of the two.
- **`risk/stops.py`** — ATR/percentage/Chandelier stop price calculation plus `trailing_stop_update()` (monotonic — only tightens toward the market).
- **`risk/engine.py`** — `RiskEngine`: portfolio-level gate/scaler sitting above any strategy signal. `update_equity()` each bar tracks peak equity and day/week boundaries; `allow_new_entry()` blocks new trades on kill-switch/daily-loss-stop/cooldown/max-trades; `size_multiplier()` scales notional down under drawdown/daily-loss/weekly-loss (multipliers stack multiplicatively — each is an independent reason to reduce, not alternative severities of one thing). Also has standalone `liquidation_gate()`/`approx_liquidation_distance_pct()` (rough, NOT exchange-accurate — must be replaced with Binance's real liquidation formula before this gates real capital).
- **`strategies/base.py`** — shared contract: a strategy is a function `(bars, params) -> bars_with[signal, atr, realized_vol_ann]`, not a class hierarchy. `persist_signal()` turns a momentary breakout event into a held position signal.
- **`strategies/strategy_a_trend.py`** (trend only), **`strategy_b_momentum.py`** (momentum only), **`strategy_c_trend_momentum.py`** (position only where trend AND momentum agree; optional BTC-regime gate via `strategies/regime.py`), **`strategy_d_full.py`** (same signal as C; the "+ Risk Management" and "+ Volatility Targeting" difference is applied by the caller — `BacktestConfig.use_vol_targeting=True` plus a `RiskEngine` — not baked into the signal itself). A/B/C are meant to run with `use_vol_targeting=False` (plain risk-per-trade sizing) so the A/B/C/D comparison isolates signal quality from sizing.
- **`backtest/engine.py`** — `run_backtest(bars, funding, config, risk_engine=None)`: bar-by-bar loop, **one-bar execution delay** (signal computed causally from bar i's close, filled at bar i+1's open — no lookahead), intrabar stop fills via high/low touch (gap-through uses the worse of open vs stop), funding settlements applied each bar to whatever position was open coming into it, before that bar's own entry/exit. `risk_engine=None` reproduces plain strategy-only behavior exactly (tested).
- **`backtest/costs.py`** — `CostModel` (taker fee + slippage as adverse price adjustment) and `funding_pnl()`; `OPTIMISTIC`/`BASE`/`PESSIMISTIC` presets for cost-scenario sweeps.
- **`backtest/metrics.py`** — CAGR, Sharpe, Sortino, Calmar, MaxDD, avg drawdown, time-under-water, win rate, profit factor, expectancy, best/worst month, longest losing streak, per-year returns, LONG-vs-SHORT breakdown — all computed from the net (post-cost) equity curve unless a metric is explicitly named "gross".
- **`backtest/walk_forward.py`** — `evaluate_fixed_config_walk_forward()` (fixed params, no inner search — used for all robustness/diagnostic work) and `run_walk_forward()` (with an inner per-window param search); `SPEC_WINDOWS` are the 4 rolling OOS windows (train up to year N-1, test year N, for 2023-2026). `slice_result_by_time()` restricts a `BacktestResult` to a sub-period without re-running from a different equity base.
- **`backtest/monte_carlo.py`** — `run_monte_carlo_from_returns()` resamples pooled OOS trade returns; percentile convention is p95=severe/worst, p5=mild/best (documented in the module docstring — this direction was a real bug once, see its regression tests).
- **`backtest/stress_test.py`** — cost/funding shock scenarios (individual and combined) layered on top of a given config.
- **`backtest/swing_capture.py`** — `identify_major_swings()` (zigzag over big price legs), `detailed_swing_report()`/`trades_in_swing()` — the toolkit behind the Stage 1-4 failure-diagnosis work in `xrp_futures/docs/research_studies.md`.
- **`reports/report.py`** — Rich console tables for backtest results.
- **`scripts/`** — 18+ `run_*` scripts, one per study/diagnostic/comparison; `download_data.py`, `compare_strategies.py`, and the whole Stage 1-4 diagnostic chain plus cross-asset/risk-scaling checks documented in `xrp_futures/docs/research_studies.md`.

## Working in this repo

- `xrp_futures` has real unit tests (`xrp_futures/tests/`, run with `pytest`) — every pure-function module (indicators, position sizing, stops, metrics) and the backtest engine (including a hand-traced numeric test and a no-lookahead test) has coverage. Add tests alongside any new module there; the legacy bot has none and that's an accepted, unrelated state.
- Every strategy/risk parameter in `xrp_futures` (timeframe, EMA spans, momentum lookback, ATR multiplier, vol target, risk-per-trade %, stop style) is deliberately NOT hardcoded as a "final" value anywhere — they're constructor args on params dataclasses so they can be swept for robustness testing. Don't bake in a "best" value without it coming from an actual sweep result.
- `legacy/spot_grid_bot/README.md` and `PLAN.md` (Spanish) describe the frozen spot bot; not authoritative for `xrp_futures`.
- Never commit `.env` (already gitignored) or print/log API keys. `logs/`, `__pycache__/`, and `xrp_futures/data/cache/*.parquet` are also gitignored.
