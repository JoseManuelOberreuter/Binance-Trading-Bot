"""
Shared-capital portfolio backtest: several symbols trade out of ONE equity, bar by
bar, the way the live bot runs them. This is unlike backtest/portfolio.py, which
combines independent per-symbol sleeves after the fact.

Per symbol, the mechanics are exactly run_backtest's: funding before the bar's own
decisions, entry/exit at the open from the previous bar's causal signal, sizing and
initial stop from the last closed bar, intrabar stop fills, trailing. A one-symbol
run reproduces run_backtest exactly (tested). What sharing capital adds:

  - every entry is sized on the shared (realized) equity;
  - `max_total_notional_pct` mirrors the live portfolio exposure cap
    (execution/sizing.py, LiveConfig.max_total_notional_pct). An entry that would
    push open notional above cap x equity is SKIPPED, never downsized, same as live;
  - within a bar: signal exits for all symbols first, then entries in symbol order
    (the order the dict is given in), then stop checks.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from xrp_futures.backtest.costs import funding_pnl
from xrp_futures.backtest.engine import (
    BacktestConfig, BacktestResult, Trade, _initial_stop, _OpenPosition, _trailing_candidate,
    regime_risk_multiplier,
)
from xrp_futures.risk import position_sizing as ps
from xrp_futures.risk import stops as stp


@dataclass
class PortfolioRun:
    result: BacktestResult                 # shared equity curves + every trade
    trades_by_symbol: dict[str, list[Trade]]
    skipped_entries: int                   # entries blocked by the exposure cap


def drawdown_multiplier(drawdown: float, steps: tuple[tuple[float, float], ...]) -> float:
    """Risk multiplier for a drawdown (positive fraction below peak): the multiplier of
    the deepest step reached, 1.0 above all steps."""
    mult = 1.0
    for threshold, m in sorted(steps):
        if drawdown >= threshold:
            mult = m
    return mult


def run_portfolio_backtest(
    bars_by_symbol: dict[str, pd.DataFrame],
    funding_by_symbol: dict[str, pd.DataFrame],
    config: BacktestConfig,
    max_total_notional_pct: float | None = None,
    drawdown_risk_steps: tuple[tuple[float, float], ...] | None = None,
) -> PortfolioRun:
    if config.use_vol_targeting:
        raise ValueError("portfolio engine supports risk-per-trade sizing only (use_vol_targeting=False)")
    cost = config.cost_model
    symbols = list(bars_by_symbol)
    bars = {s: bars_by_symbol[s].reset_index(drop=True) for s in symbols}
    row_at = {s: dict(zip(bars[s]["open_time"], range(len(bars[s])))) for s in symbols}
    timeline = sorted(set().union(*(set(b["open_time"]) for b in bars.values())))
    funding = {}
    for s in symbols:
        f = funding_by_symbol.get(s, pd.DataFrame())
        funding[s] = list(zip(f["calc_time"], f["last_funding_rate"])) if not f.empty else []
    f_idx = {s: 0 for s in symbols}

    equity = config.initial_equity
    pos: dict[str, _OpenPosition | None] = {s: None for s in symbols}
    pending: dict[str, int | None] = {s: None for s in symbols}
    last_close = {s: np.nan for s in symbols}
    trades = {s: [] for s in symbols}
    eq_curve, mtm_curve = [], []
    skipped = 0
    peak_mtm = config.initial_equity
    dd_mult = 1.0

    def close_position(s: str, fill_raw: float, exit_time: pd.Timestamp, exit_reason: str) -> None:
        nonlocal equity
        p = pos[s]
        fill = cost.slippage_adjusted_price(fill_raw, p.side, is_entry=False)
        gross = p.side * (fill - p.entry_price) / p.entry_price * p.notional
        exit_fee = cost.fee_cost(p.notional)
        trades[s].append(Trade(
            entry_time=p.entry_time, exit_time=exit_time, side=p.side,
            entry_price=p.entry_price, exit_price=fill, notional_usdt=p.notional,
            gross_pnl=gross, fees_usdt=p.entry_fee + exit_fee, funding_usdt=p.funding_accum,
            net_pnl=gross - p.entry_fee - exit_fee + p.funding_accum, exit_reason=exit_reason,
            mfe_pct=p.side * (p.mfe_price - p.entry_price) / p.entry_price,
            mae_pct=p.side * (p.mae_price - p.entry_price) / p.entry_price,
            duration_hours=(exit_time - p.entry_time).total_seconds() / 3600.0,
            entry_stop_distance_pct=p.entry_stop_distance_pct,
        ))
        equity += gross - exit_fee
        pos[s] = None

    for t in timeline:
        active = [(s, row_at[s][t]) for s in symbols if t in row_at[s]]
        rows = {s: bars[s].iloc[i] for s, i in active}

        # 1) Funding inside each bar, against positions held coming into it.
        for s, _ in active:
            bar_start, bar_end = rows[s]["open_time"], rows[s]["close_time"]
            while f_idx[s] < len(funding[s]) and funding[s][f_idx[s]][0] < bar_end:
                f_time, f_rate = funding[s][f_idx[s]]
                if f_time >= bar_start and pos[s] is not None and cost.funding_enabled:
                    f_pnl = funding_pnl(pos[s].side, pos[s].notional, f_rate)
                    equity += f_pnl
                    pos[s].funding_accum += f_pnl
                f_idx[s] += 1

        # 2) Signal exits at the open, all symbols before any entry.
        for s, _ in active:
            if pending[s] is not None and pos[s] is not None and pending[s] != pos[s].side:
                close_position(s, rows[s]["open"], rows[s]["open_time"], "signal")

        # 3) Entries at the open, sized on the shared equity from the last closed bar.
        for s, i in active:
            side = pending[s]
            if side is not None and side != 0 and pos[s] is None and equity > 0:
                bar = rows[s]
                fill = cost.slippage_adjusted_price(bar["open"], side, is_entry=True)
                decision_bar = bars[s].iloc[i - 1]
                stop = _initial_stop(fill, side, decision_bar, config)
                sdp = stp.stop_distance_pct(fill, stop)
                notional = ps.risk_per_trade_notional(
                    equity_usdt=equity, risk_pct=config.risk_pct, stop_distance_pct=max(sdp, 1e-6),
                ).notional_usdt
                if config.regime_risk_multipliers is not None:
                    notional *= regime_risk_multiplier(side, decision_bar["regime"], config.regime_risk_multipliers)
                notional *= dd_mult
                if config.max_notional_pct is not None:
                    notional = min(notional, config.max_notional_pct * equity)
                open_notional = sum(p.notional for p in pos.values() if p is not None)
                if notional > 0 and max_total_notional_pct is not None \
                        and open_notional + notional > max_total_notional_pct * equity:
                    skipped += 1
                elif notional > 0:
                    entry_fee = cost.fee_cost(notional)
                    equity -= entry_fee
                    pos[s] = _OpenPosition(side, notional, fill, stop, bar["open_time"], entry_fee)
            pending[s] = None

        # 4) Intrabar stops and trailing.
        for s, i in active:
            p, bar = pos[s], rows[s]
            if p is None:
                continue
            p.mfe_price = max(p.mfe_price, bar["high"]) if p.side == 1 else min(p.mfe_price, bar["low"])
            p.mae_price = min(p.mae_price, bar["low"]) if p.side == 1 else max(p.mae_price, bar["high"])
            hit = (bar["low"] <= p.stop_price) if p.side == 1 else (bar["high"] >= p.stop_price)
            if hit:
                gap_through = (bar["open"] < p.stop_price) if p.side == 1 else (bar["open"] > p.stop_price)
                close_position(s, bar["open"] if gap_through else p.stop_price, bar["open_time"], "stop")
            elif config.trailing_enabled:
                candidate = _trailing_candidate(bars[s], i, p.side, config)
                if candidate is not None:
                    p.stop_price = stp.trailing_stop_update(p.stop_price, candidate, p.side)

        # 5) Next bar's targets from this bar's causal signal; mark to market.
        for s, _ in active:
            pending[s] = int(rows[s]["signal"])
            last_close[s] = rows[s]["close"]
        unrealized = sum(p.side * (last_close[s] - p.entry_price) / p.entry_price * p.notional
                         for s, p in pos.items() if p is not None)
        eq_curve.append(equity)
        mtm_curve.append(equity + unrealized)
        if drawdown_risk_steps:
            peak_mtm = max(peak_mtm, mtm_curve[-1])
            dd_mult = drawdown_multiplier(1 - mtm_curve[-1] / peak_mtm, drawdown_risk_steps)

    for s in symbols:
        if pos[s] is not None:
            last = bars[s].iloc[-1]
            close_position(s, last["close"], last["open_time"], "end_of_data")
    if eq_curve:
        eq_curve[-1] = mtm_curve[-1] = equity

    index = pd.DatetimeIndex(timeline, name="open_time")
    all_trades = sorted((tr for ts in trades.values() for tr in ts), key=lambda tr: tr.exit_time)
    result = BacktestResult(
        equity_curve=pd.Series(eq_curve, index=index, name="equity"), trades=all_trades,
        bars=pd.DataFrame(), mtm_equity_curve=pd.Series(mtm_curve, index=index, name="equity"),
    )
    return PortfolioRun(result=result, trades_by_symbol=trades, skipped_entries=skipped)
