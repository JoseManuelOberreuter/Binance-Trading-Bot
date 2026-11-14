"""
Market context for the dashboard's "Mercado" page: sentiment, futures positioning,
news and scheduled macro events. No charts or technical analysis. Read-only, public
sources only, no API keys, and it never touches the bot or the account.

  Sentimiento    Fear & Greed Index (alternative.me), daily, 0 = extreme fear, 100 = extreme greed
  Futures        Binance USD-M public data: funding rate, open interest (and its 24h
                 change), share of accounts net long
  Noticias       RSS from CoinDesk, Cointelegraph, The Block, Decrypt
  Calendario     FOMC decisions and US CPI releases (hand-maintained list below; verify
                 each year against federalreserve.gov and bls.gov)

Parsers are pure functions (tested); fetchers are thin `requests` wrappers that raise,
so the dashboard can show a per-section error without losing the rest.
"""

from __future__ import annotations

import email.utils
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests

TIMEOUT = 10
HEADERS = {"User-Agent": "Mozilla/5.0 (xrp_futures dashboard)"}
FNG_URL = "https://api.alternative.me/fng/"
FAPI = "https://fapi.binance.com"
NEWS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "The Block": "https://www.theblock.co/rss.xml",
    "Decrypt": "https://decrypt.co/feed",
}
FNG_LABELS = {"Extreme Fear": "Miedo extremo", "Fear": "Miedo", "Neutral": "Neutral",
              "Greed": "Codicia", "Extreme Greed": "Codicia extrema"}


# ------------------------------------------------------------ sentiment ----
def parse_fear_greed(payload: dict) -> pd.DataFrame:
    """alternative.me /fng/ payload -> date (UTC, ascending), value 0-100, label (Spanish)."""
    rows = [{"date": pd.Timestamp(int(d["timestamp"]), unit="s", tz="UTC"), "value": int(d["value"]),
             "label": FNG_LABELS.get(d["value_classification"], d["value_classification"])}
            for d in payload.get("data", [])]
    return pd.DataFrame(rows, columns=["date", "value", "label"]).sort_values("date").reset_index(drop=True)


