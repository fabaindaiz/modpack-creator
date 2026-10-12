"""El cliente de CurseForge (T5) contra un servidor de prueba: lo que manda y cómo lee la respuesta.
Fuentes: docs.curseforge.com/rest-api (`POST /v1/mods/files`: `{"fileIds": [...]}`, respuesta
`{"data": [File]}`, `hashes[{value, algo}]` con algo 1 = sha1), la clave en `x-api-key`; y
`GET /v1/shared-profile/{code}`, **no documentado** (C5): devuelve el zip del perfil
(docs/research/2026-10-09-importar-modpacks.md). Ninguno habla con CurseForge de verdad."""
import asyncio
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.library.api.client.curseforge import CurseForgeAPI
from src.library.api.client.download import TooLarge

pytestmark = pytest.mark.network


async def serve(handler: Any, call: Any) -> tuple[Any, list[web.Request], list[Any]]:
    seen: list[web.Request] = []
    bodies: list[Any] = []

    async def record(request: web.Request) -> web.StreamResponse:
        seen.append(request)
        bodies.append(await request.json() if request.can_read_body else None)
        answer: web.StreamResponse = await handler(request)
        return answer

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", record)
    async with TestServer(app) as server:
        api = CurseForgeAPI(api_key="clave-del-dueno", base_url=str(server.make_url("/")))
        try:
            return await call(api), seen, bodies
        finally:
            await api.close()


def test_files_are_asked_in_one_batch_with_the_key_and_read_as_sha1() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        return web.json_response({"data": [
            {"id": 4567890, "modId": 238222, "hashes": [{"value": "md5x", "algo": 2}, {"value": "ABC123", "algo": 1}]},
            {"id": 5000001, "modId": 306612, "hashes": [{"value": "", "algo": 1}]}]})

    result, seen, bodies = asyncio.run(serve(handler, lambda api: api.files_get_many([4567890, 5000001, 7])))
    assert [(r.method, r.path) for r in seen] == [("POST", "/v1/mods/files")]
    assert bodies == [{"fileIds": [4567890, 5000001, 7]}] and seen[0].headers["x-api-key"] == "clave-del-dueno"
    assert result == {4567890: "abc123"}  # an empty sha1 or a file it did not return: absent


def test_a_shared_profile_is_downloaded_capped() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        return web.Response(body=b"PK zip bytes", content_type="application/zip")

    data, seen, _ = asyncio.run(serve(handler, lambda api: api.shared_profile("ABCD1234", 1024)))
    assert data == b"PK zip bytes" and [(r.method, r.path) for r in seen] == [("GET", "/v1/shared-profile/ABCD1234")]
    with pytest.raises(TooLarge):
        asyncio.run(serve(handler, lambda api: api.shared_profile("ABCD1234", 4)))


def test_without_a_key_it_is_off() -> None:
    assert CurseForgeAPI(api_key=None).enabled is False and CurseForgeAPI(api_key="k").enabled is True
