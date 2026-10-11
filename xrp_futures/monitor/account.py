"""
Read-only view of the REAL Binance account (production) for the dashboard's "Mi cuenta" tab:
Spot holdings, Futures wallet + open positions (including manual ones), Futures income history.

Strictly read-only: `ReadOnlyBinance` exposes only the GET endpoints below, so nothing here can
place, cancel or transfer anything even if the key allows it. Keys come from BINANCE_API_KEY /
BINANCE_SECRET in .env and are never logged or printed.

Fetching is separate from parsing: every `parse_*` function is pure (raw Binance JSON in,
DataFrame/dict out) and tested without network.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv

load_dotenv()

STABLES = {"USDT", "USDC", "FDUSD", "BUSD", "TUSD", "DAI", "USDP"}   # valued at 1 USD
QUOTES = ("USDT", "USDC", "FDUSD")                                 # tried in this order for pricing
DUST_USD = 1.0                                                     # spot rows below this go into "otros"
INCOME_TYPES = {"REALIZED_PNL": "PnL realizado", "COMMISSION": "Comisiones", "FUNDING_FEE": "Funding"}
INCOME_DAYS = 90                    # Binance keeps ~3 months of income history
INCOME_CHUNK_DAYS = 7               # max range per income request


class AccountError(Exception):
    """Human-readable (Spanish) reason the account could not be read."""


class ReadOnlyBinance:
    """Thin wrapper exposing only read endpoints of the production client."""

    def __init__(self, api_key: str, api_secret: str):
        self._c = Client(api_key, api_secret)
        try:   # align signed-request timestamps with Binance (avoids -1021 if the PC clock drifts)
            self._c.timestamp_offset = int(self._c.get_server_time()["serverTime"]) - int(time.time() * 1000)
        except Exception:
            pass

    def spot_balances(self) -> list[dict]:
        return self._c.get_account(omitZeroBalances="true")["balances"]

    def prices(self) -> list[dict]:
        return self._c.get_symbol_ticker()

    def futures_account(self) -> dict:
        return self._c.futures_account()

    def futures_positions(self) -> list[dict]:
        return self._c.futures_position_information()

    def futures_income(self, start_ms: int, end_ms: int) -> list[dict]:
        return self._c.futures_income_history(startTime=start_ms, endTime=end_ms, limit=1000)


def readonly_client() -> ReadOnlyBinance:
    key, secret = os.getenv("BINANCE_API_KEY", "").strip(), os.getenv("BINANCE_SECRET", "").strip()
    if not key or not secret:
        raise AccountError("Faltan BINANCE_API_KEY / BINANCE_SECRET en el archivo .env.")
    return ReadOnlyBinance(key, secret)


def explain(exc: BinanceAPIException) -> str:
    reasons = {
        -2008: "Binance no reconoce la API key (¿fue borrada o es de testnet?). Revisa BINANCE_API_KEY en .env.",
        -2014: "La API key tiene un formato inválido. Revisa BINANCE_API_KEY en .env.",
        -2015: "Key, IP o permisos inválidos. Revisa que la key tenga 'Enable Reading' / 'Enable Futures' "
               "y, si tiene restricción de IP, que incluya la IP de este PC.",
        -1022: "Firma inválida: BINANCE_SECRET no corresponde a la key.",
        -1021: "El reloj de este PC está desfasado respecto a Binance.",
    }
    return reasons.get(exc.code, f"Binance respondió error {exc.code}: {exc.message}")


# ------------------------------------------------------------------ parsing ----
def price_map(tickers: list[dict]) -> dict[str, float]:
    """Asset -> USD price, from spot tickers (stables = 1)."""
    raw = {t["symbol"]: float(t["price"]) for t in tickers}
    prices = {s: 1.0 for s in STABLES}
    for sym in raw:
        for q in QUOTES:
            if sym.endswith(q):
                base = sym[: -len(q)]
                if base and base not in prices and raw[sym] > 0:
                    prices[base] = raw[sym]
                break
    return prices


def parse_spot(balances: list[dict], prices: dict[str, float]) -> pd.DataFrame:
    """Spot holdings valued in USD, largest first. Earn (LD*) balances are left out."""
    rows = []
    for b in balances:
        asset, free, locked = b["asset"], float(b["free"]), float(b["locked"])
        qty = free + locked
        if qty <= 0 or asset.startswith("LD"):
            continue
        price = prices.get(asset)
        rows.append({"asset": asset, "qty": qty, "locked": locked, "price": price,
                     "value": qty * price if price is not None else None})
    df = pd.DataFrame(rows, columns=["asset", "qty", "locked", "price", "value"])
    if df.empty:
        return df.assign(share=pd.Series(dtype=float))
    total = df["value"].fillna(0).sum()
    df["share"] = df["value"] / total if total else None
    return df.sort_values("value", ascending=False, na_position="last").reset_index(drop=True)


def parse_futures_wallet(account: dict, prices: dict[str, float]) -> dict:
    """Futures wallet totals in USD, summed over every margin asset (USDT, USDC, ...)."""
    out = {"wallet": 0.0, "unrealized": 0.0, "margin_balance": 0.0, "available": 0.0, "assets": []}
    for a in account.get("assets", []):
        wallet = float(a.get("walletBalance", 0))
        unreal = float(a.get("unrealizedProfit", 0))
        if wallet == 0 and unreal == 0:
            continue
        px = prices.get(a["asset"], 0.0)
        out["wallet"] += wallet * px
        out["unrealized"] += unreal * px
        out["margin_balance"] += float(a.get("marginBalance", wallet + unreal)) * px
        out["available"] += float(a.get("availableBalance", 0)) * px
        out["assets"].append({"asset": a["asset"], "wallet": wallet, "unrealized": unreal})
    return out


def parse_positions(raw: list[dict]) -> pd.DataFrame:
    """Open futures positions (any origin: bot or manual)."""
    rows = []
    for p in raw:
        amt = float(p.get("positionAmt", 0))
        if amt == 0:
            continue
        entry, mark = float(p.get("entryPrice", 0)), float(p.get("markPrice", 0))
        notional = abs(float(p.get("notional", amt * mark)))
        margin = float(p.get("initialMargin") or p.get("isolatedMargin") or 0)
        liq = float(p.get("liquidationPrice", 0) or 0)
        pnl = float(p.get("unRealizedProfit", 0))
        rows.append({
            "symbol": p["symbol"], "side": 1 if amt > 0 else -1, "qty": abs(amt),
            "entry": entry, "mark": mark, "notional": notional, "pnl": pnl,
            "pnl_pct": pnl / (abs(amt) * entry) if entry else None,
            "margin": margin, "leverage": notional / margin if margin else None,
            "liq": liq or None, "liq_dist": abs(mark - liq) / mark if liq and mark else None,
            "margin_asset": p.get("marginAsset", ""),
        })
    cols = ["symbol", "side", "qty", "entry", "mark", "notional", "pnl", "pnl_pct", "margin",
            "leverage", "liq", "liq_dist", "margin_asset"]
    return pd.DataFrame(rows, columns=cols)


def parse_income(raw: list[dict], prices: dict[str, float]) -> pd.DataFrame:
    """Futures income rows (realized PnL, commissions, funding) in USD, oldest first, deduplicated."""
    rows = [{"time": pd.to_datetime(int(r["time"]), unit="ms", utc=True), "symbol": r.get("symbol") or "",
             "type": r["incomeType"], "amount": float(r["income"]) * prices.get(r.get("asset", "USDT"), 1.0),
             "id": f"{r.get('tranId')}|{r['incomeType']}|{r.get('symbol', '')}|{r['time']}"}
            for r in raw if r.get("incomeType") in INCOME_TYPES]
    df = pd.DataFrame(rows, columns=["time", "symbol", "type", "amount", "id"])
    return df.drop_duplicates("id").drop(columns="id").sort_values("time").reset_index(drop=True)


def income_by_symbol(income: pd.DataFrame) -> pd.DataFrame:
    """Per-coin breakdown: realized PnL, commissions, funding, net."""
    cols = list(INCOME_TYPES)
    if income.empty:
        return pd.DataFrame(columns=cols + ["net"])
    t = income.pivot_table(index="symbol", columns="type", values="amount", aggfunc="sum", fill_value=0.0)
    t = t.reindex(columns=cols, fill_value=0.0)
    t["net"] = t.sum(axis=1)
    return t.sort_values("net", ascending=False)


def income_by_day(income: pd.DataFrame, tz) -> pd.Series:
    """Net futures result per local calendar day."""
    if income.empty:
        return pd.Series(dtype=float)
    days = income["time"].dt.tz_convert(tz).dt.tz_localize(None).dt.normalize()
    return income.groupby(days)["amount"].sum()


# ----------------------------------------------------------------- snapshot ----
@dataclass
class AccountSnapshot:
    fetched_at: pd.Timestamp
    spot: pd.DataFrame
    futures: dict
    positions: pd.DataFrame
    errors: list[str] = field(default_factory=list)

    @property
    def spot_total(self) -> float:
        return float(self.spot["value"].fillna(0).sum()) if not self.spot.empty else 0.0

    @property
    def total(self) -> float:
        return self.spot_total + self.futures.get("margin_balance", 0.0)


def fetch_snapshot(client: ReadOnlyBinance) -> AccountSnapshot:
    """Balances and positions (cheap: 4 requests). Spot and Futures fail independently."""
    try:
        prices = price_map(client.prices())
    except BinanceAPIException as e:
        raise AccountError(explain(e)) from e
    errors, spot, fut, pos = [], pd.DataFrame(columns=["asset", "qty", "locked", "price", "value", "share"]), {}, parse_positions([])
    try:
        spot = parse_spot(client.spot_balances(), prices)
    except BinanceAPIException as e:
        errors.append(f"Spot: {explain(e)}")
    try:
        fut = parse_futures_wallet(client.futures_account(), prices)
        pos = parse_positions(client.futures_positions())
    except BinanceAPIException as e:
        errors.append(f"Futures: {explain(e)}")
    if len(errors) == 2:
        raise AccountError(errors[0].split(": ", 1)[1])
    return AccountSnapshot(pd.Timestamp.now(tz="UTC"), spot, fut, pos, errors)


def fetch_income(client: ReadOnlyBinance, days: int = INCOME_DAYS) -> pd.DataFrame:
    """Last `days` of futures income, fetched in 7-day windows (paging inside a window if full)."""
    try:
        prices = price_map(client.prices())
        end = pd.Timestamp.now(tz="UTC")
        start, raw = end - pd.Timedelta(days=days), []
        while start < end:
            stop = min(start + pd.Timedelta(days=INCOME_CHUNK_DAYS), end)
            s_ms, e_ms = int(start.timestamp() * 1000), int(stop.timestamp() * 1000)
            while True:
                page = client.futures_income(s_ms, e_ms)
                raw += page
                if len(page) < 1000:
                    break
                s_ms = int(page[-1]["time"]) + 1
            start = stop
    except BinanceAPIException as e:
        raise AccountError(explain(e)) from e
    return parse_income(raw, prices)
