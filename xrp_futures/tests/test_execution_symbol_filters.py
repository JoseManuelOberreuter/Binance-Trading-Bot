from decimal import Decimal

import pytest

from xrp_futures.execution.symbol_filters import filters_for, parse_symbol_filters


def _info(symbol="XRPUSDT", tick="0.0001", step="0.1", min_qty="0.1", min_notional="5"):
    return {
        "symbol": symbol,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": tick, "minPrice": "0.0001", "maxPrice": "10000"},
            {"filterType": "LOT_SIZE", "stepSize": "999", "minQty": "999", "maxQty": "1000000"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": step, "minQty": min_qty, "maxQty": "1000000"},
            {"filterType": "MIN_NOTIONAL", "notional": min_notional},
        ],
    }


def test_parse_prefers_market_lot_size():
    f = parse_symbol_filters(_info())
    assert f.step_size == Decimal("0.1")
    assert f.min_qty == Decimal("0.1")
    assert f.tick_size == Decimal("0.0001")
    assert f.min_notional == Decimal("5")


def test_round_qty_always_rounds_down_without_float_drift():
    f = parse_symbol_filters(_info(step="0.1"))
    assert f.round_qty(0.3) == Decimal("0.3")        # 0.1*3 float drift must not drop to 0.2
    assert f.round_qty(12.39) == Decimal("12.3")


def test_round_price_to_tick():
    f = parse_symbol_filters(_info(tick="0.01"))
    assert f.round_price(1.23456) == Decimal("1.23")
    assert f.round_price(1.235) == Decimal("1.24")


def test_qty_for_notional_skips_below_minimum_instead_of_upsizing():
    # BTC-like: min qty 0.001 at $86k -> smallest order ~$86, above min_notional 50.
    btc = parse_symbol_filters(_info("BTCUSDT", tick="0.1", step="0.001", min_qty="0.001", min_notional="50"))
    assert btc.min_order_usdt(86_000) == pytest.approx(86.0)
    assert btc.qty_for_notional(60.0, 86_000) is None      # passes min_notional, but rounds to 0 qty
    assert btc.qty_for_notional(200.0, 86_000) == Decimal("0.002")


def test_qty_for_notional_checks_min_notional_after_rounding():
    eth = parse_symbol_filters(_info("ETHUSDT", tick="0.01", step="0.001", min_qty="0.001", min_notional="20"))
    assert eth.qty_for_notional(19.0, 2750) is None
    assert eth.qty_for_notional(25.0, 2750) == Decimal("0.009")


def test_filters_for_raises_on_unknown_symbol():
    with pytest.raises(KeyError):
        filters_for({"symbols": [_info()]}, ["XRPUSDT", "DOESNOTEXIST"])
