# Project status — XRPUSDT Perpetual trend-following system

Chronological summary of everything decided and found since the config freeze.
Complements `research_notes.md` (pre-backtest research rationale) and
`design_decisions.md` (open-parameter registry) — this file is the narrative
of what actually happened once real backtests started running. Update it as
new stages complete; don't let it go stale the way the other two did.

## 1. Frozen baseline configuration

After sweeping timeframe, Donchian window, and Chandelier trailing-stop
window/multiplier as robustness checks (not a best-parameter search), this
was frozen and has not changed since:

| Parameter | Value |
|---|---|
| Timeframe | 12h |
| Signal | Donchian(40) breakout, held until the opposite breakout (`strategies/strategy_a_trend.py`, `method="donchian"`) |
| Trailing stop | Chandelier, window=5, multiplier=4x ATR (`BacktestConfig.stop_type="chandelier"`) |
| Direction | LONG + SHORT |
| Filters | None — no BTC regime gate, no ADX filter, no volatility filter |
| Position sizing | Risk-per-trade only (`use_vol_targeting=False`), risk_pct=1%/trade |
| Margin | Isolated, leverage nominal max 2x |
| Cost scenario | BASE (`backtest/costs.py`) unless noted otherwise |

This exact config passed walk-forward OOS, Monte Carlo (pooled OOS trade
resampling — see the percentile-severity bug fix in `backtest/monte_carlo.py`'s
docstring/tests), and cost/funding stress testing in an earlier round. It is
the only config in this project with that full validation — nothing found
since (see §3-§5) has replaced it.

**OOS walk-forward result (XRPUSDT, this config, 4 rolling windows 2023-2026):**

| Year | CAGR |
|---|---|
| 2023 | -3.5% |
| 2024 | +25.3% |
| 2025 | -0.9% |
| 2026 (parcial, a sep) | +7.1% |
| **Promedio** | **7.0%** (Sharpe 0.31, peor MaxDD -8.5%) |

Read this as: a real, modest, positive edge with large year-to-year variance
— not a smooth 7%/year. One of the four OOS windows had negative Sharpe.

## 2. Position sizing / risk_pct scaling

Two separate studies, same conclusion from different angles:

- **Original sweep (0.25%-2.00% risk_pct)**: Sharpe is *constant* across
  risk_pct — pure linear notional scaling, so CAGR and drawdown move together
  in the same proportion. No free lunch from position size alone.
- **Wider reality check (1%-10% risk_pct, prompted by "can we hit 50% CAGR")**:

  | risk_pct | 2023 | 2024 | 2025 | 2026 (parcial) | Promedio | Peor MDD |
  |---|---|---|---|---|---|---|
  | 1% | -3.5% | +25.3% | -0.9% | +5.7% | 6.7% | -8.5% |
  | 2% | -7.7% | +45.4% | -2.1% | +11.1% | 11.7% | -16.3% |
  | 4% | -17.8% | +72.3% | -5.8% | +20.5% | 17.3% | -30.3% |
  | 7% | -34.4% | +86.6% | -13.3% | +30.8% | 17.4% | -47.6% |
  | 10% | -50.4% | +79.9% | -22.6% | +35.9% | 10.7% | -61.4% |

  Average CAGR *peaks* around 4-7% risk (~17%) and then **declines** at 10% —
  compounding/volatility-drag from bad years (2023, 2025) eats into the base
  the good year (2024) compounds from. **No risk_pct level reaches anywhere
  near 50% CAGR**; this isn't a risk-tolerance question, it's a ceiling
  intrinsic to the signal's compounding behavior. Getting materially higher
  returns requires either a better signal (Sharpe) or diversification across
  assets — not bigger bets on this one.

## 3. Trend Strength / capture-improvement research (Stages 1-4)

Opened to investigate whether the system could capture large XRP trends
better, explicitly NOT by increasing leverage.

- **Stage 1** — measured capture of the 19 major (≥30%) OOS swings: highly
  variable, 7%-60% captured depending on the swing.
- **Stage 2** — built `strategies/trend_strength.py` (0-4 score: ADX>25,
  |EMA50 slope|>2%, Donchian(20) recency ≤10 bars, 30d momentum>10%).
  Predictive value for "premature exit" was **mixed/inconclusive** across the
  3 named case-study swings.
- **Stage 2b/2c** — tested a narrower hypothesis (volatility expansion at
  trend start predicts how fast the noisy phase resolves) against all 19
  swings. **Rejected** — weak, non-monotonic correlation.
- **Stage 3** — objective, rule-based failure classification of all 19
  swings (categories A-G + OK):

  | Categoría | N | % | Pérdida potencial (pp) |
  |---|---|---|---|
  | C) Ruido — múltiples stops/reentradas | 7 | 37% | 262 |
  | G) Otro motivo, no concluyente | 4 | 21% | 53 |
  | OK) Capturó razonablemente | 4 | 21% | 337* |
  | A) Nunca entró en dirección correcta | 2 | 11% | 0 |
  | D) Entró demasiado tarde | 1 | 5% | 9 |
  | E) Demasiado rápido para Donchian(40) | 1 | 5% | 0 |

  (*dominated by one swing, +533%, where even the best-executed trade left
  huge MFE on the table — not evidence of a fixable failure.)
  B and F: zero swings. **C (whipsaw) dominates** — both by count and by
  potential value left on the table.
