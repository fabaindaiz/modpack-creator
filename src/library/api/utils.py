import asyncio
import math
import backoff
from functools import wraps
from typing import Optional
from aiohttp import ClientResponse, ConnectionTimeoutError, ClientResponseError, ServerConnectionError
from src.library.utils import WRAP
from src.library.api.handler.response import TooLarge
from src.library.api.limiter import RateLimited
from src.exceptions import *

UNKNOWN_SERVICE = "un servicio externo"

class FakeResponse(Exception): ...

# A read rate-limited with 429 waits what Retry-After asks, capped, and tries again: hammering a
# limited panel only extends the limit. Writes are never retried (d-7985ee-05d032).
RATE_LIMIT_TRIES = 4
RATE_LIMIT_MAX_WAIT = 60.0
_sleep = asyncio.sleep

def retry_after_seconds(value: Optional[str]) -> Optional[float]:
    """Retry-After in seconds; None if absent or an HTTP date (not sent by the panel)."""
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None

def backoff_on_rate_limit(func: WRAP) -> WRAP:
    """For reads only: on 429, wait Retry-After (or 2**attempt seconds), at most
    RATE_LIMIT_MAX_WAIT, and try again, RATE_LIMIT_TRIES times in all."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        for attempt in range(1, RATE_LIMIT_TRIES + 1):
            try:
                return await func(*args, **kwargs)
            except HTTP_429_TOO_MANY_REQUESTS as error:
                if attempt == RATE_LIMIT_TRIES:
                    raise
                wait = error.retry_after if error.retry_after is not None else float(2 ** attempt)
                await _sleep(min(wait, RATE_LIMIT_MAX_WAIT))
    return wrapper # type: ignore

def not_rate_limited(error: Exception) -> bool:
    """For backoff's giveup: a 429 is backoff_on_rate_limit's to handle, not an immediate retry."""
    return isinstance(error, HTTP_429_TOO_MANY_REQUESTS)

def handle_auth(func: WRAP) -> WRAP:
    @wraps(func)
    @backoff.on_exception(backoff.expo, Exception, max_tries=3)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except ClientResponseError as e:
            await asyncio.sleep(30)
            raise Exception("Authentication has failed") from e
    return wrapper # type: ignore

def handle_errors(func: WRAP) -> WRAP:
    # No retry here: _request also sends writes, and a write is never retried (d-7985ee-05d032).
    # A read that wants retries declares backoff on its client method.
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # args[0] is the client (these are methods of HttpAPI); its SERVICE names it for the panel.
        service = getattr(args[0], "SERVICE", UNKNOWN_SERVICE) if args else UNKNOWN_SERVICE
        try:
            return await func(*args, **kwargs)
        except ServerConnectionError as e:
            raise UpstreamError(service, 502, f"Request has failed with connection error: {e}", raw=str(e)) from e
        except ConnectionTimeoutError as e:
            raise UpstreamError(service, 504, f"Request has failed with timeout error: {e}", raw=str(e)) from e
        except RateLimited as e:
            # Refused here, before sending: the panel's user has no room left (E5).
            raise QuotaExceeded(
                f"Demasiadas consultas a {service}: intenta en {math.ceil(e.seconds)} s", e.seconds) from e
        except ClientResponseError as e:
            if e.status == 429:
                retry_after = retry_after_seconds(e.headers.get("Retry-After") if e.headers else None)
                limiter = getattr(args[0], "limiter", None) if args else None
                if limiter is not None:
                    limiter.pause(retry_after if retry_after is not None else 60.0)
                raise HTTP_429_TOO_MANY_REQUESTS(f"Rate limited: {e.message}", retry_after) from e
            raise UpstreamError(service, e.status, f"Request has failed with status error {e.status}: {e.message}",
                                raw=f"{e.request_info.method} {e.request_info.url}: {e.message}") from e
        except TooLarge:
            # The caller set the cap and says what it means (e.g. "pesa más de 50 MiB").
            raise
        except Exception as e:
            raise HTTP_500_INTERNAL_SERVER_ERROR(f"Request has failed with unhandled: {e}") from e
    return wrapper # type: ignore

# This function is not implemented yet, anyways validation already happens in aiohttp
async def validate_results(response: ClientResponse) -> None:
    pass