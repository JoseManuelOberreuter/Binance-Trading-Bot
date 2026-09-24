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

HEARTBEAT_STALE_AFTER = pd.Timedelta(minutes=12)   # bot polls every 5 min; >2 missed cycles = stale


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
