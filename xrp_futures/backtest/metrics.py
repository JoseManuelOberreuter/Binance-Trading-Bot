"""
Performance metrics computed from a BacktestResult (§24). Every ratio is computed
from the equity curve (net of fees/slippage/funding) unless a "gross" variant is
explicitly named, so §33's acceptance bar is checked against the honest number.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from xrp_futures.backtest.engine import BacktestResult, Trade
from xrp_futures.indicators.volatility import BARS_PER_YEAR


def equity_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def cagr(equity: pd.Series, timeframe: str) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    n_bars = len(equity) - 1
    years = n_bars / BARS_PER_YEAR[timeframe]
    if years <= 0:
        return 0.0
    ratio = equity.iloc[-1] / equity.iloc[0]
    if ratio <= 0:
        return -1.0
    return ratio ** (1 / years) - 1


def annualized_volatility(returns: pd.Series, timeframe: str) -> float:
    if len(returns) < 2:
        return 0.0
    return returns.std() * np.sqrt(BARS_PER_YEAR[timeframe])


def sharpe_ratio(returns: pd.Series, timeframe: str, rf_annual: float = 0.0) -> float:
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    bars_per_year = BARS_PER_YEAR[timeframe]
    rf_per_bar = (1 + rf_annual) ** (1 / bars_per_year) - 1
    excess = returns - rf_per_bar
    return (excess.mean() / excess.std()) * np.sqrt(bars_per_year)


def sortino_ratio(returns: pd.Series, timeframe: str, rf_annual: float = 0.0) -> float:
    if len(returns) < 2:
        return 0.0
    bars_per_year = BARS_PER_YEAR[timeframe]
    rf_per_bar = (1 + rf_annual) ** (1 / bars_per_year) - 1
    excess = returns - rf_per_bar
    downside = excess[excess < 0]
    downside_std = downside.std() if len(downside) > 1 else 0.0
    if not downside_std:
        return 0.0
    return (excess.mean() / downside_std) * np.sqrt(bars_per_year)


def drawdown_series(equity: pd.Series) -> pd.Series:
    running_max = equity.cummax()
    return equity / running_max - 1.0


def max_drawdown(equity: pd.Series) -> float:
    dd = drawdown_series(equity)
    return dd.min() if len(dd) else 0.0


def average_drawdown(equity: pd.Series) -> float:
    dd = drawdown_series(equity)
    underwater = dd[dd < 0]
    return underwater.mean() if len(underwater) else 0.0


def time_under_water_pct(equity: pd.Series) -> float:
    dd = drawdown_series(equity)
    return float((dd < 0).mean()) if len(dd) else 0.0


def calmar_ratio(cagr_value: float, max_dd: float) -> float:
    if max_dd == 0:
        return 0.0
    return cagr_value / abs(max_dd)


def best_worst_month(equity: pd.Series) -> tuple[float, float]:
    monthly = equity.resample("ME").last().dropna()
    rets = monthly.pct_change().dropna()
    if rets.empty:
        return 0.0, 0.0
    return rets.max(), rets.min()


def longest_losing_streak(trades: list[Trade]) -> int:
    streak = longest = 0
    for t in trades:
        if t.net_pnl < 0:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
    return longest


def trade_stats(trades: list[Trade]) -> dict:
    if not trades:
        return dict(
            total_trades=0, win_rate=0.0, profit_factor=0.0, expectancy_usdt=0.0,
            avg_trade_usdt=0.0, gross_pnl_usdt=0.0, net_pnl_usdt=0.0,
            fees_usdt=0.0, funding_usdt=0.0, longest_losing_streak=0,
            avg_win_usdt=0.0, avg_loss_usdt=0.0, avg_duration_hours=0.0, avg_mfe_pct=0.0,
            top5_contribution_pct=0.0, top10_contribution_pct=0.0,
        )
    net = np.array([t.net_pnl for t in trades])
    gross = np.array([t.gross_pnl for t in trades])
    fees = np.array([t.fees_usdt for t in trades])
    funding = np.array([t.funding_usdt for t in trades])
    durations = np.array([t.duration_hours for t in trades])
    mfes = np.array([t.mfe_pct for t in trades])
    wins = net[net > 0]
    losses = net[net < 0]
    gross_profit = wins.sum() if len(wins) else 0.0
    gross_loss = abs(losses.sum()) if len(losses) else 0.0
    return dict(
        total_trades=len(trades),
        win_rate=len(wins) / len(trades),
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0,
        expectancy_usdt=net.mean(),
        avg_trade_usdt=net.mean(),
        avg_win_usdt=wins.mean() if len(wins) else 0.0,
        avg_loss_usdt=losses.mean() if len(losses) else 0.0,
        avg_duration_hours=durations.mean(),
        avg_mfe_pct=mfes.mean(),
        top5_contribution_pct=top_n_contribution(trades, 5),
        top10_contribution_pct=top_n_contribution(trades, 10),
        gross_pnl_usdt=gross.sum(),
        net_pnl_usdt=net.sum(),
        fees_usdt=fees.sum(),
        funding_usdt=funding.sum(),
        longest_losing_streak=longest_losing_streak(trades),
    )


def top_n_contribution(trades: list[Trade], n: int) -> float | None:
    """
    Fraction of TOTAL net PnL contributed by the N single best trades — a high value
    (e.g. >0.8) means the strategy's result depends heavily on a handful of outsized
    winners, exactly what a trend-following/breakout system is EXPECTED to look like
    (small losses funding rare big captures) rather than a red flag on its own.

    Returns None (not a number) when the total is too close to zero relative to the
    scale of the trades themselves — dividing by a near-breakeven total can produce
    wild, meaningless swings (e.g. -1300%, +1600%) even though nothing is actually
    wrong; that's an unstable ratio, not a real reading, so it's reported as unknown
    rather than as a specific (misleading) number.
    """
    if not trades:
        return 0.0
    net_sorted = sorted((t.net_pnl for t in trades), reverse=True)
    total = sum(net_sorted)
    scale = max(abs(v) for v in net_sorted)
    if scale == 0 or abs(total) < 0.25 * scale:
        return None
    top = sum(net_sorted[:n])
    return top / total


def side_breakdown(trades: list[Trade]) -> dict:
    out = {}
    for label, side in (("long", 1), ("short", -1)):
        side_trades = [t for t in trades if t.side == side]
        out[label] = trade_stats(side_trades)
    return out


def per_year_returns(equity: pd.Series) -> pd.Series:
    yearly = equity.resample("YE").last().dropna()
    return yearly.pct_change().dropna()


def realized_move_pct(trade: Trade) -> float:
    """Price-based realized return (side-adjusted), the same units as Trade.mfe_pct
    so the two are directly comparable — e.g. mfe_pct=0.15, realized=0.04 means the
    trade was up 15% at best and gave most of it back before/at exit."""
    if trade.entry_price == 0:
        return 0.0
    return trade.side * (trade.exit_price - trade.entry_price) / trade.entry_price


def mfe_efficiency_stats(trades: list[Trade]) -> dict:
    """
    How much of the average favorable excursion actually ends up realized at exit —
    "how much are we leaving on the table before/when we get out". Computed as a
    ratio of the AGGREGATE averages (not an average of per-trade ratios, which blows
    up whenever a trade's own MFE is near zero) — same stability reasoning as
    top_n_contribution's guard.

    capture_efficiency close to 1.0: exits capture most of the favorable move.
    Close to 0 or negative: the trailing stop (or reversal) is giving back most/all
    of the favorable excursion before exit — the stop is likely too tight for the
    moves this strategy is trying to ride.
    """
    if not trades:
        return dict(avg_mfe_pct=0.0, avg_realized_pct=0.0, capture_efficiency=None)
    mfes = [t.mfe_pct for t in trades]
    realized = [realized_move_pct(t) for t in trades]
    avg_mfe = sum(mfes) / len(mfes)
    avg_realized = sum(realized) / len(realized)
    efficiency = (avg_realized / avg_mfe) if abs(avg_mfe) > 1e-9 else None
    return dict(avg_mfe_pct=avg_mfe, avg_realized_pct=avg_realized, capture_efficiency=efficiency)


def compute_metrics(result: BacktestResult, timeframe: str) -> dict:
    equity = result.equity_curve
    rets = equity_returns(equity)
    dd = drawdown_series(equity)
    cagr_v = cagr(equity, timeframe)
    mdd = max_drawdown(equity)
    best_m, worst_m = best_worst_month(equity)
    t_stats = trade_stats(result.trades)
    mfe_stats = mfe_efficiency_stats(result.trades)

    return dict(
        cagr=cagr_v,
        annualized_return=cagr_v,
        annualized_volatility=annualized_volatility(rets, timeframe),
        sharpe=sharpe_ratio(rets, timeframe),
        sortino=sortino_ratio(rets, timeframe),
        calmar=calmar_ratio(cagr_v, mdd),
        max_drawdown=mdd,
        average_drawdown=average_drawdown(equity),
        time_under_water_pct=time_under_water_pct(equity),
        best_month=best_m,
        worst_month=worst_m,
        per_year_returns=per_year_returns(equity),
        long_vs_short=side_breakdown(result.trades),
        initial_equity=equity.iloc[0] if len(equity) else 0.0,
        final_equity=equity.iloc[-1] if len(equity) else 0.0,
        avg_realized_pct=mfe_stats["avg_realized_pct"],
        mfe_capture_efficiency=mfe_stats["capture_efficiency"],
        **t_stats,
    )
