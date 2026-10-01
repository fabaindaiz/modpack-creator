"""Un 429 del panel se espera, no se martilla (fase 2: el primer escaneo de un servidor grande).

La Client API de Pterodactyl permite 256 requests por minuto, y cada jar del escaneo cuesta dos.
Las lecturas del escaneo esperan lo que pida `Retry-After` (con tope) y reintentan; las
escrituras nunca se reintentan (d-7985ee-05d032).
"""
import asyncio
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.exceptions import HTTP_429_TOO_MANY_REQUESTS
from src.library.api import utils
from src.library.api.client import pterodactyl as module
from src.library.api.client.pterodactyl import PterodactylAPI

pytestmark = pytest.mark.network

LISTING = {"data": [{"attributes": {"name": "a.jar", "size": 1, "mode_bits": "644", "is_file": True,
                                    "is_symlink": False, "mimetype": "application/jar",
                                    "created_at": "2026-09-01T00:00:00+00:00",
                                    "modified_at": "2026-09-01T00:00:00+00:00"}}]}


def run(limited: int, call: Any, monkeypatch: pytest.MonkeyPatch, retry_after: str = "7") -> tuple[Any, list[float], int]:
    """El servidor contesta 429 a los primeros `limited` requests. Devuelve el resultado (o el
    error), las esperas pedidas y cuántos requests llegaron."""
    waits: list[float] = []

    async def no_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(utils, "_sleep", no_sleep)
    seen: list[str] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        seen.append(request.path)
        if len(seen) <= limited:
            return web.json_response({"errors": []}, status=429, headers={"Retry-After": retry_after})
        if request.path.endswith("/files/list"):
            return web.json_response(LISTING)
        return web.Response(status=204)

    async def main() -> Any:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        async with TestServer(app) as server:
            api = PterodactylAPI()
            api.base_url = str(server.make_url("/api/"))
            try:
                return await call(api)
            except HTTP_429_TOO_MANY_REQUESTS as error:
                return error

    result = asyncio.run(main())
    return result, waits, len(seen)


def test_a_read_waits_what_the_panel_asks_and_tries_again(monkeypatch: pytest.MonkeyPatch) -> None:
    result, waits, requests = run(2, lambda api: api.server_files("abc", "/mods"), monkeypatch)
    assert [f.name for f in result] == ["a.jar"]
    assert waits == [7.0, 7.0] and requests == 3


def test_the_wait_has_a_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    _, waits, _ = run(1, lambda api: api.server_files("abc", "/mods"), monkeypatch, retry_after="3600")
    assert waits == [utils.RATE_LIMIT_MAX_WAIT]


def test_a_read_that_stays_limited_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    result, waits, requests = run(99, lambda api: api.server_files("abc", "/mods"), monkeypatch)
    assert isinstance(result, HTTP_429_TOO_MANY_REQUESTS)
    assert requests == utils.RATE_LIMIT_TRIES and len(waits) == utils.RATE_LIMIT_TRIES - 1


def test_a_write_is_not_retried_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "PTERODACTYL_WRITES", True)
    result, waits, requests = run(1, lambda api: api.server_power("abc", "stop"), monkeypatch)
    assert isinstance(result, HTTP_429_TOO_MANY_REQUESTS)
    assert waits == [] and requests == 1
