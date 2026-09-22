"""
Portfolio-level Risk Engine (§12): sits ABOVE any single strategy's entry signal
and decides whether a new trade is allowed at all, and how much its size should be
scaled down given recent losses/drawdown. It never decides direction — only
gates and scales what the strategy already wants to do. This is what turns
"Strategy D" into "Trend + Momentum + Vol Targeting + Risk Management": the signal
logic is identical to Strategy C, this module is the "+ Risk Management" part.

The specific thresholds (§12) are explicitly research parameters, not settled
defaults — RiskEngineConfig exists so they can be swept like everything else.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class RiskEngineConfig:
    daily_loss_reduce_pct: float = 0.02     # daily loss > 2% -> reduce new-entry size
    daily_loss_stop_pct: float = 0.03       # daily loss > 3% -> block new entries for the rest of the day
    weekly_loss_reduce_pct: float = 0.05     # weekly loss > 5% -> reduce size substantially
    drawdown_reduce_pct: float = 0.10        # drawdown from peak equity > 10% -> reduce sizing
    drawdown_defensive_pct: float = 0.15     # > 15% -> defensive mode (much smaller size)
    drawdown_kill_pct: float = 0.20          # > 20% -> kill switch: stop opening new trades entirely
    daily_reduce_multiplier: float = 0.5
    weekly_reduce_multiplier: float = 0.5
    drawdown_reduce_multiplier: float = 0.5
    drawdown_defensive_multiplier: float = 0.25
    cooldown_bars_after_loss: int = 0        # bars to wait after a losing trade before a new entry
    max_trades_per_day: int | None = None
    min_liquidation_distance_pct: float = 0.30  # stop must trigger at least this far from liquidation


@dataclass
class RiskEngineState:
    peak_equity: float = 0.0
    day_key: object = None
    week_key: object = None
    day_start_equity: float = 0.0
    week_start_equity: float = 0.0
    trades_today: int = 0
    last_loss_bar_index: int | None = None
    kill_switch_triggered: bool = False


class RiskEngine:
    def __init__(self, config: RiskEngineConfig | None = None):
        self.config = config or RiskEngineConfig()
        self.state = RiskEngineState()

    def update_equity(self, equity: float, bar_time: pd.Timestamp, bar_index: int) -> None:
        """Call once per bar (before entry decisions) to roll day/week trackers and peak equity."""
        s, cfg = self.state, self.config
        if s.peak_equity <= 0:
            s.peak_equity = equity
        s.peak_equity = max(s.peak_equity, equity)

        day_key = bar_time.date()
        if s.day_key != day_key:
            s.day_key = day_key
            s.day_start_equity = equity
            s.trades_today = 0

        week_key = (bar_time.isocalendar().year, bar_time.isocalendar().week)
        if s.week_key != week_key:
            s.week_key = week_key
            s.week_start_equity = equity

        drawdown = (equity / s.peak_equity - 1.0) if s.peak_equity > 0 else 0.0
        if drawdown <= -cfg.drawdown_kill_pct:
            s.kill_switch_triggered = True

    def _daily_loss_pct(self, equity: float) -> float:
        if self.state.day_start_equity <= 0:
            return 0.0
        return 1.0 - equity / self.state.day_start_equity

    def _weekly_loss_pct(self, equity: float) -> float:
        if self.state.week_start_equity <= 0:
            return 0.0
        return 1.0 - equity / self.state.week_start_equity

    def _drawdown_pct(self, equity: float) -> float:
        if self.state.peak_equity <= 0:
            return 0.0
        return 1.0 - equity / self.state.peak_equity

    def allow_new_entry(self, equity: float, bar_index: int) -> bool:
        s, cfg = self.state, self.config
        if s.kill_switch_triggered:
            return False
        if self._daily_loss_pct(equity) >= cfg.daily_loss_stop_pct:
            return False
        if cfg.max_trades_per_day is not None and s.trades_today >= cfg.max_trades_per_day:
            return False
        if cfg.cooldown_bars_after_loss > 0 and s.last_loss_bar_index is not None:
            if bar_index - s.last_loss_bar_index < cfg.cooldown_bars_after_loss:
                return False
        return True

    def size_multiplier(self, equity: float) -> float:
        """
        Combined scale-down factor in [0, 1] from drawdown, daily loss, and weekly
        loss zones. Multipliers stack (multiplicatively) since each represents an
        independent reason to trade smaller, not alternative severities of one thing.
        """
        cfg = self.config
        mult = 1.0

        dd = self._drawdown_pct(equity)
        if dd >= cfg.drawdown_kill_pct:
            return 0.0
        elif dd >= cfg.drawdown_defensive_pct:
            mult *= cfg.drawdown_defensive_multiplier
        elif dd >= cfg.drawdown_reduce_pct:
            mult *= cfg.drawdown_reduce_multiplier

        if self._daily_loss_pct(equity) >= cfg.daily_loss_reduce_pct:
            mult *= cfg.daily_reduce_multiplier
        if self._weekly_loss_pct(equity) >= cfg.weekly_loss_reduce_pct:
            mult *= cfg.weekly_reduce_multiplier

        return mult

    def register_trade_opened(self) -> None:
        self.state.trades_today += 1

    def register_trade_closed(self, net_pnl: float, bar_index: int) -> None:
        if net_pnl < 0:
            self.state.last_loss_bar_index = bar_index


def approx_liquidation_distance_pct(leverage: float, maintenance_margin_rate: float = 0.005) -> float:
    """
    Rough (isolated-margin, no fees) distance from entry to liquidation as a
    fraction of entry price: roughly 1/leverage minus the maintenance margin rate.
    Conservative approximation for research-stage gating, NOT an exchange-accurate
    liquidation calculator — the live-execution phase must use Binance's actual
    liquidation price formula/endpoint before this gate is trusted with real capital.
    """
    if leverage <= 0:
        return 0.0
    return max(0.0, 1.0 / leverage - maintenance_margin_rate)


def liquidation_gate(
    stop_distance_pct: float,
    leverage: float,
    min_buffer_pct: float,
    maintenance_margin_rate: float = 0.005,
) -> bool:
    """
    True if the position's stop is safely inside the liquidation distance, i.e. the
    strategy's own stop will always trigger before liquidation could, with at least
    `min_buffer_pct` of price room to spare (§15: never let a strategy stop sit
    dangerously close to liquidation).
    """
    liq_distance = approx_liquidation_distance_pct(leverage, maintenance_margin_rate)
    return (stop_distance_pct + min_buffer_pct) <= liq_distance
