import math

import pandas as pd
import pytest

from xrp_futures.backtest.costs import CostModel
from xrp_futures.backtest.engine import BacktestConfig, regime_risk_multiplier, run_backtest

MULTS = (2.0, 1.0, 0.5)


def _bars(side: int, regime: float):
    """Signal `side` from bar0 -> entry at bar1's open (101), sized from bar0 (atr=2)."""
    start = pd.Timestamp("2024-01-01", tz="UTC")
    rows = [(100, 101, 99, 100), (101, 102, 100, 101), (101, 102, 100, 101)]
    data = []
    for i, (o, h, l, c) in enumerate(rows):
        ot = start + pd.Timedelta(hours=i)
        data.append(dict(open_time=ot, close_time=ot + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
                         open=o, high=h, low=l, close=c, signal=side, atr=2.0, realized_vol_ann=0.15,
                         regime=regime))
    return pd.DataFrame(data)


def _config(**kw):
    return BacktestConfig(initial_equity=10_000.0, risk_pct=0.01, use_vol_targeting=False, stop_type="atr",
                          atr_multiplier=1.0, trailing_enabled=False,
                          cost_model=CostModel(taker_fee_pct=0.0, slippage_pct=0.0, funding_enabled=False), **kw)


def _entry_notional(bars, config):
    trades = run_backtest(bars, pd.DataFrame(columns=["calc_time", "last_funding_rate"]), config).trades
    return trades[0].notional_usdt if trades else 0.0


BASE_NOTIONAL = 10_000.0 * 0.01 / (2.0 / 101.0)  # equity x risk / stop distance


@pytest.mark.parametrize("side, regime, mult", [
    (1, 1, 2.0), (-1, -1, 2.0),     # with the regime
    (1, 0, 1.0), (-1, 0, 1.0),      # neutral
    (1, -1, 0.5), (-1, 1, 0.5),     # against
])
def test_regime_scales_entry_notional(side, regime, mult):
    notional = _entry_notional(_bars(side, regime), _config(regime_risk_multipliers=MULTS))
    assert notional == pytest.approx(BASE_NOTIONAL * mult)


def test_off_by_default_ignores_regime_column():
    assert _entry_notional(_bars(1, -1), _config()) == pytest.approx(BASE_NOTIONAL)


def test_zero_multiplier_skips_the_entry():
    assert _entry_notional(_bars(1, -1), _config(regime_risk_multipliers=(1.0, 1.0, 0.0))) == 0.0


def test_unknown_regime_counts_as_neutral():
    assert regime_risk_multiplier(1, math.nan, MULTS) == 1.0


def test_notional_cap_binds_after_regime_scaling():
    notional = _entry_notional(_bars(1, 1), _config(regime_risk_multipliers=MULTS, max_notional_pct=1.0))
    assert notional == pytest.approx(10_000.0)  # 2x of ~50,500 capped at 1x equity
