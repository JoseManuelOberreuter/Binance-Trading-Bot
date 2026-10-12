from decimal import Decimal

import pytest
from binance.exceptions import BinanceAPIException

from xrp_futures.execution.order_manager import BinanceFuturesExchange, outcome_unknown


def api_error(code):
    return BinanceAPIException(type("R", (), {"text": ""})(), 400, f'{{"code": {code}, "msg": "x"}}')


class FakeClient:
    def __init__(self, order=None, error=None, symbol_config=None):
        self.order, self.error = order, error
        self.symbol_config = symbol_config or {"marginType": "ISOLATED", "leverage": "3"}
        self.timestamp_offset = 0
        self.changes = []

    def futures_get_order(self, symbol, origClientOrderId):
        if self.error:
            raise self.error
        return self.order

    def futures_account_trades(self, symbol, orderId):
        return [{"orderId": orderId, "qty": "50", "price": "1.5", "commission": "0.0375",
                 "realizedPnl": "0", "time": 1}]

    def futures_symbol_config(self, symbol):
        return [self.symbol_config]

    def futures_change_margin_type(self, **kw):
        self.changes.append(kw)
        raise api_error(-4067)          # e.g. refused because of open orders

    def futures_change_leverage(self, **kw):
        self.changes.append(kw)
        self.symbol_config["leverage"] = str(kw["leverage"])

    def futures_time(self):
        return {"serverTime": 10**13}


@pytest.mark.parametrize("exc,unknown", [(TimeoutError(), True), (ConnectionError(), True),
                                         (api_error(-1007), True), (api_error(-1006), True),
                                         (api_error(-2019), False), (api_error(-1111), False)])
def test_outcome_unknown_only_for_lost_responses(exc, unknown):
    assert outcome_unknown(exc) is unknown


def test_entry_status_filled_not_found_and_processing():
    ex = BinanceFuturesExchange(FakeClient(order={"status": "FILLED", "executedQty": "50", "orderId": 7}), {})
    fill = ex.entry_status("XRPUSDT", "xfb_e_1")
    assert fill.qty == Decimal("50") and fill.price == 1.5
    assert BinanceFuturesExchange(FakeClient(error=api_error(-2013)), {}).entry_status("XRPUSDT", "x") is None
    with pytest.raises(LookupError):
        BinanceFuturesExchange(FakeClient(order={"status": "NEW", "executedQty": "0", "orderId": 7}),
                               {}).entry_status("XRPUSDT", "x")
    with pytest.raises(BinanceAPIException):     # any other error: outcome still unknown
        BinanceFuturesExchange(FakeClient(error=api_error(-1007)), {}).entry_status("XRPUSDT", "x")


def test_ensure_margin_reports_cross_margin_it_could_not_fix():
    client = FakeClient(symbol_config={"marginType": "CROSSED", "leverage": "20"})
    problem = BinanceFuturesExchange(client, {}).ensure_margin("XRPUSDT", 3)
    assert problem == "CROSSED 3x (expected ISOLATED 3x)"       # leverage fixed, margin type refused
    assert BinanceFuturesExchange(FakeClient(), {}).ensure_margin("XRPUSDT", 3) is None


def test_sync_clock_sets_offset():
    client = FakeClient()
    BinanceFuturesExchange(client, {}).sync_clock()
    assert client.timestamp_offset != 0


class StopClient(FakeClient):
    def futures_create_algo_order(self, **kw):
        self.sent = kw


@pytest.mark.parametrize("working_type", ["MARK_PRICE", "CONTRACT_PRICE"])
def test_stop_uses_configured_trigger_price(working_type):
    client = StopClient()
    BinanceFuturesExchange(client, {}, working_type).place_stop("XRPUSDT", 1, Decimal("50"), Decimal("1.30"))
    assert client.sent["workingType"] == working_type and client.sent["reduceOnly"] == "true"


def test_default_stop_trigger_is_mark_price():
    from xrp_futures.config.live import DEFAULT, config_for
    assert DEFAULT.stop_working_type == config_for("production").stop_working_type == "MARK_PRICE"
    client = StopClient()
    BinanceFuturesExchange(client, {}).place_stop("XRPUSDT", -1, Decimal("50"), Decimal("1.70"))
    assert client.sent["workingType"] == "MARK_PRICE" and client.sent["side"] == "BUY"
    with pytest.raises(ValueError):
        BinanceFuturesExchange(client, {}, "LAST")
