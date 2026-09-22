# Research notes — XRPUSDT Perpetual trend/momentum/vol-targeting system

Synthesis from academic literature and GitHub research done before building the
backtesting system (per the "primera tarea" instructions). Written to survive as
project documentation, not just conversation history — update this file as
walk-forward/Monte Carlo/stress-test results change what's known.

## What the user's own targets (§1) imply

Target: Sharpe > 1, CAGR 15-25%+, MaxDD < 20%, single-asset (XRP), LONG/SHORT,
low leverage. These are **design objectives, not guarantees** — the instruction
was explicit that if the data shows they're unrealistic, that should be said
plainly. As of this writing, that check hasn't been run yet (walk-forward/compare
results are the next step) — this section records the *prior*, before seeing data.

## Academic evidence (verified independently via search, not taken on faith)

1. **Risk-managed momentum in crypto** (Barroso & Santa-Clara–style; ScienceDirect,
   2025 — "Cryptocurrency Market Risk-Managed Momentum Strategies"): scaling a
   *cross-sectional, multi-asset* long/short momentum portfolio by inverse realized
   volatility lifts annualized Sharpe **1.12 → 1.42**. Real, independently confirmed
   result. **Caveat**: this is a diversified multi-asset momentum book, not a
   single-asset time-series strategy — the magnitude won't transfer 1:1 to XRP-only.
   Relevant as evidence that *vol-scaling helps*, not as a return target.

2. **AdaptiveTrend** (arXiv 2602.11708, Feb 2026 — "Systematic Trend-Following with
   Adaptive Portfolio Construction"): claims Sharpe **2.41**, MaxDD **-12.7%**,
   Calmar **3.18** OOS over 2022-2024. Treated here as a **methodological reference
   only** (a trailing stop calibrated to intraday vol regimes; the paper's **6h
   timeframe** choice), explicitly NOT a return target, because:
   - Very recent, apparently non-peer-reviewed preprint — single-preprint results
     in quant finance routinely fail to reproduce.
   - Results are for a **150+ pair portfolio**. Most of that Sharpe/Calmar plausibly
     comes from cross-asset diversification, which a single-asset XRP strategy
     structurally cannot access — comparing our single-asset numbers against this
     paper's portfolio-level numbers would be an apples-to-oranges mistake.
   - 36-month OOS window is short for trusting a tail/drawdown claim.
   - **Concrete, actionable takeaway kept**: 6h is included in the mandatory
     timeframe sweep (§7 required it anyway).

3. **Time-series momentum + vol scaling** (Moskowitz/Ooi/Pedersen, the classic
   TSMOM paper): direct methodological basis for Strategy A/D — sign of trailing
   return as the signal, position sized ∝ target_vol/realized_vol. A public
   replication exists (`rkohli3/TSMOM` on GitHub) used only as a sanity-check
   reference for the vol-scaling formula, not as a dependency.

4. **Calibration for single-asset expectations**: broader crypto trend/momentum
   literature (e.g. "A Decade of Evidence of Trend Following Investing in
   Cryptocurrencies", arXiv 2009.12155) suggests single-asset strategies mostly
   cluster around **Sharpe 0.5–1.2** before risk overlays. Vol-targeting/risk
   management typically *adds* to that, it doesn't transform a mediocre signal
   into an exceptional one. Working assumption: Sharpe > 1 and MaxDD < 20% is a
   plausible bar for XRP-only at 1x leverage; the 15-25%+ CAGR target is the least
   certain piece of §1 and should be treated as an output to observe, not a
   constraint to engineer toward.

## Repository research

- **Freqtrade** (github.com/freqtrade/freqtrade): supports `trading_mode="futures"`
  on Binance. For EU/MiCA-restricted accounts, isolated margin mode was removed
  (~mid-2024) in favor of a cross-only "Credit Trading Mode" (stake currency
  BNFCR). Non-EU accounts appear to retain isolated margin, but this needs
  re-verification against the *specific account* used at execution time. This is
  a **live-execution-phase concern** — irrelevant to the current backtest-only
  phase, noted here so it isn't forgotten later.
- `rkohli3/TSMOM`: TSMOM replication, used only as a formula sanity-check (see above).

## First real backtest result (Strategy A, single unoptimized param set)

XRPUSDT 6h, 2020-01-06 to 2026-09-14, EMA(50)/EMA(200) trend-only, no momentum
filter, no vol targeting, ATR(2.5×) stop with trailing, BASE cost scenario:

| Metric | Value |
|---|---|
| CAGR | -0.26% |
| Sharpe | -0.02 |
| Max Drawdown | -14.5% |
| Win rate | 27.1% |
| Total trades | 468 |
| Gross PnL | +$266 |
| Net PnL | **-$172** |

**Reading this honestly**: pure trend-following on XRP alone, unoptimized, is
roughly break-even before costs and net-negative after fees+funding. This is
exactly the warning from the funding-rate research (§13): a strategy can look
profitable gross and be unprofitable net. It's also consistent with the
single-indicator skepticism in §5/§18 — Strategy A was never expected to be the
answer on its own; B/C/D (momentum, trend+momentum, +vol-targeting+risk) are
where the real comparison happens. See `xrp_futures/backtest/results/` for the
full A/B/C/D/Buy&Hold comparison once `compare_strategies.py` has been run.

## Open questions this backtesting system is built to answer, not assume

- Does momentum add anything trend alone doesn't? (Strategy A vs B vs C)
- Does the BTC regime filter help, hurt, or do nothing for XRP signals?
  (Strategy C vs C+regime — §16/§17's hypothesis, untested as of this writing)
- Does volatility targeting improve risk-adjusted returns, or just reduce raw
  returns proportionally with no Sharpe benefit? (Strategy C vs D)
- Is 6h actually better than 1h/4h/12h/1d for XRP specifically, or just for the
  AdaptiveTrend paper's very different (150-asset) setup?
- Does the result survive walk-forward (i.e., is it real or overfit to 2020-2026
  in hindsight)?
