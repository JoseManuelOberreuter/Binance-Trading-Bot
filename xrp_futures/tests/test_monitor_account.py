import inspect
import re

import pandas as pd
import pytest
from binance.exceptions import BinanceAPIException

from xrp_futures.monitor import account as acc

TICKERS = [{"symbol": "XRPUSDT", "price": "2.50"}, {"symbol": "BTCUSDT", "price": "100000"},
           {"symbol": "XRPBTC", "price": "0.000025"}, {"symbol": "PEPEUSDC", "price": "0.00001"},
           {"symbol": "USDCUSDT", "price": "0.9998"}]


def test_price_map_prefers_usd_quotes_and_pins_stables():
    p = acc.price_map(TICKERS)
    assert p["XRP"] == 2.5 and p["BTC"] == 100_000 and p["PEPE"] == 0.00001
    assert p["USDT"] == p["USDC"] == 1.0          # stables stay at 1, not the USDCUSDT ticker


def test_parse_spot_values_sorts_and_skips_earn():
    bal = [{"asset": "XRP", "free": "100", "locked": "20"}, {"asset": "USDT", "free": "50", "locked": "0"},
           {"asset": "LDXRP", "free": "500", "locked": "0"}, {"asset": "ZZZ", "free": "3", "locked": "0"}]
    df = acc.parse_spot(bal, acc.price_map(TICKERS))
    assert list(df["asset"]) == ["XRP", "USDT", "ZZZ"]         # unpriced last, Earn excluded
    assert df.loc[0, "value"] == pytest.approx(300.0)
    assert df.loc[0, "share"] == pytest.approx(300 / 350)
    assert pd.isna(df.loc[2, "value"])


def test_futures_wallet_sums_margin_assets():
    account = {"assets": [
        {"asset": "USDT", "walletBalance": "100", "unrealizedProfit": "-5", "marginBalance": "95", "availableBalance": "80"},
        {"asset": "USDC", "walletBalance": "50", "unrealizedProfit": "2", "marginBalance": "52", "availableBalance": "40"},
        {"asset": "BNB", "walletBalance": "0", "unrealizedProfit": "0"}]}
    w = acc.parse_futures_wallet(account, acc.price_map(TICKERS))
    assert w["wallet"] == 150 and w["unrealized"] == -3 and w["margin_balance"] == 147 and w["available"] == 120
    assert [a["asset"] for a in w["assets"]] == ["USDT", "USDC"]


def test_parse_positions_short_with_leverage_and_liquidation():
    raw = [{"symbol": "XRPUSDC", "positionAmt": "-100", "entryPrice": "2.60", "markPrice": "2.50",
            "notional": "-250", "unRealizedProfit": "10", "initialMargin": "50", "liquidationPrice": "3.00",
            "marginAsset": "USDC"},
           {"symbol": "BTCUSDT", "positionAmt": "0", "entryPrice": "0", "markPrice": "1"}]
    df = acc.parse_positions(raw)
    assert len(df) == 1
    r = df.iloc[0]
    assert r["side"] == -1 and r["qty"] == 100 and r["leverage"] == pytest.approx(5.0)
    assert r["pnl_pct"] == pytest.approx(10 / 260)
    assert r["liq_dist"] == pytest.approx(0.2)


def test_income_dedup_breakdown_and_daily():
    raw = [{"symbol": "XRPUSDT", "incomeType": "REALIZED_PNL", "income": "12", "asset": "USDT", "time": 1_760_000_000_000, "tranId": 1},
           {"symbol": "XRPUSDT", "incomeType": "REALIZED_PNL", "income": "12", "asset": "USDT", "time": 1_760_000_000_000, "tranId": 1},
           {"symbol": "XRPUSDT", "incomeType": "COMMISSION", "income": "-0.5", "asset": "USDT", "time": 1_760_000_000_001, "tranId": 2},
           {"symbol": "BTCUSDT", "incomeType": "FUNDING_FEE", "income": "-1", "asset": "USDC", "time": 1_760_100_000_000, "tranId": 3},
           {"symbol": "", "incomeType": "TRANSFER", "income": "500", "asset": "USDT", "time": 1_760_000_000_000, "tranId": 4}]
    inc = acc.parse_income(raw, acc.price_map(TICKERS))
    assert len(inc) == 3                                        # duplicate page row and TRANSFER dropped
    t = acc.income_by_symbol(inc)
    assert t.loc["XRPUSDT", "net"] == pytest.approx(11.5) and t.loc["BTCUSDT", "FUNDING_FEE"] == -1
    days = acc.income_by_day(inc, "UTC")
    assert days.sum() == pytest.approx(10.5)


class FakeClient:
    def __init__(self, fail_spot=False):
        self.fail_spot = fail_spot

    def prices(self):
        return TICKERS

    def spot_balances(self):
        if self.fail_spot:
            raise BinanceAPIException(type("R", (), {"text": ""})(), 401, '{"code": -2015, "msg": "x"}')
        return [{"asset": "XRP", "free": "10", "locked": "0"}]

    def futures_account(self):
        return {"assets": [{"asset": "USDT", "walletBalance": "100", "unrealizedProfit": "0",
                            "marginBalance": "100", "availableBalance": "100"}]}

    def futures_positions(self):
        return []


def test_snapshot_total_and_partial_failure():
    snap = acc.fetch_snapshot(FakeClient())
    assert snap.total == pytest.approx(125.0) and not snap.errors
    snap = acc.fetch_snapshot(FakeClient(fail_spot=True))
    assert snap.total == pytest.approx(100.0) and snap.errors[0].startswith("Spot:")


def test_module_never_calls_write_endpoints():
    src = inspect.getsource(acc)
    assert not re.search(r"\.\w*(create|cancel|transfer|withdraw|order|change|leverage)\w*\(", src)
