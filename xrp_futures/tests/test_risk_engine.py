import pandas as pd
import pytest

from xrp_futures.risk.engine import RiskEngine, RiskEngineConfig, approx_liquidation_distance_pct, liquidation_gate


def _ts(day_offset=0, hour=0):
    return pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=day_offset, hours=hour)


def test_size_multiplier_is_1_when_no_drawdown_or_losses():
    engine = RiskEngine()
    engine.update_equity(10_000.0, _ts(), 0)
    assert engine.size_multiplier(10_000.0) == pytest.approx(1.0)


def test_size_multiplier_reduces_after_drawdown_threshold():
    # daily/weekly thresholds set unreachable so this bar's drop only trips the
    # drawdown-from-peak dimension in isolation (they'd otherwise stack: same first
    # bar sets peak_equity, day_start_equity and week_start_equity all to 10,000).
    cfg = RiskEngineConfig(
        drawdown_reduce_pct=0.10, drawdown_reduce_multiplier=0.5,
        daily_loss_reduce_pct=0.99, weekly_loss_reduce_pct=0.99,
    )
    engine = RiskEngine(cfg)
    engine.update_equity(10_000.0, _ts(), 0)  # sets peak
    engine.update_equity(8_950.0, _ts(hour=1), 1)  # -10.5% drawdown -> should be in "reduce" zone
    assert engine.size_multiplier(8_950.0) == pytest.approx(0.5)


def test_size_multiplier_goes_defensive_then_zero_at_kill_threshold():
    cfg = RiskEngineConfig(
        drawdown_reduce_pct=0.10, drawdown_defensive_pct=0.15, drawdown_kill_pct=0.20,
        drawdown_defensive_multiplier=0.25,
        daily_loss_reduce_pct=0.99, weekly_loss_reduce_pct=0.99,
    )
    engine = RiskEngine(cfg)
    engine.update_equity(10_000.0, _ts(), 0)
    engine.update_equity(8_400.0, _ts(hour=1), 1)  # -16% -> defensive zone
    assert engine.size_multiplier(8_400.0) == pytest.approx(0.25)

    engine.update_equity(7_900.0, _ts(hour=2), 2)  # -21% -> kill zone
    assert engine.size_multiplier(7_900.0) == 0.0
    assert engine.state.kill_switch_triggered is True


def test_kill_switch_blocks_new_entries_even_after_partial_equity_recovery():
    cfg = RiskEngineConfig(drawdown_kill_pct=0.20)
    engine = RiskEngine(cfg)
    engine.update_equity(10_000.0, _ts(), 0)
    engine.update_equity(7_900.0, _ts(hour=1), 1)  # triggers kill switch
    assert engine.allow_new_entry(7_900.0, 1) is False

    # equity recovers somewhat, but kill switch is sticky (requires an explicit reset, not
    # just equity moving back up) — a strategy that hit its worst-case bar shouldn't auto-resume
    engine.update_equity(9_000.0, _ts(hour=2), 2)
    assert engine.allow_new_entry(9_000.0, 2) is False


def test_daily_loss_stop_blocks_new_entries_same_day_only():
    cfg = RiskEngineConfig(daily_loss_stop_pct=0.03)
    engine = RiskEngine(cfg)
    engine.update_equity(10_000.0, _ts(day_offset=0, hour=0), 0)
    engine.update_equity(9_600.0, _ts(day_offset=0, hour=5), 1)  # -4% same day -> blocked
    assert engine.allow_new_entry(9_600.0, 1) is False

    # next day: day_start_equity resets, loss-from-today is 0 again -> allowed
    engine.update_equity(9_600.0, _ts(day_offset=1, hour=0), 2)
    assert engine.allow_new_entry(9_600.0, 2) is True


def test_weekly_loss_reduces_size():
    cfg = RiskEngineConfig(weekly_loss_reduce_pct=0.05, weekly_reduce_multiplier=0.5)
    engine = RiskEngine(cfg)
    engine.update_equity(10_000.0, _ts(day_offset=0), 0)  # Monday, sets week baseline
    engine.update_equity(9_400.0, _ts(day_offset=2), 1)   # Wednesday, same week, -6%
    assert engine.size_multiplier(9_400.0) == pytest.approx(0.5)


def test_cooldown_after_loss_blocks_entries_for_n_bars():
    cfg = RiskEngineConfig(cooldown_bars_after_loss=3)
    engine = RiskEngine(cfg)
    engine.update_equity(10_000.0, _ts(), 0)
    engine.register_trade_closed(net_pnl=-50.0, bar_index=10)

    assert engine.allow_new_entry(10_000.0, 11) is False  # 1 bar later, still cooling down
    assert engine.allow_new_entry(10_000.0, 12) is False  # 2 bars later
    assert engine.allow_new_entry(10_000.0, 13) is True   # 3 bars later, cooldown over


def test_max_trades_per_day_blocks_additional_entries():
    cfg = RiskEngineConfig(max_trades_per_day=2)
    engine = RiskEngine(cfg)
    engine.update_equity(10_000.0, _ts(), 0)
    engine.register_trade_opened()
    engine.register_trade_opened()
    assert engine.allow_new_entry(10_000.0, 5) is False


def test_approx_liquidation_distance_scales_inversely_with_leverage():
    d1 = approx_liquidation_distance_pct(leverage=1.0, maintenance_margin_rate=0.005)
    d10 = approx_liquidation_distance_pct(leverage=10.0, maintenance_margin_rate=0.005)
    assert d1 == pytest.approx(0.995)
    assert d10 == pytest.approx(0.095)
    assert d1 > d10


def test_liquidation_gate_passes_at_1x_with_reasonable_stop():
    # 1x leverage: liquidation is ~99.5% away, any sane stop (e.g. 5%) clears easily
    assert liquidation_gate(stop_distance_pct=0.05, leverage=1.0, min_buffer_pct=0.30) is True


def test_liquidation_gate_blocks_high_leverage_with_wide_stop():
    # 10x leverage: liquidation distance ~9.5%; a 5% stop plus a 10% buffer doesn't fit
    assert liquidation_gate(stop_distance_pct=0.05, leverage=10.0, min_buffer_pct=0.10) is False
