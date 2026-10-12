import inspect

import numpy as np
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


# ---- holding exit rules (reference indicator) ----------------------------------------
def _series(values):
    return pd.Series(values, index=pd.date_range("2024-01-01", periods=len(values), freq="D", tz="UTC"), dtype=float)


def test_closed_daily_closes_drops_the_candle_in_progress():
    day = 86_400_000
    kl = [[0, "1", "1", "1", "10", "0", day - 1], [day, "1", "1", "1", "11", "0", 2 * day - 1]]
    now = pd.to_datetime(day + 5, unit="ms", utc=True)          # second candle still open
    assert list(acc.closed_daily_closes(kl, now)) == [10.0]


def test_trail_exits_after_25pct_drop_and_reenters_on_50_day_high():
    up = list(range(100, 160))                       # peak 159
    down = [159 * 0.74] * 5                          # -26% -> out
    t = acc.trail_state(_series(up + down))
    assert not t["inside"] and t["reentry_level"] == 159
    back = up + down + [160]                         # close above the prior 50-day high -> in again
    t = acc.trail_state(_series(back))
    assert t["inside"] and t["peak"] == 160 and t["exit_level"] == pytest.approx(120)


def test_trail_state_matches_the_backtested_rule():
    """Same state machine as the spot backtest: replay it independently and compare every day."""
    rng = np.random.default_rng(7)
    closes = _series(100 * np.cumprod(1 + rng.normal(0, 0.05, 700)))
    hi = closes.rolling(50).max().shift(1)
    inside, peak = True, closes.iloc[0]
    for i, (px, h) in enumerate(zip(closes, hi)):
        if inside:
            peak = max(peak, px)
            inside = px > peak * 0.75
        elif not np.isnan(h) and px > h:
            inside, peak = True, px
        assert acc.trail_state(closes.iloc[: i + 1])["inside"] == inside


def test_holding_signal_sma_and_distances():
    closes = _series([1.0] * 199 + [3.0])            # sma200 = 1.01
    sig = acc.holding_signal(closes, price=2.0)
    assert sig["sma_in"] and sig["vs_sma"] == pytest.approx(2.0 / 1.01 - 1)
    assert sig["trail_in"] and sig["exit_level"] == pytest.approx(2.25)
    assert sig["level_dist"] == pytest.approx(0.125)
    short = acc.holding_signal(_series([1.0] * 30), price=1.0)
    assert short["sma200"] is None and short["trail_in"] is None


def test_rule_assets_skip_stables_dust_and_unpriced():
    spot = pd.DataFrame({"asset": ["PEPE", "USDT", "SHIB", "ZZZ"], "value": [490.0, 50.0, 0.000002, None]})
    assert acc.rule_assets(spot) == ["PEPE"]
