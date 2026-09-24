"""
Configuration for the live/testnet bot (docs/execution_plan.md, plan v2).

Every value here was either validated by a backtest study or explicitly
confirmed by the user — see the plan's "Decisiones de diseño" for the why.
Strategy params are the frozen ones and must stay identical to the backtest
(tests/test_execution_live_signal.py checks the live path reproduces it).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from xrp_futures.backtest.engine import BacktestConfig
from xrp_futures.backtest.costs import BASE
from xrp_futures.strategies.strategy_a_trend import StrategyAParams

UNIVERSE = ("BTCUSDT", "ETHUSDT", "XRPUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "LTCUSDT", "LINKUSDT")

FROZEN_STRATEGY = StrategyAParams(timeframe="12h", method="donchian", donchian_window=40)


@dataclass(frozen=True)
class LiveConfig:
    universe: tuple[str, ...] = UNIVERSE
    strategy: StrategyAParams = FROZEN_STRATEGY
    initial_stop_atr_multiplier: float = 2.0     # BacktestConfig.atr_multiplier default used by the validated runs
    chandelier_window: int = 5
    chandelier_multiplier: float = 4.0
    risk_pct: float = 0.003                      # per trade, on TOTAL account equity (user-confirmed)
    leverage: int = 3                            # margin tool only; isolated margin (user-confirmed)
    kill_switch_drawdown: float = 0.30           # block new entries at -30% from peak (user-confirmed)
    max_total_notional_pct: float = 1.0          # PROPOSED safety cap, not yet confirmed by the user
    # Size positions as if the account had this much equity (e.g. to simulate $2,000 on a
    # testnet account funded with far more). None = use the real futures wallet equity.
    equity_override_usdt: float | None = 250.0   # user-chosen simulated capital for the testnet phase
    # On testnet, size/validate orders with PRODUCTION's exchange filters (testnet allows
    # smaller orders, e.g. LTC/LINK $5 vs $20, BTC 0.0001 vs 0.001) so testnet behaves
    # like real money would. Ignored in production (already the real filters).
    use_production_filters: bool = True
    bars_lookback: int = 300                     # 12h bars fetched per cycle (Donchian 40 + ATR warmup)

    def backtest_config(self, initial_equity: float = 10_000.0) -> BacktestConfig:
        """The BacktestConfig equivalent to this live config (for live-vs-backtest checks)."""
        return BacktestConfig(
            initial_equity=initial_equity, risk_pct=self.risk_pct, use_vol_targeting=False,
            stop_type="chandelier", atr_multiplier=self.initial_stop_atr_multiplier,
            chandelier_window=self.chandelier_window, chandelier_multiplier=self.chandelier_multiplier,
            trailing_enabled=True, cost_model=BASE,
        )


DEFAULT = LiveConfig()
