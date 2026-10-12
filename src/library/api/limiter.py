"""How many requests the app sends to an API (spec 2026-10-09-economia-de-requests, E4–E5).

A sliding minute with a global cap and a quota per user, which binds only once half the minute is
spent. The panel's request names its user in CURRENT_USER: past a limit it is refused with the
seconds to wait (RateLimited). A background
job runs without a user: it waits its turn, and says until when through `on_wait`. A 429 from the
API pauses everyone for its Retry-After, 2 s without one; under 30 requests left at the API
(X-Ratelimit-Remaining), everyone slows down by the refill time (M5). A pause of a few seconds makes
a panel user wait instead of refusing them. In process: the app runs as a single uvicorn process.
"""
import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from typing import Optional

WINDOW = 60.0
# Modrinth's bucket refills one request every 0.2 s (GCRA); below FLOOR left, the app slows down by
# that much per missing request (docs/research/2026-10-09-api-modrinth-detalle.md, P3).
FLOOR = 30
REFILL = 0.2
# A 429 without Retry-After (Modrinth sends none): its next token is ~0.2 s away.
BARE_429_PAUSE = 2.0
# A panel user waits this long at most; past it, they are told how long to wait.
SHORT_WAIT = 3.0
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

    def observe(self, remaining: int) -> None:
        """What the API says is left (X-Ratelimit-Remaining): below FLOOR, pause by the refill time."""
        if remaining < FLOOR:
            self.pause((FLOOR - max(remaining, 0)) * REFILL)

    def observe_headers(self, headers: Mapping[str, str]) -> None:
        """`observe` from a response's headers, when it carries X-Ratelimit-Remaining."""
        value = headers.get("X-Ratelimit-Remaining")
        if value is not None and value.strip().lstrip("-").isdigit():
            self.observe(int(value))

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
            if user is not None and wait > SHORT_WAIT:
                raise RateLimited(wait)
            if on_wait is not None:
                on_wait(wait)
            await self.sleep(wait)

    def _wait(self, user: Optional[str], now: float) -> float:
        waits = [self.paused_until - now, _window_wait(self.sent, self.per_minute, now)]
        if user is not None:
            mine = self.by_user.setdefault(user, deque())
            _window_wait(mine, self.per_user, now)  # drops what left the window
            # A user's quota binds only once half the app's minute is spent: with room to spare
            # nobody is held back, and under pressure no one person takes it all (owner, 2026-10-09).
            if len(self.sent) >= self.per_minute // 2:
                waits.append(_window_wait(mine, self.per_user, now))
        return max(waits)


def _window_wait(sent: deque[float], limit: int, now: float) -> float:
    while sent and sent[0] <= now - WINDOW:
        sent.popleft()
    return sent[0] + WINDOW - now if len(sent) >= limit else 0.0
