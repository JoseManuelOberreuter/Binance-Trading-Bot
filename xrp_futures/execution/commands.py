"""
Command inbox between the dashboard and the bot (user-requested basic controls).

The dashboard never talks to Binance and holds no API keys: it only appends a
command to `live_state/<env>/commands.jsonl`. The bot picks pending commands up at
its next cycle, applies them with its own safety rules, and logs the outcome to
`commands_done.jsonl`. A dashboard bug therefore can't bypass stops or limits.

Handoff: the bot atomically renames the inbox before reading it, so a command the
dashboard appends meanwhile lands in a fresh inbox and is picked up next cycle.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

PAUSE_BOT = "PAUSE_BOT"
RESUME_BOT = "RESUME_BOT"
CLOSE_POSITION = "CLOSE_POSITION"
PAUSE_SYMBOL = "PAUSE_SYMBOL"
RESUME_SYMBOL = "RESUME_SYMBOL"
RESET_KILL_SWITCH = "RESET_KILL_SWITCH"

NEEDS_SYMBOL = {CLOSE_POSITION, PAUSE_SYMBOL, RESUME_SYMBOL}
ALL = {PAUSE_BOT, RESUME_BOT, CLOSE_POSITION, PAUSE_SYMBOL, RESUME_SYMBOL, RESET_KILL_SWITCH}


class CommandQueue:
    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir)
        self.inbox = self.dir / "commands.jsonl"
        self.processing = self.dir / "commands.processing.jsonl"
        self.done = self.dir / "commands_done.jsonl"

    def submit(self, kind: str, symbol: str | None = None, source: str = "dashboard") -> dict:
        if kind not in ALL:
            raise ValueError(f"unknown command {kind!r}")
        if kind in NEEDS_SYMBOL and not symbol:
            raise ValueError(f"{kind} needs a symbol")
        cmd = {"id": uuid.uuid4().hex[:12], "kind": kind, "symbol": symbol, "source": source,
               "submitted_at": datetime.now(timezone.utc).isoformat()}
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(cmd) + "\n")
        return cmd

    def pending(self) -> list[dict]:
        """Read without consuming (dry-run / dashboard display)."""
        return self._read(self.processing) + self._read(self.inbox)

    def take(self) -> list[dict]:
        """Consume pending commands. A leftover processing file (crash mid-apply) is retried first."""
        if self.inbox.exists() and not self.processing.exists():
            try:
                os.replace(self.inbox, self.processing)
            except PermissionError:   # Windows: dashboard is writing right now — next cycle
                pass
        cmds = self._read(self.processing)
        return cmds

    def finish(self, results: list[dict]) -> None:
        """Log outcomes and drop the processing file once every taken command is handled."""
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.done, "a", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, default=str) + "\n")
        if self.processing.exists():
            self.processing.unlink()

    def history(self, limit: int = 50) -> list[dict]:
        return self._read(self.done)[-limit:]

    @staticmethod
    def _read(path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
