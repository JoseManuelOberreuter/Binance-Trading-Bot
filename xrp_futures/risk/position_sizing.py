"""
Pure position-sizing math. Two independent sizing mechanisms, combined by taking
the smaller (more conservative) of the two notionals:

1. Risk-per-trade sizing (§9): given equity, a max risk % of equity, and the
   distance to the stop, size so a stop-out loses exactly that much equity.
2. Volatility targeting (§8): scale exposure so annualized position volatility
   sits near a target, regardless of the stop distance.

Neither function touches leverage or margin — those are §3's separate concerns,
applied afterward by whatever turns a notional into an exchange order.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskPerTradeResult:
    equity: float
    risk_pct: float
    stop_distance_pct: float
    risk_amount_usdt: float
    notional_usdt: float


def risk_per_trade_notional(
    equity_usdt: float,
    risk_pct: float,
    stop_distance_pct: float,
) -> RiskPerTradeResult:
    """
    Size a position so that if the stop is hit, the loss equals `risk_pct` of equity.

    §9 worked example: equity=$10,000, risk_pct=0.5% -> risk_amount=$50.
      stop_distance_pct=2%  -> notional = 50 / 0.02 = $2,500
      stop_distance_pct=5%  -> notional = 50 / 0.05 = $1,000
    """
    if equity_usdt <= 0 or risk_pct <= 0 or stop_distance_pct <= 0:
        return RiskPerTradeResult(equity_usdt, risk_pct, stop_distance_pct, 0.0, 0.0)

    risk_amount = equity_usdt * risk_pct
    notional = risk_amount / stop_distance_pct
    return RiskPerTradeResult(equity_usdt, risk_pct, stop_distance_pct, risk_amount, notional)


def volatility_target_scalar(target_vol: float, realized_vol: float, max_scalar: float = 3.0) -> float:
    """
    Exposure multiplier = target_vol / realized_vol, clamped to [0, max_scalar] so a
    near-zero realized_vol reading can't blow the position up to an absurd multiple.
    """
    if realized_vol <= 0 or target_vol <= 0:
        return 0.0
    return min(target_vol / realized_vol, max_scalar)


def volatility_target_notional(
    equity_usdt: float,
    target_vol: float,
    realized_vol: float,
    max_scalar: float = 3.0,
) -> float:
    """Notional such that the position's *annualized* volatility ~= target_vol."""
    if equity_usdt <= 0:
        return 0.0
    scalar = volatility_target_scalar(target_vol, realized_vol, max_scalar)
    return equity_usdt * scalar


def combined_notional(
    equity_usdt: float,
    risk_pct: float,
    stop_distance_pct: float,
    target_vol: float,
    realized_vol: float,
    max_vol_scalar: float = 3.0,
) -> float:
    """
    The position notional actually used: the smaller of the risk-per-trade notional
    and the volatility-targeting notional. Volatility targeting keeps day-to-day risk
    stable across calm/turbulent regimes; risk-per-trade caps the worst case if the
    stop is hit. Taking the min means neither mechanism alone can push risk higher
    than the other allows.
    """
    risk_notional = risk_per_trade_notional(equity_usdt, risk_pct, stop_distance_pct).notional_usdt
    vol_notional = volatility_target_notional(equity_usdt, target_vol, realized_vol, max_vol_scalar)
    return min(risk_notional, vol_notional)
