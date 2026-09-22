"""
Strategy A — Trend Following, pure (§18/§5). No momentum term, no volatility
targeting (BacktestConfig.use_vol_targeting=False is the intended pairing for A/B/C
so the comparison in §23 isolates signal quality, not position sizing).

Two selectable trend definitions, both to be swept for robustness (§25), neither
assumed superior ahead of the data:
  "ema"      price vs EMA(slow) AND EMA(fast) vs EMA(slow) alignment (§4's example)
  "donchian" Donchian channel breakout, held until the opposite breakout
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from xrp_futures.indicators.trend import donchian_breakout_signal, ema
from xrp_futures.strategies.base import attach_risk_columns, persist_signal


@dataclass(frozen=True)
class StrategyAParams:
    timeframe: str = "6h"          # 6h is mandatory to test per the AdaptiveTrend reference; not assumed best
    method: str = "ema"            # "ema" | "donchian"
    ema_fast: int = 50
    ema_slow: int = 200
    donchian_window: int = 55
    atr_window: int = 14
    vol_window: int = 30


def generate(bars: pd.DataFrame, params: StrategyAParams) -> pd.DataFrame:
    out = attach_risk_columns(bars, params.timeframe, params.atr_window, params.vol_window)
    close, high, low = out["close"], out["high"], out["low"]

    if params.method == "ema":
        fast = ema(close, params.ema_fast)
        slow = ema(close, params.ema_slow)
        out["ema_fast"] = fast
        out["ema_slow"] = slow
        long_cond = (close > slow) & (fast > slow)
        short_cond = (close < slow) & (fast < slow)
        signal = pd.Series(0, index=out.index, dtype=int)
        signal[long_cond] = 1
        signal[short_cond] = -1
    elif params.method == "donchian":
        momentary = donchian_breakout_signal(close, high, low, params.donchian_window)
        signal = persist_signal(momentary)
    else:
        raise ValueError(f"Unknown Strategy A method {params.method!r}")

    out["signal"] = signal
    return out
