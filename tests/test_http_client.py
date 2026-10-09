"""El cliente HTTP común no arrastra estado de un request al siguiente.

Contra un servidor aiohttp local, nunca contra Modrinth ni Pterodactyl.
"""
import asyncio
from typing import Any, Awaitable, Callable

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


@pytest.mark.network
def test_status_error_is_an_upstream_error_naming_the_service() -> None:
    """A client with SERVICE answering 404 raises UpstreamError: the service, the upstream status
    and the same English detail as before (so /v1 does not change)."""
    from src.exceptions import UpstreamError

    class FakePterodactylAPI(HttpAPI):
        SERVICE = "Pterodactyl"

    async def missing(request: web.Request) -> web.Response:
        return web.Response(status=404, text="nope")

    async def run() -> UpstreamError:
        app = web.Application()
        app.router.add_get("/resource", missing)
        async with TestServer(app) as server:
            api = FakePterodactylAPI(base_url=str(server.make_url("/")))
            with pytest.raises(UpstreamError) as caught:
                await api._request(METHOD.GET, "resource")
            return caught.value

    error = asyncio.run(run())
    assert isinstance(error, UpstreamError) and error.service == "Pterodactyl" and error.upstream_status == 404
    assert error.status_code == 404
    assert error.detail.startswith("Request has failed with status error 404")


async def _download(body: bytes, cap: int, chunked: bool = False) -> bytes:
    from src.library.api.client.download import Downloader

    async def handler(request: web.Request) -> web.StreamResponse:
        if not chunked:
            return web.Response(body=body)
        response = web.StreamResponse()
        response.enable_chunked_encoding()
        await response.prepare(request)
        for start in range(0, len(body), 10):
            await response.write(body[start:start + 10])
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get("/pack.mrpack", handler)
    async with TestServer(app) as server:
        downloader = Downloader()
        try:
            return await downloader.download_capped(str(server.make_url("/pack.mrpack")), cap)
        finally:
            await downloader.close()


@pytest.mark.network
def test_a_capped_download_returns_what_fits() -> None:
    assert asyncio.run(_download(b"x" * 100, cap=100)) == b"x" * 100


@pytest.mark.network
@pytest.mark.parametrize("chunked", [False, True])
def test_a_capped_download_stops_past_the_cap_with_or_without_a_length(chunked: bool) -> None:
    from src.library.api.client.download import TooLarge

    with pytest.raises(TooLarge):
        asyncio.run(_download(b"x" * 101, cap=100, chunked=chunked))


async def _hits_for_a_404(call: Callable[[Any, str], Awaitable[Any]], path: str, client: Any) -> int:
    hits = 0

    async def handler(request: web.Request) -> web.Response:
        nonlocal hits
        hits += 1
        return web.json_response({"error": "not_found"}, status=404)

    app = web.Application()
    app.router.add_get(path, handler)
    async with TestServer(app) as server:
        client.base_url = str(server.make_url("/"))
        try:
            await call(client, "x")
        except Exception:
            pass
        await client.close()
    return hits


@pytest.mark.network
def test_a_read_that_answers_404_is_not_asked_again() -> None:
    # A4 (docs/superpowers/plans/2026-10-09-pendientes.md): a 404 does not change by asking twice,
    # and each try is a request the owner counts.
    from src.library.api.client.modrinth import ModrinthAPI
    from src.library.api.client.pterodactyl import PterodactylAPI

    modrinth = asyncio.run(_hits_for_a_404(lambda c, ref: c.project_get(ref), "/project/x", ModrinthAPI()))
    panel = asyncio.run(_hits_for_a_404(lambda c, d: c.server_files("srv", d), "/client/servers/srv/files/list",
                                        PterodactylAPI()))
    assert (modrinth, panel) == (1, 1)
