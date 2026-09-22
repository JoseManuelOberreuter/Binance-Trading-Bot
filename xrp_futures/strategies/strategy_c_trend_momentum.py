"""
Strategy C — Trend + Momentum (§18). Two selectable combination modes — neither
assumed better ahead of the data (§4: "no asumir que estos indicadores son
definitivos. Probar diferentes configuraciones"):

  "and"   (original) — take a position only when trend AND momentum AGREE;
          flat otherwise. A conjunction, deliberately strict.
  "veto"  (relaxed) — take the TREND direction by default; momentum only VETOES
          it (forces flat) when momentum actively points the OPPOSITE way.
          Momentum being merely neutral (0) is not treated as disagreement, so
          this fires much more often than "and" — the hypothesis being that
          "and" was too strict (§25 robustness note: an empirical comparison
          run showed "and" performing worse than trend alone, prompting this).

Optional BTC regime gate (§16/§17): when enabled, a signal is only taken if it
doesn't contradict the BTC regime (e.g. no SHORT while BTC regime is BULL). This
is OFF by default — compare_strategies.py should run both ways to test the
hypothesis empirically rather than assume it helps.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from xrp_futures.strategies import strategy_a_trend as trend_mod
from xrp_futures.strategies import strategy_b_momentum as momentum_mod
from xrp_futures.strategies.regime import BEAR, BULL


@dataclass(frozen=True)
class StrategyCParams:
    timeframe: str = "6h"
    trend: trend_mod.StrategyAParams = None
    momentum: momentum_mod.StrategyBParams = None
    mode: str = "and"  # "and" (strict agreement) | "veto" (trend-led, momentum only blocks disagreement)
    use_btc_regime: bool = False

    def __post_init__(self):
        if self.trend is None:
            object.__setattr__(self, "trend", trend_mod.StrategyAParams(timeframe=self.timeframe))
        if self.momentum is None:
            object.__setattr__(self, "momentum", momentum_mod.StrategyBParams(timeframe=self.timeframe))
        if self.mode not in ("and", "veto"):
            raise ValueError(f"Unknown Strategy C mode {self.mode!r}")


def generate(bars: pd.DataFrame, params: StrategyCParams, btc_regime: pd.Series | None = None) -> pd.DataFrame:
    trend_out = trend_mod.generate(bars, params.trend)
    momentum_out = momentum_mod.generate(bars, params.momentum)

    out = trend_out.copy()
    out["trend_signal"] = trend_out["signal"]
    out["momentum_signal"] = momentum_out["signal"]
    for col in ("momentum_raw", "momentum_log", "momentum_vol_adjusted"):
        out[col] = momentum_out[col]

    if params.mode == "and":
        agree = out["trend_signal"] == out["momentum_signal"]
        signal = out["trend_signal"].where(agree, 0)
    else:  # "veto"
        disagree = (out["trend_signal"] != 0) & (out["momentum_signal"] == -out["trend_signal"])
        signal = out["trend_signal"].where(~disagree, 0)

    if params.use_btc_regime:
        if btc_regime is None:
            raise ValueError("use_btc_regime=True requires a `btc_regime` Series aligned to `bars`")
        regime = btc_regime.reset_index(drop=True)
        signal = signal.reset_index(drop=True)
        signal[(signal == 1) & (regime == BEAR)] = 0
        signal[(signal == -1) & (regime == BULL)] = 0
        out["btc_regime"] = regime.values

    out["signal"] = signal.astype(int)
    return out
