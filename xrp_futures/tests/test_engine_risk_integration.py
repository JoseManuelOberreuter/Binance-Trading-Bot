import pandas as pd
import pytest

from xrp_futures.backtest.costs import CostModel
from xrp_futures.backtest.engine import BacktestConfig, run_backtest
from xrp_futures.risk.engine import RiskEngine, RiskEngineConfig


def _bars(rows):
    start = pd.Timestamp("2024-01-01", tz="UTC")
    data = []
    for i, (o, h, l, c, sig) in enumerate(rows):
        ot = start + pd.Timedelta(hours=i)
        data.append(dict(
            open_time=ot, close_time=ot + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
            open=o, high=h, low=l, close=c, signal=sig, atr=2.0, realized_vol_ann=0.15,
        ))
    return pd.DataFrame(data)


def _empty_funding():
    return pd.DataFrame(columns=["calc_time", "last_funding_rate"])


def test_risk_engine_none_reproduces_baseline_behavior():
    rows = [(100, 101, 99, 100, 0), (101, 102, 99, 101, 1), (101, 103, 100, 102, 1),
            (102, 103, 101, 102, 0), (102, 103, 101, 102, 0)]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="pct", pct_stop=0.5)
    baseline = run_backtest(bars, _empty_funding(), config, risk_engine=None)
    with_engine_but_permissive = run_backtest(bars, _empty_funding(), config, risk_engine=RiskEngine())
    assert baseline.equity_curve.tolist() == pytest.approx(with_engine_but_permissive.equity_curve.tolist())
    assert len(baseline.trades) == len(with_engine_but_permissive.trades) == 1


def test_kill_switch_blocks_reentry_after_large_drawdown():
    # A long that immediately gets stopped out hard should trip the kill switch and
    # prevent the NEXT signal from opening a new position at all.
    rows = [
        (100, 101, 99, 100, 0),
        (100, 101, 99, 100, 1),   # entry next bar
        (100, 101, 50, 51, 1),    # huge drop -> stop hit, big loss -> drawdown kill zone
        (51, 55, 50, 54, 1),      # signal still long, but kill switch should block re-entry
        (54, 60, 53, 59, 1),
    ]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="pct", pct_stop=0.05, risk_pct=0.5, use_vol_targeting=False)
    risk_cfg = RiskEngineConfig(drawdown_kill_pct=0.05)
    engine = RiskEngine(risk_cfg)
    result = run_backtest(bars, _empty_funding(), config, risk_engine=engine)

    assert engine.state.kill_switch_triggered is True
    # only the one (stopped-out) trade should exist -> no re-entry after the kill switch
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "stop"


def test_size_multiplier_scales_down_notional_on_reentry_after_drawdown():
    rows = [
        (100, 101, 99, 100, 0),
        (100, 101, 99, 100, 1),
        (100, 101, 88, 89, 1),    # stop hit (12% pct stop, risk_pct=10% -> ~10% drawdown, below kill)
        (89, 95, 88, 94, 1),      # re-entry allowed, but should be sized down
    ]
    bars = _bars(rows)
    # risk_pct sets the loss-at-stop as a share of equity directly (loss ~= equity * risk_pct
    # regardless of stop distance), so 10% risk_pct -> ~10% drawdown from the one stop-out.
    config = BacktestConfig(stop_type="pct", pct_stop=0.12, risk_pct=0.10, use_vol_targeting=False)
    # daily_loss_stop_pct also overridden -> isolates size_multiplier's drawdown-reduce
    # zone from the separate allow_new_entry daily-loss gate (default 3%, which this
    # single ~10% same-day loss would otherwise also trip, blocking re-entry entirely).
    risk_cfg = RiskEngineConfig(
        drawdown_reduce_pct=0.05, drawdown_reduce_multiplier=0.3, drawdown_kill_pct=0.90,
        daily_loss_reduce_pct=0.90, daily_loss_stop_pct=0.90, weekly_loss_reduce_pct=0.90,
    )
    engine_with_risk = RiskEngine(risk_cfg)
    result_with = run_backtest(bars, _empty_funding(), config, risk_engine=engine_with_risk)
    result_without = run_backtest(bars, _empty_funding(), config, risk_engine=None)

    assert len(result_with.trades) == 2
    assert len(result_without.trades) == 2
    # second trade's notional should be smaller WITH the risk engine's drawdown throttle
    assert result_with.trades[1].notional_usdt < result_without.trades[1].notional_usdt


def test_cooldown_blocks_immediate_reentry_after_a_loss():
    rows = [
        (100, 101, 99, 100, 0),
        (100, 101, 99, 100, 1),
        (100, 101, 90, 91, 0),   # stop hit -> loss, cooldown starts
        (91, 95, 90, 94, 1),     # would re-enter, but cooldown should block it
        (94, 96, 93, 95, 1),
    ]
    bars = _bars(rows)
    config = BacktestConfig(stop_type="pct", pct_stop=0.09, risk_pct=0.5, use_vol_targeting=False)
    risk_cfg = RiskEngineConfig(cooldown_bars_after_loss=5)
    engine = RiskEngine(risk_cfg)
    result = run_backtest(bars, _empty_funding(), config, risk_engine=engine)
    assert len(result.trades) == 1  # cooldown prevents the second entry within this short series
