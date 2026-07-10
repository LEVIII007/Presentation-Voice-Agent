"""Session cap. At 10-15 concurrent users the bottleneck is vendor quotas,
not CPU — the cap exists so overload degrades into a polite rejection instead
of degraded audio for everyone."""

from __future__ import annotations


class ConnectionManager:
    def __init__(self, max_sessions: int):
        self.max_sessions = max_sessions
        self._active = 0

    @property
    def active(self) -> int:
        return self._active

    def try_acquire(self) -> bool:
        if self._active >= self.max_sessions:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        self._active = max(0, self._active - 1)
