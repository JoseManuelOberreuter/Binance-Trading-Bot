import pytest

from xrp_futures.risk import position_sizing as ps


def test_risk_per_trade_matches_spec_example_2pct_stop():
    # §9: equity=$10,000, risk=0.5% -> $50 risk. stop at 2% -> notional ~= $2,500.
    result = ps.risk_per_trade_notional(equity_usdt=10_000, risk_pct=0.005, stop_distance_pct=0.02)
    assert result.risk_amount_usdt == pytest.approx(50.0)
    assert result.notional_usdt == pytest.approx(2_500.0)


def test_risk_per_trade_matches_spec_example_5pct_stop():
    # Same equity/risk, wider 5% stop -> smaller notional, ~= $1,000.
    result = ps.risk_per_trade_notional(equity_usdt=10_000, risk_pct=0.005, stop_distance_pct=0.05)
    assert result.notional_usdt == pytest.approx(1_000.0)


@pytest.mark.parametrize("bad_input", [
    dict(equity_usdt=0, risk_pct=0.005, stop_distance_pct=0.02),
    dict(equity_usdt=10_000, risk_pct=0, stop_distance_pct=0.02),
    dict(equity_usdt=10_000, risk_pct=0.005, stop_distance_pct=0),
])
def test_risk_per_trade_degenerate_inputs_return_zero(bad_input):
    result = ps.risk_per_trade_notional(**bad_input)
    assert result.notional_usdt == 0.0


def test_volatility_target_scalar_basic():
    # target 15% annualized vol, realized 15% -> scalar 1.0 (full equity notional)
    assert ps.volatility_target_scalar(target_vol=0.15, realized_vol=0.15) == pytest.approx(1.0)
    # calmer market than target -> scale UP exposure (bounded by max_scalar)
    assert ps.volatility_target_scalar(target_vol=0.15, realized_vol=0.05) == pytest.approx(3.0)
    # more turbulent market than target -> scale DOWN exposure
    assert ps.volatility_target_scalar(target_vol=0.15, realized_vol=0.30) == pytest.approx(0.5)


def test_volatility_target_scalar_clamped_to_max():
    scalar = ps.volatility_target_scalar(target_vol=0.20, realized_vol=0.01, max_scalar=3.0)
    assert scalar == 3.0


def test_volatility_target_scalar_zero_realized_vol_is_zero_not_infinite():
    assert ps.volatility_target_scalar(target_vol=0.15, realized_vol=0.0) == 0.0


def test_volatility_target_notional_scales_with_equity():
    notional = ps.volatility_target_notional(equity_usdt=10_000, target_vol=0.15, realized_vol=0.15)
    assert notional == pytest.approx(10_000.0)


def test_combined_notional_takes_the_more_conservative_side():
    # Risk-per-trade allows $2,500 (0.5% risk / 2% stop); vol targeting allows $500
    # (very turbulent market) -> combined must be the smaller of the two.
    combined = ps.combined_notional(
        equity_usdt=10_000,
        risk_pct=0.005,
        stop_distance_pct=0.02,
        target_vol=0.15,
        realized_vol=3.0,  # extreme realized vol -> tiny vol-target notional
    )
    vol_notional = ps.volatility_target_notional(10_000, 0.15, 3.0)
    assert combined == pytest.approx(vol_notional)
    assert combined < 2_500.0
