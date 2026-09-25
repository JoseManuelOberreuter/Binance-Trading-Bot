"""
Structured bot events (Phase 5b, docs/execution_plan.md decision 25).

The engine records what it DID as typed events with their numbers in
live_state/<env>/events.jsonl; the dashboard turns them into plain-language
messages (monitor/messages.py). bot.log stays the technical debug log.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

BOT_STARTED = "BOT_STARTED"
CANDLE_PROCESSED = "CANDLE_PROCESSED"
POSITION_OPENED = "POSITION_OPENED"
STOP_MOVED = "STOP_MOVED"
POSITION_CLOSED = "POSITION_CLOSED"
ENTRY_SKIPPED = "ENTRY_SKIPPED"
COMMAND_APPLIED = "COMMAND_APPLIED"
ALERT = "ALERT"
KILL_SWITCH = "KILL_SWITCH"
CYCLE_ERROR = "CYCLE_ERROR"
STATUS = "STATUS"                      # periodic "still running" summary (LiveConfig.status_every_min)


class EventLog:
    def __init__(self, state_dir: Path, enabled: bool = True):
        self.path = Path(state_dir) / "events.jsonl"
        self.enabled = enabled            # False in --dry-run: nothing is recorded

    def emit(self, kind: str, **data) -> dict:
        event = {"time": datetime.now(timezone.utc).isoformat(), "type": kind, **data}
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")
        return event

    def read(self, limit: int | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return rows[-limit:] if limit else rows
