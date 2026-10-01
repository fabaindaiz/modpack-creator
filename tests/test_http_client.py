"""El cliente HTTP común no arrastra estado de un request al siguiente.

Contra un servidor aiohttp local, nunca contra Modrinth ni Pterodactyl.
"""
import asyncio

import pytest

from aiohttp import web
from aiohttp.test_utils import TestServer

from src.library.api import METHOD, HttpAPI
from src.library.api.session import TokenAuthorization


async def _authorization_headers_seen() -> list[str | None]:
    seen: list[str | None] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.headers.get("Authorization"))
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/resource", handler)
    async with TestServer(app) as server:
        base_url = str(server.make_url("/"))
        with_token = HttpAPI(base_url=base_url, session_auth=TokenAuthorization("pterodactyl-token"))
        without_token = HttpAPI(base_url=base_url)
        await with_token._request(METHOD.GET, "resource")
        await without_token._request(METHOD.GET, "resource")
    return seen


@pytest.mark.network
def test_token_of_one_client_never_reaches_another() -> None:
    assert asyncio.run(_authorization_headers_seen()) == ["Bearer pterodactyl-token", None]


async def _peers_seen(requests: int) -> tuple[list[object], bool]:
    """Lo que ve el servidor de requests seguidos de un mismo cliente, y si close() lo cierra."""
    peers: list[object] = []

    async def handler(request: web.Request) -> web.Response:
        assert request.transport is not None
        peers.append(request.transport.get_extra_info("peername"))
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/resource", handler)
    async with TestServer(app) as server:
        api = HttpAPI(base_url=str(server.make_url("/")))
        for _ in range(requests):
            await api._request(METHOD.GET, "resource")
        await api.close()
        closed = api._session is None
    return peers, closed


@pytest.mark.network
def test_one_client_keeps_one_connection_across_requests() -> None:
    """i-7985ee-57034c: aiohttp desaconseja una ClientSession por request; con una por cliente,
    los requests seguidos reusan la conexión (keep-alive) en vez de abrir una cada vez."""
    peers, closed = asyncio.run(_peers_seen(3))
    assert len(peers) == 3 and len(set(peers)) == 1
    assert closed
