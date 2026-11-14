from datetime import datetime, timezone

import pandas as pd

from xrp_futures.monitor import market as mk


def test_parse_fear_greed_sorts_and_translates():
    payload = {"data": [
        {"value": "13", "value_classification": "Extreme Fear", "timestamp": "1794614400"},
        {"value": "55", "value_classification": "Greed", "timestamp": "1794528000"},
    ]}
    df = mk.parse_fear_greed(payload)
    assert list(df["value"]) == [55, 13]                      # oldest first
    assert list(df["label"]) == ["Codicia", "Miedo extremo"]
    assert str(df["date"].dt.tz) == "UTC"


def test_parse_futures_row():
    premium = {"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}
    oi = [{"sumOpenInterestValue": "100"}, {"sumOpenInterestValue": "110"}]
    ls = [{"longAccount": "0.7612"}]
    row = mk.parse_futures_row("BTCUSDT", premium, oi, ls)
    assert row["funding"] == 0.0001
    assert row["oi_usd"] == 110
    assert abs(row["oi_change"] - 0.10) < 1e-12
    assert row["long_share"] == 0.7612


def test_parse_futures_row_tolerates_missing_history():
    row = mk.parse_futures_row("X", {"lastFundingRate": "0"}, [], [])
    assert pd.isna(row["oi_usd"]) and pd.isna(row["oi_change"]) and pd.isna(row["long_share"])


def test_futures_reading_majority_long():
    df = pd.DataFrame({"funding": [0.0001, 0.0002, -0.0001], "long_share": [0.7, 0.6, 0.5]})
    text = mk.futures_reading(df)
    assert "positivo en 2 y negativo en 1 de 3" in text and "al alza" in text and "60%" in text


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>
<item><title><![CDATA[BTC rises]]></title><link>https://a/1</link><pubDate>Sat, 14 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title>Old news</title><link>https://a/2</link><pubDate>Mon, 02 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title>No date</title><link>https://a/3</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>btc rises</title><link href="https://b/1"/><updated>2026-11-14T11:00:00Z</updated></entry>
<entry><title>ETF inflows</title><link href="https://b/2"/><updated>2026-11-14T12:00:00Z</updated></entry>
</feed>"""


def test_parse_rss_and_atom():
    rss = mk.parse_rss(RSS, "A")
    assert [x["title"] for x in rss] == ["BTC rises", "Old news", "No date"]
    assert rss[0]["published"] == pd.Timestamp("2026-11-14 10:00", tz="UTC")
    assert pd.isna(rss[2]["published"])
    atom = mk.parse_rss(ATOM, "B")
    assert atom[1]["link"] == "https://b/2" and atom[1]["published"] == pd.Timestamp("2026-11-14 12:00", tz="UTC")


def test_merge_news_filters_dedupes_and_sorts():
    items = mk.parse_rss(RSS, "A") + mk.parse_rss(ATOM, "B")
    df = mk.merge_news(items, pd.Timestamp("2026-11-13", tz="UTC"))
    assert list(df["title"]) == ["ETF inflows", "BTC rises"]   # old + undated dropped, duplicate title once


def test_calendar_times_are_utc_and_upcoming_filters():
    fomc = next(e for e in mk.EVENTS if e.kind == "FOMC")
    assert fomc.at == datetime(2026, 12, 9, 19, 0, tzinfo=timezone.utc)      # 14:00 ET in winter
    cpi = next(e for e in mk.EVENTS if e.kind == "CPI")
    assert cpi.at == datetime(2026, 12, 10, 13, 30, tzinfo=timezone.utc)     # 08:30 ET
    soon = mk.upcoming(mk.EVENTS, datetime(2026, 12, 9, 20, 0, tzinfo=timezone.utc), days=30)
    assert soon[0].kind == "CPI"                                             # past FOMC excluded
    assert all(e.at <= datetime(2027, 1, 8, 20, 0, tzinfo=timezone.utc) for e in soon)
    assert mk.EVENTS == sorted(mk.EVENTS, key=lambda e: e.at)
