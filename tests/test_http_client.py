"""El cliente HTTP común no arrastra estado de un request al siguiente.

Contra un servidor aiohttp local, nunca contra Modrinth ni Pterodactyl.
"""
import asyncio

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


def test_token_of_one_client_never_reaches_another() -> None:
    assert asyncio.run(_authorization_headers_seen()) == ["Bearer pterodactyl-token", None]
