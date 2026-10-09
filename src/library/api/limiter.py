"""How many requests the app sends to an API (spec 2026-10-09-economia-de-requests, E4–E5).

A sliding minute with a global cap and a quota per user. The panel's request names its user in
CURRENT_USER: past either limit it is refused with the seconds to wait (RateLimited). A background
job runs without a user: it waits its turn, and says until when through `on_wait`. A 429 from the
API pauses everyone for its Retry-After. In process: the app runs as a single uvicorn process.
"""
import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Optional

WINDOW = 60.0
# The user the panel's request acts for (iss|sub); None in a background job.
CURRENT_USER: ContextVar[Optional[str]] = ContextVar("api_user", default=None)
# What a background job does while it waits its turn (it shows "esperando turno").
ON_WAIT: ContextVar[Optional[Callable[[float], None]]] = ContextVar("api_on_wait", default=None)


class RateLimited(Exception):
    """No room left for this user now; `seconds` until there is."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"rate limited for {seconds:.0f} s")
        self.seconds = seconds


class RateLimiter:
    def __init__(self, per_minute: int, per_user: int,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.per_minute = per_minute
        self.per_user = per_user
        self.clock = clock
        self.sleep = sleep
        self.sent: deque[float] = deque()
        self.by_user: dict[str, deque[float]] = {}
        self.paused_until = 0.0

    def pause(self, seconds: float) -> None:
        """Hold every request for `seconds` (a 429's Retry-After)."""
        self.paused_until = max(self.paused_until, self.clock() + seconds)

    async def acquire(self, user: Optional[str], on_wait: Optional[Callable[[float], None]] = None) -> None:
        """Take one request: at once if there is room; else RateLimited for a user, or wait for a job."""
        while True:
            now = self.clock()
            wait = self._wait(user, now)
            if wait <= 0:
                self.sent.append(now)
                if user is not None:
                    self.by_user.setdefault(user, deque()).append(now)
                return
            if user is not None:
                raise RateLimited(wait)
            if on_wait is not None:
                on_wait(wait)
            await self.sleep(wait)

    def _wait(self, user: Optional[str], now: float) -> float:
        waits = [self.paused_until - now, _window_wait(self.sent, self.per_minute, now)]
        if user is not None:
            waits.append(_window_wait(self.by_user.setdefault(user, deque()), self.per_user, now))
        return max(waits)


def _window_wait(sent: deque[float], limit: int, now: float) -> float:
    while sent and sent[0] <= now - WINDOW:
        sent.popleft()
    return sent[0] + WINDOW - now if len(sent) >= limit else 0.0