def fetch_fear_greed(days: int = 30) -> pd.DataFrame:
    r = requests.get(FNG_URL, params={"limit": days}, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_fear_greed(r.json())


# -------------------------------------------------------------- futures ----
def parse_futures_row(symbol: str, premium: dict, oi_hist: list[dict], long_short: list[dict]) -> dict:
    """One symbol's positioning. `oi_hist` is hourly openInterestHist (oldest first),
    `long_short` is globalLongShortAccountRatio (latest last)."""
    oi_now = float(oi_hist[-1]["sumOpenInterestValue"]) if oi_hist else float("nan")
    oi_then = float(oi_hist[0]["sumOpenInterestValue"]) if len(oi_hist) > 1 else float("nan")
    return {
        "symbol": symbol,
        "funding": float(premium["lastFundingRate"]),          # per 8h settlement
        "oi_usd": oi_now,
        "oi_change": oi_now / oi_then - 1 if oi_then and oi_then == oi_then else float("nan"),
        "long_share": float(long_short[-1]["longAccount"]) if long_short else float("nan"),
    }


def fetch_futures(symbols, session: requests.Session | None = None) -> pd.DataFrame:
    s = session or requests.Session()
    premiums = {p["symbol"]: p for p in _get(s, "/fapi/v1/premiumIndex")}
    rows = []
    for sym in symbols:
        oi = _get(s, "/futures/data/openInterestHist", symbol=sym, period="1h", limit=25)   # 24h span
        ls = _get(s, "/futures/data/globalLongShortAccountRatio", symbol=sym, period="1h", limit=1)
        rows.append(parse_futures_row(sym, premiums[sym], oi, ls))
    return pd.DataFrame(rows)


def _get(s: requests.Session, path: str, **params):
    r = s.get(FAPI + path, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def futures_reading(df: pd.DataFrame) -> str:
    """One plain-language line about crowd positioning across the symbols."""
    if df.empty:
        return ""
    n = len(df)
    pos, neg = int((df["funding"] > 0).sum()), int((df["funding"] < 0).sum())
    longs = float(df["long_share"].mean())
    tone = ("Los LONG pagan a los SHORT, señal de más apuesta al alza" if pos > neg else
            "Los SHORT pagan a los LONG, señal de más apuesta a la baja" if neg > pos else
            "Posicionamiento dividido")
    text = f"Funding positivo en {pos} y negativo en {neg} de {n} monedas. {tone}. "
    text += f"En promedio, el {longs:.0%} de las cuentas está LONG"
    if neg > pos and longs > 0.6:
        text += ", pero el funding indica que el dinero grande está SHORT"
    return text + "."


# ----------------------------------------------------------------- news ----
def parse_rss(xml_text: str, source: str) -> list[dict]:
    """RSS 2.0 (or Atom) -> [{title, link, source, published (UTC Timestamp | NaT)}]."""
    root = ET.fromstring(xml_text)
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.iter("item")
    out = []
    for it in items:
        out.append(_item(it.findtext("title"), it.findtext("link"), it.findtext("pubDate"), source))
    if not out:   # Atom
        for e in root.iter(f"{atom}entry"):
            link = e.find(f"{atom}link")
            out.append(_item(e.findtext(f"{atom}title"), link.get("href") if link is not None else "",
                             e.findtext(f"{atom}updated") or e.findtext(f"{atom}published"), source))
    return [x for x in out if x["title"]]


def _item(title, link, published, source) -> dict:
    ts = pd.NaT
    if published:
        try:
            ts = pd.Timestamp(email.utils.parsedate_to_datetime(published.strip())).tz_convert("UTC")
        except (TypeError, ValueError):
            try:
                ts = pd.Timestamp(published.strip()).tz_convert("UTC")
            except (TypeError, ValueError):
                ts = pd.NaT
    return {"title": (title or "").strip(), "link": (link or "").strip(), "source": source, "published": ts}


def merge_news(items: list[dict], since: pd.Timestamp) -> pd.DataFrame:
    """Newest first, only items published at/after `since`, de-duplicated by title."""
    df = pd.DataFrame(items, columns=["title", "link", "source", "published"])
    df = df[df["published"].notna() & (df["published"] >= since)]
    df = df.assign(key=df["title"].str.lower().str.strip()).drop_duplicates("key").drop(columns="key")
    return df.sort_values("published", ascending=False).reset_index(drop=True)


def fetch_news(hours: int = 24) -> tuple[pd.DataFrame, list[str]]:
    """Merged news from every feed + one error string per feed that failed."""
    items, errors = [], []
    for source, url in NEWS_FEEDS.items():
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            items += parse_rss(r.text, source)
        except (requests.RequestException, ET.ParseError) as e:
            errors.append(f"{source}: {type(e).__name__}")
    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours)
    return merge_news(items, since), errors


# ------------------------------------------------------------- calendar ----
ET_ZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MarketEvent:
    at: datetime          # UTC
    kind: str             # "FOMC" | "CPI"
    title: str
    note: str = ""


def _et(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=ET_ZONE).astimezone(timezone.utc)


# FOMC: decision + statement 14:00 ET on the meeting's 2nd day (federalreserve.gov,
# 2027 schedule announced Nov 2026 as tentative). CPI: 08:30 ET (bls.gov).
_FOMC = [date(2026, 12, 9), date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9),
         date(2027, 7, 28), date(2027, 9, 15), date(2027, 11, 17)]
_FOMC_NO_DECISION = [date(2027, 10, 27)]
_CPI = [(date(2026, 12, 10), "noviembre"), (date(2027, 1, 13), "diciembre"), (date(2027, 2, 11), "enero")]

EVENTS: list[MarketEvent] = sorted(
    [MarketEvent(_et(d, 14, 0), "FOMC", "Decisión de tasas de la Fed (FOMC)",
                 "Tasa de interés + conferencia de prensa. Históricamente mueve mucho a BTC.") for d in _FOMC]
    + [MarketEvent(_et(d, 14, 0), "FOMC", "Reunión de la Fed sobre estrategia",
                   "Sin decisión de tasas anunciada.") for d in _FOMC_NO_DECISION]
    + [MarketEvent(_et(d, 8, 30), "CPI", f"Inflación de EE.UU. (IPC de {m})",
                   "Inflación más alta de lo esperado suele presionar a la baja.") for d, m in _CPI],
    key=lambda e: e.at)


def upcoming(events: list[MarketEvent], now: datetime, days: int = 60) -> list[MarketEvent]:
    horizon = now + pd.Timedelta(days=days)
    return [e for e in events if now <= e.at <= horizon]
