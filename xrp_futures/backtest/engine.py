"""
Bar-by-bar backtest engine for a single-instrument LONG/SHORT/flat strategy.

Contract for `bars` (sorted by open_time, one row per bar):
  open_time, close_time, open, high, low, close   — from data/loader.py
  signal        int in {-1, 0, 1} — CAUSAL: signal.iloc[i] must only use information
                available up to and including bar i's close. It is applied starting
                at bar i+1's open (one-bar execution delay — no lookahead).
  atr           float — required if config.stop_type in ("atr", "chandelier")
  realized_vol_ann  float — annualized realized volatility as of bar i, used for
                volatility-targeted position sizing (falls back to config.target_vol,
                i.e. scalar 1.0, if missing/NaN so an early-warmup bar doesn't crash).

Funding (`funding`): calc_time, last_funding_rate — applied to whatever position is
open at each settlement, before that bar's own entry/exit logic runs.

This mirrors (and generalizes) the high/low-intrabar-fill pattern used in the legacy
spot grid bot's `strategy/grid.py:run_grid_simulation` — checked-in-bar stop/take
fills, no same-bar signal-to-fill lookahead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from xrp_futures.backtest.costs import CostModel, funding_pnl
from xrp_futures.risk import position_sizing as ps
from xrp_futures.risk import stops as stp
from xrp_futures.risk.engine import RiskEngine


@dataclass
class BacktestConfig:
    initial_equity: float = 10_000.0
    risk_pct: float = 0.005
    use_vol_targeting: bool = True  # False = pure risk-per-trade sizing (ablation baseline for A/B/C)
    target_vol: float = 0.15
    max_vol_scalar: float = 3.0
    stop_type: str = "atr"  # "atr" | "pct" | "chandelier"
    atr_multiplier: float = 2.0
    pct_stop: float = 0.03
    chandelier_window: int = 22
    chandelier_multiplier: float = 3.0
    trailing_enabled: bool = True
    cost_model: CostModel = field(default_factory=CostModel)


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: int
    entry_price: float
    exit_price: float
    notional_usdt: float
    gross_pnl: float
    fees_usdt: float
    funding_usdt: float
    net_pnl: float
    exit_reason: str  # "stop" | "signal" | "end_of_data"
    mfe_pct: float = 0.0        # Maximum Favorable Excursion: best unrealized move in the trade's favor, as % of entry
    mae_pct: float = 0.0        # Maximum Adverse Excursion: worst unrealized move against the trade, as % of entry (<=0)
    duration_hours: float = 0.0
    entry_stop_distance_pct: float = 0.0  # |entry - initial stop| / entry — the INITIAL distance, before any trailing


@dataclass
class BacktestResult:
    equity_curve: pd.Series  # indexed by open_time
    trades: list[Trade]
    bars: pd.DataFrame


class _OpenPosition:
    __slots__ = ("side", "notional", "entry_price", "stop_price", "entry_time", "entry_fee",
                 "funding_accum", "mfe_price", "mae_price", "entry_stop_distance_pct")

    def __init__(self, side, notional, entry_price, stop_price, entry_time, entry_fee):
        self.side = side
        self.notional = notional
        self.entry_price = entry_price
        self.stop_price = stop_price
        self.entry_time = entry_time
        self.entry_stop_distance_pct = stp.stop_distance_pct(entry_price, stop_price)
        self.entry_fee = entry_fee
        self.funding_accum = 0.0
        self.mfe_price = entry_price
        self.mae_price = entry_price


def _initial_stop(entry_price: float, side: int, bar: pd.Series, config: BacktestConfig) -> float:
    if config.stop_type == "pct":
        return stp.percentage_stop_price(entry_price, config.pct_stop, side)
    return stp.atr_stop_price(entry_price, bar["atr"], config.atr_multiplier, side)


def _trailing_candidate(bars: pd.DataFrame, i: int, side: int, config: BacktestConfig) -> float | None:
    if config.stop_type == "pct":
        return None  # a plain % stop does not trail
    if config.stop_type == "chandelier":
        window = config.chandelier_window
        lo = max(0, i - window + 1)
        sl = bars.iloc[lo:i + 1]
        atr_val = bars.iloc[i]["atr"]
        if side == 1:
            return sl["high"].max() - config.chandelier_multiplier * atr_val
        return sl["low"].min() + config.chandelier_multiplier * atr_val
    atr_val = bars.iloc[i]["atr"]
    close = bars.iloc[i]["close"]
    return stp.atr_stop_price(close, atr_val, config.atr_multiplier, side)


def run_backtest(
    bars: pd.DataFrame,
    funding: pd.DataFrame,
    config: BacktestConfig,
    risk_engine: RiskEngine | None = None,
) -> BacktestResult:
    """
    `risk_engine`, when given, gates and scales new entries (§12) on top of whatever
    the strategy signal wants — it never changes direction, only whether/how big a
    new position is allowed to be. Passing None (the default) reproduces the exact
    behavior of a plain strategy-only backtest (A/B/C); pair Strategy D with a
    configured RiskEngine to test "+ Risk Management" as its own ablation step.
    """
    bars = bars.reset_index(drop=True)
    n = len(bars)
    cost = config.cost_model

    equity = config.initial_equity
    equity_curve = np.empty(n)
    pos: _OpenPosition | None = None
    trades: list[Trade] = []
    pending_target: int | None = None

    funding_pairs = list(zip(funding["calc_time"], funding["last_funding_rate"])) if not funding.empty else []
    f_idx, n_funding = 0, len(funding_pairs)

    def close_position(fill_raw: float, exit_time: pd.Timestamp, exit_reason: str) -> None:
        nonlocal equity, pos
        fill = cost.slippage_adjusted_price(fill_raw, pos.side, is_entry=False)
        gross = pos.side * (fill - pos.entry_price) / pos.entry_price * pos.notional
        exit_fee = cost.fee_cost(pos.notional)
        total_fees = pos.entry_fee + exit_fee
        net = gross - total_fees + pos.funding_accum
        mfe_pct = pos.side * (pos.mfe_price - pos.entry_price) / pos.entry_price
        mae_pct = pos.side * (pos.mae_price - pos.entry_price) / pos.entry_price
        duration_hours = (exit_time - pos.entry_time).total_seconds() / 3600.0
        trades.append(Trade(
            entry_time=pos.entry_time, exit_time=exit_time, side=pos.side,
            entry_price=pos.entry_price, exit_price=fill, notional_usdt=pos.notional,
            gross_pnl=gross, fees_usdt=total_fees, funding_usdt=pos.funding_accum,
            net_pnl=net, exit_reason=exit_reason, mfe_pct=mfe_pct, mae_pct=mae_pct, duration_hours=duration_hours,
            entry_stop_distance_pct=pos.entry_stop_distance_pct,
        ))
        equity += gross - exit_fee
        pos = None
        if risk_engine is not None:
            risk_engine.register_trade_closed(net, i)

    for i in range(n):
        bar = bars.iloc[i]
        bar_start, bar_end = bar["open_time"], bar["close_time"]

        if risk_engine is not None:
            risk_engine.update_equity(equity, bar_start, i)

        # 1) Funding settlements inside this bar's interval, against the position held
        #    coming INTO the bar (before today's own entry/exit decisions).
        while f_idx < n_funding and funding_pairs[f_idx][0] < bar_end:
            f_time, f_rate = funding_pairs[f_idx]
            if f_time >= bar_start and pos is not None and cost.funding_enabled:
                f_pnl = funding_pnl(pos.side, pos.notional, f_rate)
                equity += f_pnl
                pos.funding_accum += f_pnl
            f_idx += 1

        # 2) Apply the pending entry/exit decided from the PREVIOUS bar's causal signal,
        #    filled at THIS bar's open.
        if pending_target is not None and pos is not None and pending_target != pos.side:
            close_position(bar["open"], bar["open_time"], "signal")
        entry_allowed = risk_engine is None or risk_engine.allow_new_entry(equity, i)
        if pending_target is not None and pending_target != 0 and pos is None and equity > 0 and entry_allowed:
            side = pending_target
            fill = cost.slippage_adjusted_price(bar["open"], side, is_entry=True)
            stop = _initial_stop(fill, side, bar, config)
            sdp = stp.stop_distance_pct(fill, stop)
            if config.use_vol_targeting:
                realized_vol = bar.get("realized_vol_ann")
                if realized_vol is None or not np.isfinite(realized_vol) or realized_vol <= 0:
                    realized_vol = config.target_vol  # neutral scalar (1.0) during vol warmup
                notional = ps.combined_notional(
                    equity_usdt=equity,
                    risk_pct=config.risk_pct,
                    stop_distance_pct=max(sdp, 1e-6),
                    target_vol=config.target_vol,
                    realized_vol=realized_vol,
                    max_vol_scalar=config.max_vol_scalar,
                )
            else:
                notional = ps.risk_per_trade_notional(
                    equity_usdt=equity,
                    risk_pct=config.risk_pct,
                    stop_distance_pct=max(sdp, 1e-6),
                ).notional_usdt
            if risk_engine is not None:
                notional *= risk_engine.size_multiplier(equity)
            if notional > 0:
                entry_fee = cost.fee_cost(notional)
                equity -= entry_fee
                pos = _OpenPosition(side, notional, fill, stop, bar["open_time"], entry_fee)
                if risk_engine is not None:
                    risk_engine.register_trade_opened()
        pending_target = None

        # 3) Intrabar stop check for whatever position is open after step 2. MFE/MAE are
        #    updated first using this bar's full range, even on the bar that ends up hitting
        #    the stop (price may have moved favorably — or adversely — before reversing).
        if pos is not None:
            pos.mfe_price = max(pos.mfe_price, bar["high"]) if pos.side == 1 else min(pos.mfe_price, bar["low"])
            pos.mae_price = min(pos.mae_price, bar["low"]) if pos.side == 1 else max(pos.mae_price, bar["high"])
            hit = (bar["low"] <= pos.stop_price) if pos.side == 1 else (bar["high"] >= pos.stop_price)
            if hit:
                gap_through = (bar["open"] < pos.stop_price) if pos.side == 1 else (bar["open"] > pos.stop_price)
                raw_fill = bar["open"] if gap_through else pos.stop_price
                close_position(raw_fill, bar["open_time"], "stop")
            elif config.trailing_enabled:
                candidate = _trailing_candidate(bars, i, pos.side, config)
                if candidate is not None:
                    pos.stop_price = stp.trailing_stop_update(pos.stop_price, candidate, pos.side)

        # 4) Decide the target for the NEXT bar from this bar's (causal) signal.
        pending_target = int(bar["signal"])
        equity_curve[i] = equity

    if pos is not None:
        last = bars.iloc[-1]
        close_position(last["close"], last["open_time"], "end_of_data")
        equity_curve[-1] = equity

    equity_series = pd.Series(equity_curve, index=bars["open_time"], name="equity")
    return BacktestResult(equity_curve=equity_series, trades=trades, bars=bars)
