"""
Strategy B — Momentum, pure (§6/§18). No trend filter, no volatility targeting
(paired with BacktestConfig.use_vol_targeting=False, same as Strategy A, so the
A vs B vs C vs D comparison isolates signal quality).

`form` selects which momentum definition drives the sign (§6): all three are
computed and attached to the output for inspection regardless of which one drives
`signal`, so a robustness sweep can compare them without re-running the backtest.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from xrp_futures.indicators.momentum import log_momentum, momentum_sign, raw_momentum, vol_adjusted_momentum
from xrp_futures.strategies.base import attach_risk_columns


@dataclass(frozen=True)
class StrategyBParams:
    timeframe: str = "6h"
    lookback_days: int = 30        # sweep 7/14/30/60/90 per §6
    form: str = "vol_adjusted"     # "raw" | "log" | "vol_adjusted"
    mom_vol_window: int = 30       # realized-vol window used only by the vol_adjusted form
    deadband: float = 0.0          # ignore momentum smaller than this magnitude -> flat
    atr_window: int = 14
    vol_window: int = 30           # realized-vol window for sizing (BacktestConfig.realized_vol_ann)


def generate(bars: pd.DataFrame, params: StrategyBParams) -> pd.DataFrame:
    out = attach_risk_columns(bars, params.timeframe, params.atr_window, params.vol_window)
    close = out["close"]

    out["momentum_raw"] = raw_momentum(close, params.lookback_days, params.timeframe)
    out["momentum_log"] = log_momentum(close, params.lookback_days, params.timeframe)
    out["momentum_vol_adjusted"] = vol_adjusted_momentum(
        close, params.lookback_days, params.timeframe, params.mom_vol_window
    )

    mom_col = {"raw": "momentum_raw", "log": "momentum_log", "vol_adjusted": "momentum_vol_adjusted"}.get(params.form)
    if mom_col is None:
        raise ValueError(f"Unknown Strategy B form {params.form!r}")

    out["signal"] = momentum_sign(out[mom_col], deadband=params.deadband)
    return out
