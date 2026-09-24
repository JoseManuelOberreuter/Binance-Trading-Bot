from decimal import Decimal

from xrp_futures.config.live import LiveConfig
from xrp_futures.execution.reconcile import (
    ADOPT_STOP, CANCEL_DUPLICATE_STOP, CANCEL_ORPHAN_STOP, PAUSE_SYMBOL, PLACE_STOP, RECORD_CLOSED,
    ExchangeOrder, ExchangePosition, parse_orders, parse_positions, reconcile,
)
from xrp_futures.execution.state import BotState, Position

UNIVERSE = ["XRPUSDT"]


def _state(with_position=True, stop_id="xfb_s1"):
    state = BotState.new("testnet", LiveConfig())
    if with_position:
        state.positions["XRPUSDT"] = Position("XRPUSDT", 1, "50.0", 1.5, "2026-09-24T12:00:00+00:00", "1.38", stop_id)
    return state


def _pos(amt="50.0"):
    return {"XRPUSDT": ExchangePosition("XRPUSDT", Decimal(amt), 1.5)}


def _stop(cid="xfb_s1"):
    return ExchangeOrder("XRPUSDT", cid, "STOP_MARKET", 1.38)


def _kinds(actions):
    return [(a.kind, a.client_order_id) for a in actions]


def test_everything_matches_no_actions():
    assert reconcile(_state(), _pos(), [_stop()], UNIVERSE) == []


def test_position_without_stop_gets_one():
    assert _kinds(reconcile(_state(), _pos(), [], UNIVERSE)) == [(PLACE_STOP, None)]


def test_stop_filled_while_bot_was_down():
    actions = reconcile(_state(), {}, [], UNIVERSE)
    assert _kinds(actions) == [(RECORD_CLOSED, None)]


def test_closed_position_with_leftover_stop_cancels_it():
    assert _kinds(reconcile(_state(), {}, [_stop()], UNIVERSE)) == [(RECORD_CLOSED, None), (CANCEL_ORPHAN_STOP, "xfb_s1")]


def test_orphan_bot_stop_without_position():
    assert _kinds(reconcile(_state(with_position=False), {}, [_stop()], UNIVERSE)) == [(CANCEL_ORPHAN_STOP, "xfb_s1")]


def test_unknown_position_is_paused_not_touched():
    actions = reconcile(_state(with_position=False), _pos("-3.0"), [], UNIVERSE)
    assert _kinds(actions) == [(PAUSE_SYMBOL, None)]


def test_quantity_mismatch_pauses():
    assert _kinds(reconcile(_state(), _pos("20.0"), [_stop()], UNIVERSE)) == [(PAUSE_SYMBOL, None)]


def test_duplicate_stops_keep_the_known_one():
    actions = reconcile(_state(), _pos(), [_stop("xfb_other"), _stop("xfb_s1")], UNIVERSE)
    assert _kinds(actions) == [(CANCEL_DUPLICATE_STOP, "xfb_other")]


def test_single_stop_with_unknown_id_is_adopted():
    assert _kinds(reconcile(_state(stop_id=None), _pos(), [_stop("xfb_s9")], UNIVERSE)) == [(ADOPT_STOP, "xfb_s9")]


def test_non_bot_orders_are_never_touched():
    manual = ExchangeOrder("XRPUSDT", "web_manual_123", "STOP_MARKET", 1.2)
    # No bot position, a manual stop exists: nothing to do (not an orphan of ours).
    assert reconcile(_state(with_position=False), {}, [manual], UNIVERSE) == []
    # Bot position whose only stop is manual: the bot adds its own, ignores the manual one.
    assert _kinds(reconcile(_state(), _pos(), [manual], UNIVERSE)) == [(PLACE_STOP, None)]


def test_paused_symbol_is_left_alone():
    state = _state()
    state.paused["XRPUSDT"] = "quantity mismatch"
    assert reconcile(state, {}, [_stop()], UNIVERSE) == []


def test_parse_binance_payloads():
    positions = parse_positions([
        {"symbol": "XRPUSDT", "positionAmt": "-12.5", "entryPrice": "1.47"},
        {"symbol": "BNBUSDT", "positionAmt": "0.00", "entryPrice": "0"},
    ])
    assert list(positions) == ["XRPUSDT"] and positions["XRPUSDT"].amt == Decimal("-12.5")
    orders = parse_orders([{"symbol": "XRPUSDT", "clientOrderId": "xfb_a", "type": "STOP_MARKET", "stopPrice": "1.60"}])
    assert orders[0].is_bot_stop and orders[0].stop_price == 1.60
