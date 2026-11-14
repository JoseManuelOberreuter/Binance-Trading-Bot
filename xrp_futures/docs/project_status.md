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

## 4b. Regime-scaled risk (8-asset portfolio, October 2026)

`scripts/run_regime_risk.py`. Each entry's risk is scaled by the BTC regime
(`strategies/regime.py`, causal at the decision bar): more when the trade goes
WITH the regime, less when neutral, least when AGAINST it. The signal, stops and
universe are unchanged.

- **Engine support:** `BacktestConfig.regime_risk_multipliers` and `max_notional_pct`,
  both off by default.
- **Shared-capital engine:** `backtest/portfolio_engine.py` runs all symbols out of
  ONE equity with the live exposure cap (entries over `max_total_notional_pct` x
  equity are skipped, as in live). A one-symbol run reproduces `run_backtest`
  exactly (tested).

All figures below come from the shared-capital engine with the live 1x cap,
mark-to-market, starting from $1,000.

| Variant (with/neutral/against) | Avg risk | 2022 | 2023 | 2024 | 2025 | 2023-25 chained | 2020-26 chained | Worst DD |
|---|---|---|---|---|---|---|---|---|
| Fixed 0.6% (live) | 0.60% | 901 | 1,346 | 953 | 1,034 | 1,348 | 4,203 | -39% |
| Fixed 0.75% | 0.75% | 871 | 1,321 | 923 | 945 | 1,177 | 4,210 | -47% |
| Regime 1.0/0.5/0.25 | 0.64% | 1,059 | 1,372 | 812 | 1,073 | 1,230 | 6,862 | -48% |
| Regime 1.2/0.6/0 | 0.85% | 1,125 | 1,683 | 699 | 1,116 | 1,311 | 10,561 | -57% |

- **Over 2020-26, regime scaling beats fixed risk at the same average risk**, mostly
  from 2020-2022. In the bear year 2022 it gains or loses little where fixed risk
  loses about 10%.
- **Over 2023-25 it gives no edge once the live cap applies.** 2024 is much worse
  (BTC was bullish while alts chopped), which cancels the gains in 2023 and 2025.
  Without the cap, regime scaling wins in every period.
- **Walk-forward** (variant picked by Calmar on prior years only, 2022-26 chained):
  $1,954 versus $1,407 for fixed 0.6%. Nearly all of the gap is 2022-23; the 2024
  test year lost $699 against $953.
- **Drawdowns are larger** (-48% to -57% versus -39%).
- **Kill-switch finding (applies to the CURRENT live config too):** chained from 2020,
  every variant reaches -30% from peak, which is the live kill-switch level. Fixed
  0.6% gets there in March 2023, after the 2021 peak and the 2022 bear market. Live,
  that would have blocked entries for good until a manual reset.
- **The cap binds even at 0.6%:** 99 entries skipped over 2020-26, and far more at
  higher risk.
- **Status:** not adopted. The edge is real over the full history but absent in the
  most recent years under the live cap, and it costs a deeper drawdown.

## 4c. Keeping the worst drawdown at or under 30% (October 2026)

`scripts/run_drawdown_limit.py`. Shared-capital engine with the live 1x cap and
mark-to-market equity. A variant passes only if its worst drawdown stays at or under
30% (the kill-switch level) from every yearly start date.

The throttle is `drawdown_risk_steps` in `portfolio_engine.py`:
- soft: half risk at -10%, a quarter at -20%;
- hard: half at -8%, a quarter at -15%, a tenth at -22%.

| Variant | 2022 | 2023 | 2024 | 2025 | 2023-25 chained | 2020-26 chained | Worst DD | Passes |
|---|---|---|---|---|---|---|---|---|
| Fixed 0.6% (live) | 901 | 1,346 | 953 | 1,034 | 1,348 | 4,203 | -39% | no |
| Fixed 0.45% | 930 | 1,281 | 981 | 1,041 | 1,322 | 3,282 | -31% | no |
| **Fixed 0.4%** | 939 | 1,254 | 988 | 1,037 | **1,296** | 2,948 | **-28%** | yes |
| Fixed 0.3% | 956 | 1,196 | 1,000 | 1,029 | 1,237 | 2,339 | -22% | yes |
| 0.8% + soft throttle | 884 | 1,154 | 856 | 999 | 1,091 | 2,997 | -28% | yes |
| Regime 1.0/0.5/0.25 + soft | 1,013 | 1,218 | 921 | 1,072 | 1,143 | 3,853 | -27% | yes |

- **Drawdown scales roughly linearly with risk.** 0.4% is the highest fixed risk
  that stays under 30%.
- **Drawdown throttles cap the drawdown but cost return,** worst in recent years
  (2023-25 chained $1,011-1,091 versus $1,296 for fixed 0.4%). They cut risk exactly
  when the recovery trades come.
- **Regime + soft throttle has the best full-history result** that passes ($3,853),
  but it is worse than fixed 0.4% over 2023-25.
- **Recommendation:** fixed 0.4%. It is the simplest, the best of the passing
  variants in recent years, and needs no new live code (`SET_RISK`). The margin
  under the 30% kill-switch is thin (-28%); 0.35% would add headroom.

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
