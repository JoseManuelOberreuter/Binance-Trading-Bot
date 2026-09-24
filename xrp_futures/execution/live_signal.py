"""
Live signal (Phase 2, docs/execution_plan.md): fetch recent CLOSED 12h klines, run the
exact backtest strategy function on them, and return what the bot needs for one symbol
at the last closed candle — target direction, ATR, and the Chandelier trailing levels.

No stateful/incremental re-implementation of Donchian/ATR/Chandelier: the same
`strategy_a_trend.generate` and the engine's trailing-stop candidate are reused, so the
live path cannot drift from the backtest (tests/test_execution_live_signal.py).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from binance.client import Client

from xrp_futures.backtest.engine import _trailing_candidate
from xrp_futures.config.live import LiveConfig
from xrp_futures.data.binance_vision import KLINE_COLUMNS
from xrp_futures.strategies import strategy_a_trend as strat_a

_FLOAT_COLUMNS = ["open", "high", "low", "close", "volume", "quote_volume",
                  "taker_buy_volume", "taker_buy_quote_volume"]


@dataclass(frozen=True)
class LiveSignal:
    symbol: str
    bar_open_time: pd.Timestamp      # the last CLOSED candle the decision is based on
    close: float
    signal: int                      # target position: 1 long, -1 short, 0 flat
    atr: float
    chandelier_long: float           # trailing-stop candidate if holding a LONG
    chandelier_short: float          # trailing-stop candidate if holding a SHORT


def klines_to_bars(raw: list[list]) -> pd.DataFrame:
    """Binance kline rows -> the same column layout/types data/loader.py produces."""
    df = pd.DataFrame(raw, columns=KLINE_COLUMNS).drop(columns=["ignore"])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df[_FLOAT_COLUMNS] = df[_FLOAT_COLUMNS].astype(float)
    df["count"] = df["count"].astype(int)
    return df


def closed_only(bars: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Drop the still-forming candle (Binance returns it as the last row)."""
    return bars[bars["close_time"] < now].reset_index(drop=True)


def fetch_closed_bars(client: Client, symbol: str, config: LiveConfig, now: pd.Timestamp | None = None) -> pd.DataFrame:
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    raw = client.futures_klines(symbol=symbol, interval=config.strategy.timeframe, limit=config.bars_lookback + 1)
    return closed_only(klines_to_bars(raw), now)


def compute_live_signal(symbol: str, bars: pd.DataFrame, config: LiveConfig) -> LiveSignal:
    """Signal/ATR/Chandelier levels at the last row of `bars` (which must all be closed)."""
    if len(bars) < config.strategy.donchian_window + config.strategy.atr_window:
        raise ValueError(f"{symbol}: only {len(bars)} bars, not enough warmup")
    out = strat_a.generate(bars.reset_index(drop=True), config.strategy)
    i = len(out) - 1
    bt_config = config.backtest_config()
    last = out.iloc[i]
    return LiveSignal(
        symbol=symbol,
        bar_open_time=last["open_time"],
        close=float(last["close"]),
        signal=int(last["signal"]),
        atr=float(last["atr"]),
        chandelier_long=float(_trailing_candidate(out, i, 1, bt_config)),
        chandelier_short=float(_trailing_candidate(out, i, -1, bt_config)),
    )
