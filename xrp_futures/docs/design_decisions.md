# Design decisions and open parameters

Per §25/§36.5: none of the parameters below should be treated as "final" until a
robustness sweep (varying it across its listed range) shows the result holds up.
This file is the registry of what's still open — update the "status" column as
sweeps narrow things down.

## Parameters explicitly NOT fixed yet

| Parameter | Range to sweep | Where | Status |
|---|---|---|---|
| Timeframe | 1h / 4h / 6h / 12h / 1d | `StrategyAParams.timeframe` etc. | **Frozen: 12h**, chosen after a robustness sweep alongside Donchian window and Chandelier window/multiplier (see `docs/project_status.md` §1) |
| EMA pair (trend) | 50/200, 20/100, others | `StrategyAParams.ema_fast/ema_slow` | Untested — moot for the frozen config, which uses `method="donchian"`, not the EMA method |
| Donchian window | e.g. 20-100 | `StrategyAParams.donchian_window` | **Frozen: 40** |
| Momentum lookback | 7/14/30/60/90 days | `StrategyBParams.lookback_days` | Untested — moot, frozen config is Strategy A (trend only), no momentum term |
| Momentum form | raw / log / vol-adjusted | `StrategyBParams.form` | Untested — same as above |
| Realized-vol window | 20/30/60 bars | `*.vol_window` | Untested |
| Vol target | 10/15/20/25% | `BacktestConfig.target_vol` | Untested — moot, frozen config uses `use_vol_targeting=False` |
| Risk per trade | 0.25/0.50/0.75/1.00% | `BacktestConfig.risk_pct` | Swept 0.25%-10%: Sharpe is ~constant across the range (pure linear scaling), CAGR/MaxDD scale together and CAGR peaks ~17% around 4-7% risk before *declining* past that from compounding drag (`docs/project_status.md` §2). **Operating point: 1%**, chosen for risk discipline, not because higher values failed to run |
| ATR stop multiplier | 1.5-3.0 | `BacktestConfig.atr_multiplier` | Untested — note this is the *initial* stop distance, separate from the Chandelier trailing multiplier below; still at the 2.0 default |
| Trailing stop style | none / ATR / % / Chandelier | `BacktestConfig.stop_type` | **Frozen: Chandelier**, window=5, multiplier=4x ATR |
| BTC regime gate | on / off, threshold choices | `StrategyCParams.use_btc_regime`, `regime.py` thresholds | Untested — hypothesis only; frozen config runs with no filters at all |
| RiskEngine thresholds | daily/weekly loss %, drawdown zones | `RiskEngineConfig` | Not used in the validated frozen config (`risk_engine=None`) — §12's example values were never exercised against real data |
| Cost scenario | Optimistic/Base/Pessimistic | `backtest/costs.py` presets | Frozen config's headline numbers are BASE; PESSIMISTIC/combined-stress scenarios were run in an earlier stress-test round and degrade the result (exact figures not reproduced in `project_status.md` — re-run `backtest/stress_test.py` if the precise numbers are needed) |

## Architectural decisions made (and why)

- **Parameters are dataclass constructor args, never module-level globals loaded
  from `.env`.** The legacy spot bot's `config.py` pattern (env vars → global
  constants at import time) can't support a parameter sweep — you'd need to
  restart the process per combination. Every `xrp_futures` strategy/risk/backtest
  parameter is passed explicitly so the same code runs unchanged across hundreds
  of combinations, and every backtest run can log its exact parameter set.
- **One-bar execution delay in the backtest engine** (`backtest/engine.py`):
  signal computed causally from bar i's close, filled at bar i+1's open. Chosen
  over "fill at the same bar's close" to be conservative/realistic and to make
  lookahead bugs structurally harder to introduce (verified with a dedicated test).
- **Bar-based (not tick-level) engine.** Funding-vs-stop intra-bar sequencing is
  therefore a simplification: funding settlements inside a bar are applied against
  the position held coming INTO that bar, before that bar's own entry/exit logic.
  Acceptable for research-stage decisions; would need tick/order-book-level
  simulation before sizing real capital on the exact timing.
- **`min(risk_per_trade_notional, vol_target_notional)`** for Strategy D sizing —
  neither mechanism alone can push risk higher than the other allows. A/B/C use
  risk-per-trade only (`use_vol_targeting=False`) specifically so the A/B/C/D
  comparison isolates signal quality from sizing method.
- **`RiskEngine` never changes direction, only gates/scales.** Keeps "what to
  trade" (strategy) and "how much / whether at all" (risk) as separable, only
  because that separation is what let A/B/C run without a risk engine at all
  while D adds it as its own testable increment.
- **`approx_liquidation_distance_pct()` is explicitly a rough approximation**
  (isolated margin, no fees, `1/leverage - maintenance_margin_rate`), flagged in
  its own docstring as not exchange-accurate. Fine for a backtest-phase gate at
  ~1x leverage where liquidation is never remotely close; must be replaced with
  Binance's real liquidation formula before the live-execution phase uses it to
  protect real capital.