- **Stage 3b** — investigated the 2 "A" swings directly (trade-log level,
  plus a continuous non-windowed backtest to rule out a walk-forward
  slicing artifact). Finding: **not entry failures at all** — in both cases
  the strategy was on the *wrong side* through the entire reversal and its
  stop-out landed almost exactly at the swing's extreme, leaving no time
  inside the swing's own window for a correct-direction entry. Same failure
  family as C, not a distinct "Donchian never confirmed" problem. Revises
  Stage 3's "genuinely uncapturable by Donchian(40)" bucket down to **1/19
  (5%, category E only)**.
- **Stage 3c** — timed the 36 stop-outs inside the 7 "C" swings: median 19
  bars (~9.5 days) held before a stop, first-trade-of-swing vs later trades
  statistically the same. **Refutes** "trail is too tight right after entry."
- **Stage 4a** — checked why re-entries in C-swings are sometimes delayed
  many days: **100% of the long gaps (3/3) are explained by genuine opposite-
  direction Donchian(40) fakeout breakouts**, which mostly lost money (e.g.
  6 countertrend SHORT trades during the 2025 +101% rally's mid-move gap,
  net ≈ -395, while the real uptrend continued). Not slow reconfirmation —
  active bad countertrend trades.
- **Stage 4b** — built and tested `strategies/reversal_confirmation.py`
  (Option 2: require a reversal breakout to persist `confirm_bars`
  consecutive bars before honoring it; `confirm_bars=1` reproduces the frozen
  baseline exactly — verified). Result: **not a clean improvement**.

  | Variant | Sharpe | CAGR | Peor MDD | Categoría A | Categoría C |
  |---|---|---|---|---|---|
  | Baseline (confirm_bars=1) | 0.31 | 7.0% | -8.5% | 2 | 7 |
  | confirm_bars=2 | 0.12 | 8.9% | -12.0% | 6 | 2 |
  | confirm_bars=3 | 0.20 | 7.2% | -16.1% | 7 | 2 |

  Reduced whipsaw (C: 7→2) but nearly tripled missed-entry swings (A: 2→6-7)
  and worsened Sharpe/drawdown. **This specific mechanism is shelved** — not
  wired into anything live. The next idea worth testing (not yet built) is a
  Trend-Strength-based reversal filter (Option 1, deprioritized in favor of
  Option 2 because Stage 2 already showed Trend Strength's predictive power
  was weak) or a design that filters *fakeouts* specifically rather than
  delaying *all* reversals uniformly.

## 4. Cross-asset robustness (ETH)

Same frozen config, zero parameter changes, run on ETHUSDT:

| | XRPUSDT | ETHUSDT |
|---|---|---|
| 2023 | -3.5% | -3.5% |
| 2024 | +25.3% | +9.2% |
| 2025 | -0.9% | +3.9% |
| 2026 (parcial) | +7.1% | +18.0% |
| Promedio CAGR | 7.0% | 6.9% |
| Sharpe promedio | 0.31 | 0.66 |
| Peor MaxDD | -8.5% | -13.5% |
| Swings ≥30% capturados | 16/19 (84%) | 9/9 (100%) |
| Captura promedio cuando operó | 18% | 37% |

Similar CAGR without retuning is a real (if noisy — only 4 non-independent
windows, 9-19 swings) point in favor of the mechanism generalizing, not being
an XRP-only fluke. ETH's worse worst-case drawdown (-13.5%) is the
counterpoint. Data range for both was capped at 2020-01-01 by the test
script's own request — this did **not** test whether ETH has usable history
further back (its Binance Futures listing predates XRP's); that's still open
if it matters later.

## 5. Open threads / not yet done

- The 50% CAGR question (§2) points toward multi-asset diversification as
  the most promising realistic lever — not yet built or tested as an actual
  combined portfolio (only single-asset XRP and single-asset ETH have been
  run separately).
- No live/testnet execution code exists yet for `xrp_futures` (plan drafted
  covering `execution/`, `monitor/` Streamlit dashboard, but not yet built —
  see `.claude/plans/recursive-finding-alpaca.md` if still present, or ask
  to re-derive it).
- `risk/engine.py`'s `approx_liquidation_distance_pct()` is still the rough
  approximation flagged in `design_decisions.md` — the live-execution plan's
  answer is to read Binance's real `liquidationPrice` directly rather than
  improve this formula, but that's not implemented.
- Category G swings (4/19, Stage 3) were left as "no single objective cause
  dominates" — not investigated further at the level of detail Category
  A/C got.
