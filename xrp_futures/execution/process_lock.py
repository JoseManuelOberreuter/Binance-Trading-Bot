"""
One bot process per environment (fatal-error review, decision 38).

Two bots on the same account (two terminals, or the PC and the VM at once) would both
enter and both manage stops. `acquire()` takes an OS-level exclusive lock on
live_state/<env>/bot.lock; a second process fails to get it and must exit. The OS
releases the lock when the process dies, so a crash never leaves a stale lock behind.
"""

from __future__ import annotations

import os
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


class ProcessLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._f = None

    def acquire(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise AlreadyRunning(f"another bot process already holds {self.path}") from None
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
        self._f = f
        return self

    def release(self) -> None:
        if self._f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
        finally:
            self._f.close()
            self._f = None
