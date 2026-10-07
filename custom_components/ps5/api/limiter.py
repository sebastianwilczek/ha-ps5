"""Client-side rate limiter shared by all requests of one config entry."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
import time
from typing import Any

from .errors import RateLimited

MAX_REQUESTS_PER_HOUR = 150
MIN_REQUEST_SPACING = 2.0
MIN_WRITE_SPACING = 10.0
WINDOW = 3600.0


def _monotonic() -> float:
    return time.monotonic()


class RateLimiter:
    """Rolling-hour cap, minimum spacing between requests and between writes.

    Callers queue in FIFO order (asyncio.Lock is fair). While paused after an
    HTTP 429, every acquire fails immediately with RateLimited.
    """

    def __init__(
        self,
        *,
        max_per_hour: int = MAX_REQUESTS_PER_HOUR,
        min_spacing: float = MIN_REQUEST_SPACING,
        write_spacing: float = MIN_WRITE_SPACING,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
    ) -> None:
        self._max_per_hour = max_per_hour
        self._min_spacing = min_spacing
        self._write_spacing = write_spacing
        self._clock = clock or _monotonic
        self._sleep = sleep or asyncio.sleep
        self._lock = asyncio.Lock()
        self._sent: deque[float] = deque()
        self._last_request: float | None = None
        self._last_write: float | None = None
        self._paused_until: float | None = None
        self.total_requests = 0

    def pause(self, seconds: float) -> None:
        """Pause all requests for `seconds`."""
        until = self._clock() + seconds
        if self._paused_until is None or until > self._paused_until:
            self._paused_until = until

    @property
    def pause_remaining(self) -> float:
        if self._paused_until is None:
            return 0.0
        return max(0.0, self._paused_until - self._clock())

    def _check_paused(self) -> None:
        if (remaining := self.pause_remaining) > 0:
            raise RateLimited(remaining)

    async def acquire(self, *, write: bool = False) -> None:
        """Wait until a request may be sent, then account for it."""
        self._check_paused()
        async with self._lock:
            while True:
                self._check_paused()
                now = self._clock()
                while self._sent and self._sent[0] <= now - WINDOW:
                    self._sent.popleft()
                wait = 0.0
                if self._last_request is not None:
                    wait = max(wait, self._last_request + self._min_spacing - now)
                if write and self._last_write is not None:
                    wait = max(wait, self._last_write + self._write_spacing - now)
                if len(self._sent) >= self._max_per_hour:
                    wait = max(wait, self._sent[0] + WINDOW - now)
                if wait <= 0:
                    break
                await self._sleep(wait)
            self._sent.append(now)
            self._last_request = now
            if write:
                self._last_write = now
            self.total_requests += 1

    def snapshot(self) -> dict[str, Any]:
        """Return the limiter state for diagnostics."""
        now = self._clock()
        return {
            "requests_last_hour": sum(1 for t in self._sent if t > now - WINDOW),
            "max_per_hour": self._max_per_hour,
            "total_requests": self.total_requests,
            "paused_for_s": round(self.pause_remaining, 1),
        }
