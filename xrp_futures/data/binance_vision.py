"""
Download historical USDⓈ-M futures klines and funding rate from Binance's public
data dump (data.binance.vision) — no API key required, no rate limits.

URL schemes (verified against the live bucket):
  Monthly klines:  {BASE}/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{YYYY-MM}.zip
  Daily klines:    {BASE}/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{YYYY-MM-DD}.zip
  Monthly funding: {BASE}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{YYYY-MM}.zip
  Daily funding:   {BASE}/daily/fundingRate/{symbol}/{symbol}-fundingRate-{YYYY-MM-DD}.zip

Each zip contains a single CSV with a header row. A month/day that doesn't exist yet
(future) or predates the symbol's futures listing (e.g. XRPUSDT starts 2020-01) returns
HTTP 404 and is simply skipped.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import requests

BASE_URL = "https://data.binance.vision/data/futures/um"
REQUEST_TIMEOUT_SEC = 30

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]
FUNDING_COLUMNS = ["calc_time", "funding_interval_hours", "last_funding_rate"]


@dataclass
class FetchStats:
    """Bookkeeping returned alongside a downloaded DataFrame."""

    requested: int = 0
    found: int = 0
    missing: list[str] = None

    def __post_init__(self):
        if self.missing is None:
            self.missing = []


def _month_starts(start: date, end: date):
    """Yield the first-of-month date for each calendar month in [start, end]."""
    cur = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cur <= last:
        yield cur
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)


def _day_range(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def parse_archive_csv(raw: bytes, expected_columns: list[str]) -> pd.DataFrame:
    """
    Parse one archive's CSV bytes. Binance's older monthly archives (roughly
    pre-2021) ship WITHOUT a header row, while newer ones do — silently trusting
    pd.read_csv()'s auto-detected header on a headerless file turns the first data
    row into column names and corrupts/drops that file's data with no error. Detect
    explicitly by checking whether the first field of the first line is the expected
    header name; fall back to `expected_columns` as explicit names otherwise.
    Pulled out of _fetch_zip_csv as pure logic so it's unit-testable without network I/O.
    """
    first_field = raw.split(b",", 1)[0].decode().strip()
    has_header = first_field == expected_columns[0]
    if has_header:
        return pd.read_csv(io.BytesIO(raw))
    return pd.read_csv(io.BytesIO(raw), header=None, names=expected_columns)


def _fetch_zip_csv(url: str, expected_columns: list[str]) -> pd.DataFrame | None:
    """Download one archive and parse its single CSV member. None on HTTP 404."""
    resp = requests.get(url, timeout=REQUEST_TIMEOUT_SEC)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = zf.namelist()[0]
        raw = zf.read(name)
    return parse_archive_csv(raw, expected_columns)


def _klines_url(symbol: str, interval: str, day_or_month: date, monthly: bool) -> str:
    sym = symbol.upper()
    if monthly:
        stamp = day_or_month.strftime("%Y-%m")
        return f"{BASE_URL}/monthly/klines/{sym}/{interval}/{sym}-{interval}-{stamp}.zip"
    stamp = day_or_month.strftime("%Y-%m-%d")
    return f"{BASE_URL}/daily/klines/{sym}/{interval}/{sym}-{interval}-{stamp}.zip"


def _funding_url(symbol: str, day_or_month: date, monthly: bool) -> str:
    sym = symbol.upper()
    if monthly:
        stamp = day_or_month.strftime("%Y-%m")
        return f"{BASE_URL}/monthly/fundingRate/{sym}/{sym}-fundingRate-{stamp}.zip"
    stamp = day_or_month.strftime("%Y-%m-%d")
    return f"{BASE_URL}/daily/fundingRate/{sym}/{sym}-fundingRate-{stamp}.zip"


def download_klines(
    symbol: str,
    interval: str,
    start: date,
    end: date | None = None,
) -> tuple[pd.DataFrame, FetchStats]:
    """
    Download 1h (or other interval) klines for [start, end] (inclusive), preferring
    complete monthly archives and falling back to daily archives for the partial
    current month. Returns (DataFrame sorted by open_time, dedup'd, FetchStats).
    """
    end = end or date.today()
    stats = FetchStats()
    frames: list[pd.DataFrame] = []

    months = list(_month_starts(start, end))
    # Last month is only "complete" (safe to fetch as monthly) if end is past that month.
    current_month_start = date(end.year, end.month, 1)
    full_months = [m for m in months if m < current_month_start]

    for m in full_months:
        stats.requested += 1
        url = _klines_url(symbol, interval, m, monthly=True)
        df = _fetch_zip_csv(url, KLINE_COLUMNS)
        if df is None:
            stats.missing.append(m.strftime("%Y-%m (monthly)"))
            continue
        stats.found += 1
        frames.append(df)

    # Fill the tail (current partial month, plus any months data.binance.vision hasn't
    # yet published as monthly) with daily files.
    tail_start = max(current_month_start, start)
    for d in _day_range(tail_start, end):
        stats.requested += 1
        url = _klines_url(symbol, interval, d, monthly=False)
        df = _fetch_zip_csv(url, KLINE_COLUMNS)
        if df is None:
            stats.missing.append(d.isoformat())
            continue
        stats.found += 1
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=KLINE_COLUMNS), stats

    out = pd.concat(frames, ignore_index=True)
    missing_cols = set(KLINE_COLUMNS) - set(out.columns)
    if missing_cols:
        raise ValueError(f"Downloaded klines missing expected columns {missing_cols} — archive schema may have changed")
    out = out.drop_duplicates(subset="open_time").sort_values("open_time").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume", "quote_volume",
                "taker_buy_volume", "taker_buy_quote_volume"]:
        out[col] = out[col].astype(float)
    out["open_time"] = pd.to_datetime(out["open_time"], unit="ms", utc=True)
    out["close_time"] = pd.to_datetime(out["close_time"], unit="ms", utc=True)
    n_before = len(out)
    out = out.dropna(subset=["open_time"])
    if len(out) < n_before:
        print(f"[binance_vision] WARNING: dropped {n_before - len(out)} klines rows with unparseable open_time "
              f"(possible archive schema mismatch)")
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    out = out[(out["open_time"] >= start_ts) & (out["open_time"] < end_ts)].reset_index(drop=True)
    return out, stats


def download_funding_rate(
    symbol: str,
    start: date,
    end: date | None = None,
) -> tuple[pd.DataFrame, FetchStats]:
    """Download historical funding rate settlements for [start, end] (inclusive)."""
    end = end or date.today()
    stats = FetchStats()
    frames: list[pd.DataFrame] = []

    months = list(_month_starts(start, end))
    current_month_start = date(end.year, end.month, 1)
    full_months = [m for m in months if m < current_month_start]

    for m in full_months:
        stats.requested += 1
        url = _funding_url(symbol, m, monthly=True)
        df = _fetch_zip_csv(url, FUNDING_COLUMNS)
        if df is None:
            stats.missing.append(m.strftime("%Y-%m (monthly)"))
            continue
        stats.found += 1
        frames.append(df)

    tail_start = max(current_month_start, start)
    for d in _day_range(tail_start, end):
        stats.requested += 1
        url = _funding_url(symbol, d, monthly=False)
        df = _fetch_zip_csv(url, FUNDING_COLUMNS)
        if df is None:
            stats.missing.append(d.isoformat())
            continue
        stats.found += 1
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=FUNDING_COLUMNS), stats

    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset="calc_time").sort_values("calc_time").reset_index(drop=True)
    out["last_funding_rate"] = out["last_funding_rate"].astype(float)
    out["calc_time"] = pd.to_datetime(out["calc_time"], unit="ms", utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    out = out[(out["calc_time"] >= start_ts) & (out["calc_time"] < end_ts)].reset_index(drop=True)
    return out, stats
