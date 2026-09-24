"""
Operational kill-switch (Phase 3, docs/execution_plan.md decision 5).

Tracks peak equity; once equity falls `max_drawdown` below the peak it trips and
STAYS tripped (latch) until an explicit manual reset — a recovering equity curve
must not silently re-enable trading. Tripping only blocks NEW entries; open
positions keep their exchange-side stops. Independent from the strategy and from
risk/engine.py's RiskEngine (not part of the validated result).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class KillSwitch:
    max_drawdown: float
    peak_equity: float | None = None
    tripped: bool = False
    tripped_at_equity: float | None = None

    def update(self, equity: float) -> bool:
        """Feed the latest equity; returns True if new entries are blocked."""
        if self.peak_equity is None or equity > self.peak_equity:
            self.peak_equity = equity
        if not self.tripped and equity <= self.peak_equity * (1 - self.max_drawdown):
            self.tripped = True
            self.tripped_at_equity = equity
        return self.tripped

    def drawdown(self, equity: float) -> float:
        return 0.0 if not self.peak_equity else equity / self.peak_equity - 1

    def reset(self, equity: float) -> None:
        """Manual re-enable: the current equity becomes the new peak."""
        self.tripped = False
        self.tripped_at_equity = None
        self.peak_equity = equity

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "KillSwitch":
        return cls(**d)
