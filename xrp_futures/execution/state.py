"""
Persistent bot state (Phase 3, docs/execution_plan.md).

`live_state/<environment>/` (gitignored) holds:
  state.json        — current state, rewritten atomically (temp file + os.replace), so
                      a crash/power cut mid-write never leaves a corrupt file
  trades.jsonl      — append-only closed-trade log
  equity_log.jsonl  — append-only equity snapshots (read by the dashboard)
Testnet and production live in separate folders and can never mix.

Binance is the source of truth for positions/orders (see reconcile.py); this file
remembers what only the bot knows: its stops' intent, realized PnL for the simulated
capital, the kill-switch, and which candle it last acted on.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

from xrp_futures.config.live import LiveConfig
from xrp_futures.execution.kill_switch import KillSwitch

STATE_ROOT = Path(__file__).resolve().parent.parent / "live_state"


@dataclass
class Position:
    symbol: str
    side: int                     # 1 long, -1 short
    qty: str                      # Decimal as string (exact)
    entry_price: float
    entry_time: str               # ISO UTC
    stop_price: str               # current (trailed) stop, Decimal as string
    stop_client_order_id: str | None = None   # clientAlgoId of the exchange-side stop
    entry_order_id: int | None = None
    entry_commission: float = 0.0             # USDT, from the entry fills (for exact trade PnL)

    @property
    def signed_qty(self) -> Decimal:
        return Decimal(self.qty) * self.side


@dataclass
class BotState:
    environment: str
    kill_switch: KillSwitch
    # Capital the bot may use (user-chosen 2026-10-11): sizing equity = simulated_capital +
    # realized + unrealized bot PnL, capped at the real wallet. None = the whole futures wallet.
    # SET_CAPITAL rebases it so the bot's equity equals the new amount at that moment.
    simulated_capital: float | None = None     # LiveConfig.equity_override_usdt at first start
    assigned_capital: float | None = None      # last amount the user assigned (display only)
    realized_pnl: float = 0.0                  # net of fees/funding, since first start
    positions: dict[str, Position] = field(default_factory=dict)
    paused: dict[str, str] = field(default_factory=dict)   # symbol -> reason (needs manual review)
    last_processed_bar: str | None = None      # ISO open_time of the last 12h candle acted on
    bot_paused: bool = False                   # manual pause: no new entries (stops/exits keep working)
    last_cycle_at: str | None = None           # ISO UTC heartbeat, read by the dashboard
    last_status_at: str | None = None          # last periodic STATUS event (console summary)
    last_equity_log_at: str | None = None      # last equity_log.jsonl snapshot (throttled)
    last_signals: dict[str, int] = field(default_factory=dict)   # per symbol, from the last processed candle
    # Entry orders sent whose outcome isn't confirmed yet (response lost): symbol -> client_id,
    # side, atr, equity, created. Resolved at the start of the next cycle (engine._resolve_pending).
    pending_entries: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def new(cls, environment: str, config: LiveConfig) -> "BotState":
        return cls(environment=environment, kill_switch=KillSwitch(config.kill_switch_drawdown),
                   simulated_capital=config.equity_override_usdt, assigned_capital=config.equity_override_usdt,
                   bot_paused=config.start_paused)

    def sizing_equity(self, wallet_equity: float, unrealized_pnl: float = 0.0) -> float:
        """
        Equity used for sizing and the kill-switch. With an assigned capital: that capital
        + what the bot has made/lost since (realized + open), so gains are reinvested — but
        never more than the real futures wallet. Otherwise the whole wallet equity.
        """
        if self.simulated_capital is not None:
            return min(self.simulated_capital + self.realized_pnl + unrealized_pnl, wallet_equity)
        return wallet_equity

    def set_capital(self, amount: float | None, wallet_equity: float, unrealized_pnl: float = 0.0) -> float:
        """Assign `amount` USDT to the bot from now on (None = whole wallet). Rebases so the
        bot's equity is `amount` right now, and moves the kill-switch peak to it so a lower
        allocation isn't mistaken for a loss (an already-tripped switch stays tripped)."""
        if amount is None:
            self.simulated_capital = None
        else:
            self.simulated_capital = amount - self.realized_pnl - unrealized_pnl
        self.assigned_capital = amount
        equity = self.sizing_equity(wallet_equity, unrealized_pnl)
        self.kill_switch.peak_equity = equity
        return equity

    def open_notional(self, prices: dict[str, float]) -> float:
        return sum(float(Decimal(p.qty)) * prices[s] for s, p in self.positions.items() if s in prices)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kill_switch"] = self.kill_switch.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "BotState":
        d = dict(d)
        d["kill_switch"] = KillSwitch.from_dict(d["kill_switch"])
        d["positions"] = {s: Position(**p) for s, p in d.get("positions", {}).items()}
        return cls(**d)


class StateStore:
    def __init__(self, environment: str, root: Path = STATE_ROOT):
        self.environment = environment
        self.dir = Path(root) / environment
        self.state_path = self.dir / "state.json"
        self.trades_path = self.dir / "trades.jsonl"
        self.equity_path = self.dir / "equity_log.jsonl"
        self.alerts_path = self.dir / "alerts.jsonl"

    def load(self, config: LiveConfig) -> BotState:
        if not self.state_path.exists():
            return BotState.new(self.environment, config)
        state = BotState.from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))
        if state.environment != self.environment:
            raise ValueError(f"state file is for {state.environment!r}, not {self.environment!r}")
        return state

    def save(self, state: BotState) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_path)

    def append_trade(self, record: dict) -> None:
        self._append(self.trades_path, record)

    def append_equity(self, record: dict) -> None:
        self._append(self.equity_path, record)

    def append_alert(self, record: dict) -> None:
        self._append(self.alerts_path, record)

    def _append(self, path: Path, record: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def read_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
