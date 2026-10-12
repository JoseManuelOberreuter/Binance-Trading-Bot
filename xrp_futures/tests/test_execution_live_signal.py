import numpy as np
import pandas as pd
import pytest

from xrp_futures.backtest.engine import _trailing_candidate
from xrp_futures.config.live import LiveConfig
from xrp_futures.execution.live_signal import closed_only, compute_live_signal, klines_to_bars
from xrp_futures.strategies import strategy_a_trend as strat_a

CONFIG = LiveConfig()


def _raw_klines(n=1500, seed=0):
    """Binance-format 12h kline rows (ms timestamps, numeric strings) for a trending random walk."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2021-01-01", tz="UTC")
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.03, n))
    rows = []
    for i in range(n):
        ot = start + pd.Timedelta(hours=12 * i)
        o = close[i - 1] if i else 100.0
        h = max(o, close[i]) * (1 + abs(rng.normal(0, 0.01)))
        l = min(o, close[i]) * (1 - abs(rng.normal(0, 0.01)))
        ct = ot + pd.Timedelta(hours=12) - pd.Timedelta(milliseconds=1)
        rows.append([int(ot.value // 10**6), f"{o}", f"{h}", f"{l}", f"{close[i]}", "1000",
                     int(ct.value // 10**6), "100000", 50, "500", "50000", "0"])
    return rows


def test_klines_to_bars_parses_binance_rows():
    bars = klines_to_bars(_raw_klines(3))
    assert str(bars["open_time"].dt.tz) == "UTC"
    assert bars["close_time"].iloc[0] == pd.Timestamp("2021-01-01 11:59:59.999", tz="UTC")
    assert bars["close"].dtype == float


def test_closed_only_drops_forming_candle():
    bars = klines_to_bars(_raw_klines(3))
    now = bars["open_time"].iloc[2] + pd.Timedelta(hours=1)   # third candle still forming
    assert len(closed_only(bars, now)) == 2


def test_live_window_reproduces_full_history_backtest_signal():
    """
    The live bot only sees the last `bars_lookback` candles; the backtest sees the whole
    history. Signal, ATR and Chandelier levels must match at every decision point anyway.
    """
    bars = klines_to_bars(_raw_klines())
    full = strat_a.generate(bars, CONFIG.strategy)
    bt_config = CONFIG.backtest_config()
    n_checked = 0
    for end in range(CONFIG.bars_lookback, len(bars)):
        window = bars.iloc[end - CONFIG.bars_lookback + 1:end + 1]
        live = compute_live_signal("TEST", window, CONFIG)
        assert live.bar_open_time == full["open_time"].iloc[end]
        assert live.signal == full["signal"].iloc[end]
        assert live.atr == pytest.approx(full["atr"].iloc[end], rel=1e-9)
        assert live.chandelier_long == pytest.approx(_trailing_candidate(full, end, 1, bt_config), rel=1e-9)
        assert live.chandelier_short == pytest.approx(_trailing_candidate(full, end, -1, bt_config), rel=1e-9)
        n_checked += 1
    assert n_checked > 1000
    assert set(full["signal"].iloc[CONFIG.bars_lookback:]) == {-1, 1}   # both directions exercised


def test_rejects_too_short_history():
    with pytest.raises(ValueError):
        compute_live_signal("TEST", klines_to_bars(_raw_klines(30)), CONFIG)
