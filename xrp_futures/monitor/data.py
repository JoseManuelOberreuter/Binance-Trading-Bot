"""
Read-side of the dashboard (Phase 5): loads what the bot wrote under
live_state/<env>/ and derives the summaries the UI shows. Pure file reads — no
Binance client, no API keys. Kept separate from dashboard.py so it's testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from xrp_futures.config.live import LiveConfig
from xrp_futures.execution.commands import CommandQueue
from xrp_futures.execution.events import CANDLE_PROCESSED, EventLog
from xrp_futures.execution.state import STATE_ROOT, BotState, StateStore

HEARTBEAT_STALE_AFTER = pd.Timedelta(minutes=4)    # bot polls every minute; >3 missed cycles = stale


@dataclass
class BotView:
    environment: str
    state: BotState | None
    equity: pd.DataFrame
    trades: pd.DataFrame
    alerts: pd.DataFrame
    commands_done: list[dict]
    commands_pending: list[dict]
    log_tail: list[str]
    events: list[dict]              # newest last

    def last_event(self, kind: str) -> dict | None:
        return next((e for e in reversed(self.events) if e["type"] == kind), None)

    @property
    def last_candle(self) -> dict | None:
        return self.last_event(CANDLE_PROCESSED)


def environments(root: Path = STATE_ROOT) -> list[str]:
    return sorted(p.name for p in Path(root).iterdir() if p.is_dir()) if Path(root).exists() else []


def _frame(rows: list[dict], time_cols: tuple[str, ...]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in time_cols:
        if c in df:
            df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    return df


def tail(path: Path, n: int = 200) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]


def load(environment: str, config: LiveConfig, root: Path = STATE_ROOT) -> BotView:
    store = StateStore(environment, root=root)
    state = None
    if store.state_path.exists():
        state = BotState.from_dict(json.loads(store.state_path.read_text(encoding="utf-8")))
    queue = CommandQueue(store.dir)
    return BotView(
        environment=environment,
        state=state,
        equity=_frame(store.read_jsonl(store.equity_path), ("time",)),
        trades=_frame(store.read_jsonl(store.trades_path), ("entry_time", "exit_time")),
        alerts=_frame(store.read_jsonl(store.alerts_path), ("time",)),
        commands_done=queue.history(),
        commands_pending=queue.pending(),
        log_tail=tail(store.dir / "bot.log"),
        events=EventLog(store.dir).read(limit=500),
    )


def heartbeat_status(state: BotState | None, now: pd.Timestamp) -> tuple[bool, str]:
    """(is_alive, label) from the bot's last cycle timestamp."""
    if state is None or not state.last_cycle_at:
        return False, "nunca ha corrido"
    age = now - pd.Timestamp(state.last_cycle_at)
    minutes = int(age.total_seconds() // 60)
    return age <= HEARTBEAT_STALE_AFTER, f"último ciclo hace {minutes} min"


def trade_summary(trades: pd.DataFrame) -> dict:
    """Totals over closed trades whose PnL is known."""
    if trades.empty or "net_pnl" not in trades:
        return dict(n=0, wins=0, win_rate=0.0, net_pnl=0.0, best=0.0, worst=0.0)
    pnl = trades["net_pnl"].dropna().astype(float)
    if pnl.empty:
        return dict(n=0, wins=0, win_rate=0.0, net_pnl=0.0, best=0.0, worst=0.0)
    wins = int((pnl > 0).sum())
    return dict(n=len(pnl), wins=wins, win_rate=wins / len(pnl), net_pnl=float(pnl.sum()),
                best=float(pnl.max()), worst=float(pnl.min()))


def positions_frame(state: BotState | None) -> pd.DataFrame:
    if state is None or not state.positions:
        return pd.DataFrame()
    rows = []
    for p in state.positions.values():
        stop, entry = float(p.stop_price), p.entry_price
        rows.append(dict(Moneda=p.symbol, Lado="LONG" if p.side == 1 else "SHORT", Cantidad=p.qty,
                         Entrada=entry, Stop=stop, **{"Distancia stop": abs(entry - stop) / entry},
                         Desde=pd.Timestamp(p.entry_time).strftime("%Y-%m-%d %H:%M UTC")))
    return pd.DataFrame(rows)


def equity_every(equity: pd.DataFrame, rule: str) -> pd.DataFrame:
    """
    The very first snapshot (the curve's starting point) plus one point per `rule` bucket
    (e.g. "4h", "1D"): the LAST snapshot in each bucket, with its real timestamp — so the
    newest point is always the current equity and the curve is visible from the start,
    not only once a second bucket has closed.
    """
    if equity.empty:
        return equity
    df = equity.sort_values("time").reset_index(drop=True)
    last_per_bucket = df.groupby(df["time"].dt.floor(rule), sort=True).tail(1)
    return pd.concat([df.iloc[[0]], last_per_bucket]).drop_duplicates().reset_index(drop=True)


MIN_BUCKETS = {"1h": 6, "4h": 6, "1D": 7}   # at least 6 hours / 1 day of 4h buckets / 1 week of days
MAX_TICKS = 12


def time_axis(first: pd.Timestamp, last: pd.Timestamp, rule: str) -> tuple[pd.Timestamp, pd.Timestamp, list]:
    """
    (start, end, ticks) for the equity chart's x axis, aligned to the chosen interval:
    the window starts at the bucket boundary before `first`, spans at least MIN_BUCKETS,
    and ticks fall on bucket boundaries (thinned to <= MAX_TICKS) — never 5-minute ticks
    when the user asked for 4-hour or daily points.
    """
    step = pd.Timedelta(rule)
    start = first.floor(rule)
    end = max(last.ceil(rule), start + MIN_BUCKETS.get(rule, 1) * step)
    ticks = list(pd.date_range(start, end, freq=rule))
    stride = -(-len(ticks) // MAX_TICKS)   # ceil division
    return start, end, ticks[::stride]


def latest_marks(view: BotView) -> dict[str, dict]:
    """{symbol: {"price", "unrealized"}} from the newest equity snapshot that has them."""
    if view.equity.empty or "positions" not in view.equity:
        return {}
    marks = view.equity["positions"].dropna()
    return marks.iloc[-1] if len(marks) and isinstance(marks.iloc[-1], dict) else {}


def bot_pnl(state: BotState, unrealized: float) -> float:
    """What the bot itself has made or lost: closed trades (net) + its open positions."""
    return state.realized_pnl + unrealized


def starting_capital(state: BotState, equity_now: float, unrealized: float) -> float:
    """
    Baseline for "desde el inicio": current equity minus the bot's own PnL. Deposits,
    conversions, transfers and capital changes are therefore never counted as gains
    (in real-money mode the first snapshot can be anything, e.g. $0.24 before funding).
    """
    return equity_now - bot_pnl(state, unrealized)


def performance_curve(equity_log: pd.DataFrame, baseline: float) -> pd.DataFrame:
    """Equity curve without external flows: baseline + the bot's PnL at each snapshot."""
    out = equity_log[["time"]].copy()
    unreal = equity_log["unrealized_pnl"] if "unrealized_pnl" in equity_log else 0.0
    out["equity"] = baseline + equity_log["realized_pnl"].fillna(0.0) + pd.Series(unreal, index=out.index).fillna(0.0)
    return out


def per_coin(state: BotState, trades: pd.DataFrame, marks: dict[str, dict], universe,
             capital0: float | None = None) -> dict[str, dict]:
    """
    Cumulative result per coin since the bot started: realized (closed trades, net of fees
    and funding) + unrealized (open position, from the last bot cycle). `pct` is relative
    to the starting capital — how much each coin has added to or taken from the account.
    """
    capital0 = capital0 if capital0 is not None else (state.simulated_capital or 0.0)
    out = {}
    for symbol in universe:
        mine = trades[trades["symbol"] == symbol] if not trades.empty and "symbol" in trades else trades.iloc[:0]
        realized = float(mine["net_pnl"].dropna().sum()) if "net_pnl" in mine else 0.0
        mark = marks.get(symbol) if symbol in state.positions else None
        unrealized = float(mark["unrealized"]) if mark else 0.0
        total = realized + unrealized
        traded = len(mine) > 0 or symbol in state.positions
        out[symbol] = dict(realized=realized, unrealized=unrealized, total=total, traded=traded,
                           n_trades=len(mine), pct=(total / capital0) if capital0 and traded else None)
    return out


# ------------------------------------------------------------- capital chart ----
CHART_GAP = pd.Timedelta(minutes=30)       # snapshots come every 5 min; a longer gap = bot was off
CHART_RANGES = {"24 horas": pd.Timedelta(days=1), "7 días": pd.Timedelta(days=7),
                "30 días": pd.Timedelta(days=30), "Todo": None}
CHART_STEPS = {"1 hora": "1h", "4 horas": "4h", "1 día": "1D"}
DEFAULT_STEP = {"24 horas": "1 hora", "7 días": "4 horas", "30 días": "1 día", "Todo": "1 día"}


def chart_points(curve: pd.DataFrame, baseline: float, rule: str, window: pd.Timedelta | None,
                 now: pd.Timestamp) -> pd.DataFrame:
    """
    Points for the capital chart from a performance curve (time, equity; local tz-naive):
    drawdown from the running peak (computed on the full history, like the kill-switch),
    result vs `baseline` in %, a `segment` id that changes wherever the bot was off for more
    than CHART_GAP (the line breaks there instead of inventing a straight line), restricted
    to the last `window` and thinned to one point per `rule` bucket within each segment.
    """
    if curve.empty:
        return curve.assign(drawdown=[], pct=[], segment=[])
    df = curve.sort_values("time").reset_index(drop=True)
    df["drawdown"] = df["equity"] / df["equity"].cummax() - 1
    df["segment"] = (df["time"].diff() > CHART_GAP).cumsum()
    if window is not None:
        df = df[df["time"] >= now - window]
    if df.empty:
        return df.assign(pct=[])
    parts = [equity_every(g, rule) for _, g in df.groupby("segment", sort=True)]
    out = pd.concat(parts).drop_duplicates().reset_index(drop=True)
    out["pct"] = out["equity"] / baseline - 1 if baseline else 0.0
    out["change"] = out["equity"].diff().fillna(0.0)                 # gained/lost since the previous point
    out["change_pct"] = (out["equity"] / out["equity"].shift(1) - 1).fillna(0.0)
    return out


def trade_marks(trades: pd.DataFrame, open_positions: dict, curve: pd.DataFrame, tz) -> pd.DataFrame:
    """
    One mark per entry and per exit, placed on the curve at that moment (local tz-naive):
    kind "Entrada"/"Salida", coin, side and, for exits, the trade's net result.
    """
    rows = []
    if not trades.empty:
        for _, t in trades.iterrows():
            side = t.get("side", "")
            if pd.notna(t.get("entry_time")):
                rows.append(dict(time=t["entry_time"], kind="Entrada", symbol=t["symbol"], side=side, pnl=None))
            if pd.notna(t.get("exit_time")):
                pnl = t.get("net_pnl")
                rows.append(dict(time=t["exit_time"], kind="Salida", symbol=t["symbol"], side=side,
                                 pnl=float(pnl) if pnl is not None and pd.notna(pnl) else None))
    for symbol, pos in open_positions.items():
        rows.append(dict(time=pd.Timestamp(pos.entry_time), kind="Entrada", symbol=symbol,
                         side="LONG" if pos.side == 1 else "SHORT", pnl=None))
    if not rows or curve.empty:
        return pd.DataFrame(columns=["time", "kind", "symbol", "side", "pnl", "equity", "result"])
    marks = pd.DataFrame(rows)
    marks["time"] = pd.to_datetime(marks["time"], utc=True).dt.tz_convert(tz).dt.tz_localize(None)
    marks = pd.merge_asof(marks.sort_values("time"), curve[["time", "equity"]].sort_values("time"),
                          on="time", direction="nearest")
    marks["result"] = ["neutral" if p is None or pd.isna(p) else "good" if p > 0 else "bad" for p in marks["pnl"]]
    return marks.reset_index(drop=True)
