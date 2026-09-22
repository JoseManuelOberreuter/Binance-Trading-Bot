"""
Strategy D — Trend + Momentum + Volatility Targeting + Risk Management (§18).
The primary candidate — NOT pre-declared a winner; compare_strategies.py must show
it beating A/B/C and Buy&Hold with real numbers before it's treated as one.

D's ENTRY SIGNAL is identical to Strategy C (trend/momentum agreement, optional BTC
regime gate) — signal quality is a Strategy C question. What D adds is at the
backtest/portfolio level, not the signal level:
  - BacktestConfig.use_vol_targeting=True (§8): position sized by target_vol/realized_vol,
    not just fixed risk-per-trade.
  - The portfolio-level RiskEngine (risk/engine.py, §12): daily/weekly loss throttling,
    drawdown-based size reduction, kill switch — applied by the caller around
    run_backtest, not baked into this module.
"""

from __future__ import annotations

import pandas as pd

from xrp_futures.backtest.costs import BASE, CostModel
from xrp_futures.backtest.engine import BacktestConfig
from xrp_futures.strategies.strategy_c_trend_momentum import StrategyCParams
from xrp_futures.strategies.strategy_c_trend_momentum import generate as generate_c


def generate(bars: pd.DataFrame, params: StrategyCParams, btc_regime: pd.Series | None = None) -> pd.DataFrame:
    return generate_c(bars, params, btc_regime)


def recommended_backtest_config(
    initial_equity: float = 10_000.0,
    risk_pct: float = 0.005,
    target_vol: float = 0.15,
    cost_model: CostModel = BASE,
) -> BacktestConfig:
    """
    A starting point for Strategy D runs, NOT a tuned/final configuration — every
    value here is one of the §25 robustness-sweep parameters and should be varied,
    not trusted as-is.
    """
    return BacktestConfig(
        initial_equity=initial_equity,
        risk_pct=risk_pct,
        use_vol_targeting=True,
        target_vol=target_vol,
        stop_type="chandelier",
        chandelier_window=22,
        chandelier_multiplier=3.0,
        trailing_enabled=True,
        cost_model=cost_model,
    )
