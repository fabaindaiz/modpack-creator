"""El límite de requests a Modrinth (spec 2026-10-09-economia-de-requests, E4–E5): un tope global
y un cupo por usuario por minuto. En el panel, sin cupo, se avisa con los segundos; un trabajo de
fondo espera su turno. Un 429 de Modrinth pausa a todos lo que diga su Retry-After."""
import asyncio
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.exceptions import HTTP_429_TOO_MANY_REQUESTS
from src.library.api import METHOD, HttpAPI
from src.library.api.limiter import CURRENT_USER, RateLimited, RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def limiter(per_minute: int = 3, per_user: int = 2) -> tuple[RateLimiter, Clock]:
    clock = Clock()
    return RateLimiter(per_minute=per_minute, per_user=per_user, clock=clock, sleep=clock.sleep), clock


def test_a_user_past_their_quota_is_told_how_long_to_wait_and_others_are_not() -> None:
    async def scenario() -> tuple[Any, ...]:
        limit, clock = limiter(per_minute=10, per_user=2)
        await limit.acquire("ana")
        clock.now += 15
        await limit.acquire("ana")
        with pytest.raises(RateLimited) as refused:
            await limit.acquire("ana")
        await limit.acquire("bea")  # another user's quota is their own
        return refused.value.seconds, clock.slept

    seconds, slept = asyncio.run(scenario())
    assert seconds == pytest.approx(45.0) and slept == []


def test_the_global_cap_refuses_a_user_and_makes_a_job_wait_its_turn() -> None:
    async def scenario() -> tuple[Any, ...]:
        limit, clock = limiter(per_minute=3, per_user=10)
        for user in ("ana", "bea", None):
            await limit.acquire(user)
        with pytest.raises(RateLimited) as refused:
            await limit.acquire("carla")
        await limit.acquire(None)  # a background job: waits until the oldest leaves the window
        return refused.value.seconds, clock.slept

    seconds, slept = asyncio.run(scenario())
    assert seconds == pytest.approx(60.0) and slept == [pytest.approx(60.0)]


def test_a_pause_from_modrinth_holds_everyone_for_its_retry_after() -> None:
    async def scenario() -> tuple[Any, ...]:
        limit, clock = limiter(per_minute=100, per_user=100)
        limit.pause(12)
        with pytest.raises(RateLimited) as refused:
            await limit.acquire("ana")
        await limit.acquire(None)
        return refused.value.seconds, clock.slept

    seconds, slept = asyncio.run(scenario())
    assert seconds == pytest.approx(12.0) and slept == [pytest.approx(12.0)]


def test_a_job_that_waits_says_until_when() -> None:
    async def scenario() -> list[float]:
        limit, clock = limiter(per_minute=1, per_user=10)
        seen: list[float] = []
        await limit.acquire(None)
        await limit.acquire(None, on_wait=seen.append)
        return seen

    assert asyncio.run(scenario()) == [pytest.approx(60.0)]


async def _client_against(status: int, retry_after: str, limit: RateLimiter, user: str | None) -> tuple[Any, int]:
    hits = 0

    async def handler(request: web.Request) -> web.Response:
        nonlocal hits
        hits += 1
        return web.json_response({}, status=status, headers={"Retry-After": retry_after} if status == 429 else {})

    app = web.Application()
    app.router.add_get("/resource", handler)
    async with TestServer(app) as server:
        api = HttpAPI(base_url=str(server.make_url("/")), limiter=limit)
        api.SERVICE = "Modrinth"  # type: ignore[attr-defined]
        token = CURRENT_USER.set(user)
        try:
            outcome: Any = None
            for _ in range(2):
                try:
                    await api._request(METHOD.GET, "resource")
                except HTTP_429_TOO_MANY_REQUESTS as error:
                    outcome = error
            return outcome, hits
        finally:
            CURRENT_USER.reset(token)
            await api.close()


@pytest.mark.network
def test_a_429_from_modrinth_pauses_the_limiter_and_the_next_panel_call_never_leaves() -> None:
    limit, _ = limiter(per_minute=100, per_user=100)
    error, hits = asyncio.run(_client_against(429, "30", limit, "ana"))
    assert hits == 1  # the second call was refused here, not sent
    assert "Demasiadas consultas a Modrinth: intenta en 30 s" in str(error.detail)


@pytest.mark.network
def test_a_client_without_a_limiter_is_not_limited() -> None:
    async def scenario() -> int:
        hits = 0

        async def handler(request: web.Request) -> web.Response:
            nonlocal hits
            hits += 1
            return web.json_response({})

        app = web.Application()
        app.router.add_get("/resource", handler)
        async with TestServer(app) as server:
            api = HttpAPI(base_url=str(server.make_url("/")))
            for _ in range(5):
                await api._request(METHOD.GET, "resource")
            await api.close()
        return hits

    assert asyncio.run(scenario()) == 5


@pytest.mark.network
def test_a_modrinth_read_does_not_retry_a_429_on_its_own() -> None:
    from src.library.api.client.modrinth import ModrinthAPI

    async def scenario() -> int:
        hits = 0

        async def handler(request: web.Request) -> web.Response:
            nonlocal hits
            hits += 1
            return web.json_response({}, status=429, headers={"Retry-After": "1"})

        app = web.Application()
        app.router.add_get("/project/x", handler)
        async with TestServer(app) as server:
            api = ModrinthAPI(limiter=limiter(per_minute=100, per_user=100)[0])
            api.base_url = str(server.make_url("/"))
            with pytest.raises(HTTP_429_TOO_MANY_REQUESTS):
                await api.project_get("x")
            await api.close()
        return hits

    assert asyncio.run(scenario()) == 1
