"""Cada llamada a Modrinth y Pterodactyl sigue su especificación (i-7985ee-fb00ca).

Contra un servidor aiohttp local que registra lo que recibe y contesta con la forma que dice la
especificación, nunca contra las APIs reales. Las fuentes, en `docs/references.md`:
- Modrinth: `openapi.yaml` v2.7.0 (`HashList`, `GetLatestVersionFromHashBody`, `HashVersionMap`).
- Pterodactyl: `routes/api-client.php`, `FileController`, `FileUploadController` del panel, y el
  handler de subida de Wings (`router_server_files.go`).
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.library.api import METHOD
from src.library.api.handler import JsonResponse
from src.library.api.client.modrinth import ModrinthAPI
from src.library.api.client.pterodactyl import PterodactylAPI
from tests.conftest import MongoRunner

pytestmark = pytest.mark.network


@pytest.fixture(autouse=True)
def writes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los contratos de escritura necesitan la puerta abierta (tests/test_write_gate.py)."""
    from src.library.api.client import pterodactyl
    monkeypatch.setattr(pterodactyl, "PTERODACTYL_WRITES", True)

VERSION: dict[str, Any] = {
    "id": "v1", "project_id": "AANobbMI", "author_id": "a", "name": "v1", "version_number": "1",
    "version_type": "release", "status": "listed", "downloads": 1, "game_versions": ["1.21.1"],
    "loaders": ["fabric"], "featured": False, "date_published": "2020-01-01T00:00:00Z",
    "dependencies": [],
    "files": [{"hashes": {"sha512": "s512", "sha1": "s1"}, "url": "u", "filename": "a.jar",
               "primary": True, "size": 1}],
}


@dataclass
class Seen:
    method: str
    path: str
    query: dict[str, str]
    authorization: str | None
    json: Any = None
    form: dict[str, Any] = field(default_factory=dict)


Reply = Callable[[web.Request, str], Awaitable[web.StreamResponse]]


def json_reply(body: Any, status: int = 200) -> Reply:
    async def reply(request: web.Request, base: str) -> web.StreamResponse:
        return web.json_response(body, status=status)
    return reply


def empty_reply(status: int = 204) -> Reply:
    async def reply(request: web.Request, base: str) -> web.StreamResponse:
        return web.Response(status=status)
    return reply


async def serve(routes: dict[tuple[str, str], Reply],
                call: Callable[[str], Awaitable[Any]]) -> tuple[Any, list[Seen]]:
    """Levanta el servidor, llama a `call(base_url)` y devuelve su resultado y lo que se recibió.

    Una ruta que la especificación no define contesta 405, como lo haría la API real.
    """
    seen: list[Seen] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        entry = Seen(request.method, request.path, dict(request.query), request.headers.get("Authorization"))
        if request.content_type == "application/json" and request.can_read_body:
            entry.json = await request.json()
        elif request.content_type == "multipart/form-data":
            form = await request.post()
            entry.form = {k: (v.filename, v.file.read()) if isinstance(v, web.FileField) else v
                          for k, v in form.items()}
        seen.append(entry)
        reply = routes.get((request.method, request.path))
        if reply is None:
            return web.Response(status=405)
        return await reply(request, str(request.url.origin()))

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    async with TestServer(app) as server:
        return await call(str(server.make_url("/"))), seen


def run(routes: dict[tuple[str, str], Reply],
        call: Callable[[str], Awaitable[Any]]) -> tuple[Any, list[Seen]]:
    return asyncio.run(serve(routes, call))


def modrinth(base: str) -> ModrinthAPI:
    api = ModrinthAPI()
    api.base_url = base
    return api


def pterodactyl(base: str) -> PterodactylAPI:
    api = PterodactylAPI()
    api.base_url = base + "api/"
    return api


# Modrinth. Un ModrinthVersion es un Document: construirlo exige Beanie inicializado, y eso
# exige un servidor; por eso estos corren dentro del fixture `mongo`.


def test_versions_from_many_hashes_post_a_hash_list_and_read_a_hash_map(mongo: MongoRunner) -> None:
    result, seen = mongo(lambda: serve({("POST", "/version_files"): json_reply({"s512": VERSION})},
                       lambda base: modrinth(base).files_hash_post_many(["s512"], "sha512")))
    assert [(s.method, s.path, s.json) for s in seen] == [
        ("POST", "/version_files", {"hashes": ["s512"], "algorithm": "sha512"})]
    assert {h: v.version_id for h, v in result.items()} == {"s512": "v1"}


def test_latest_version_from_a_hash_posts_arrays(mongo: MongoRunner) -> None:
    result, seen = mongo(lambda: serve({("POST", "/version_file/s512/update"): json_reply(VERSION)},
                       lambda base: modrinth(base).files_update_hash_post("s512", "sha512", ["fabric"], ["1.21.1"])))
    assert [(s.method, s.path, s.query, s.json) for s in seen] == [
        ("POST", "/version_file/s512/update", {"algorithm": "sha512"},
         {"loaders": ["fabric"], "game_versions": ["1.21.1"]})]
    assert result.version_id == "v1"


def test_latest_versions_from_many_hashes_post_arrays_and_read_a_hash_map(mongo: MongoRunner) -> None:
    result, seen = mongo(lambda: serve({("POST", "/version_files/update"): json_reply({"s512": VERSION})},
                       lambda base: modrinth(base).files_update_hash_post_many(["s512"], "sha512", ["fabric"], ["1.21.1"])))
    assert [(s.method, s.path, s.json) for s in seen] == [
        ("POST", "/version_files/update",
         {"hashes": ["s512"], "algorithm": "sha512", "loaders": ["fabric"], "game_versions": ["1.21.1"]})]
    assert {h: v.version_id for h, v in result.items()} == {"s512": "v1"}


def test_project_versions_ask_for_all_versions_without_changelogs(mongo: MongoRunner) -> None:
    """`featured` filtra a solo destacadas o solo no destacadas: sin él, vienen todas. La spec
    recomienda include_changelog=false."""
    result, seen = mongo(lambda: serve({("GET", "/project/iris/version"): json_reply([VERSION])},
                                       lambda base: modrinth(base).project_versions("iris", ["fabric"], ["26.3"])))
    assert [(s.method, s.path, s.query) for s in seen] == [
        ("GET", "/project/iris/version",
         {"loaders": '["fabric"]', "game_versions": '["26.3"]', "include_changelog": "false"})]
    assert [v.version_id for v in result] == ["v1"]


def test_game_versions_are_read_from_the_tag_in_the_order_modrinth_gives() -> None:
    tags = [{"version": "26.3", "version_type": "release", "date": "2026-09-15T11:23:02Z", "major": False},
            {"version": "1.21.11", "version_type": "release", "date": "2025-12-09T12:00:00Z", "major": False}]
    result, seen = run({("GET", "/tag/game_version"): json_reply(tags)},
                       lambda base: modrinth(base).game_versions())
    assert [(s.method, s.path) for s in seen] == [("GET", "/tag/game_version")]
    assert [t.version for t in result] == ["26.3", "1.21.11"]


# Pterodactyl


def test_a_command_is_a_post_with_a_json_body_answered_204() -> None:
    result, seen = run({("POST", "/api/client/servers/abc/command"): empty_reply()},
                       lambda base: pterodactyl(base).server_command("abc", "say hi"))
    assert [(s.method, s.path, s.query, s.json) for s in seen] == [
        ("POST", "/api/client/servers/abc/command", {}, {"command": "say hi"})]
    assert result is True


def test_power_is_a_post_with_a_json_body_answered_204() -> None:
    result, seen = run({("POST", "/api/client/servers/abc/power"): empty_reply()},
                       lambda base: pterodactyl(base).server_power("abc", "restart"))
    assert [(s.method, s.path, s.json) for s in seen] == [
        ("POST", "/api/client/servers/abc/power", {"signal": "restart"})]
    assert result is True


def test_deleting_files_is_a_post_with_root_and_files_in_the_body() -> None:
    result, seen = run({("POST", "/api/client/servers/abc/files/delete"): empty_reply()},
                       lambda base: pterodactyl(base).server_files_delete("abc", "/mods", ["a.jar", "b.jar"]))
    assert [(s.method, s.path, s.query, s.json) for s in seen] == [
        ("POST", "/api/client/servers/abc/files/delete", {}, {"root": "/mods", "files": ["a.jar", "b.jar"]})]
    assert result is True


def signed(path: str) -> Reply:
    async def reply(request: web.Request, base: str) -> web.StreamResponse:
        return web.json_response({"object": "signed_url", "attributes": {"url": f"{base}{path}?token=jwt"}})
    return reply


def test_uploading_asks_the_panel_for_a_signed_url_and_posts_the_file_to_wings_without_the_key() -> None:
    routes = {
        ("GET", "/api/client/servers/abc/files/upload"): signed("/upload/file"),
        ("POST", "/upload/file"): empty_reply(200),
    }
    result, seen = run(routes, lambda base: pterodactyl(base).server_files_upload("abc", "/mods", "a.jar", b"jar"))
    assert [(s.method, s.path) for s in seen] == [
        ("GET", "/api/client/servers/abc/files/upload"), ("POST", "/upload/file")]
    wings = seen[1]
    assert wings.query == {"token": "jwt", "directory": "/mods"}
    assert wings.form == {"files": ("a.jar", b"jar")}
    assert wings.authorization is None
    assert result is True


def test_downloading_asks_the_panel_for_a_signed_url_and_reads_the_file_from_wings_without_the_key() -> None:
    async def file(request: web.Request, base: str) -> web.StreamResponse:
        return web.Response(body=b"jar bytes", content_type="application/octet-stream")

    routes = {
        ("GET", "/api/client/servers/abc/files/download"): signed("/download/file"),
        ("GET", "/download/file"): file,
    }
    result, seen = run(routes, lambda base: pterodactyl(base).server_files_download("abc", "/mods/a.jar"))
    assert [(s.method, s.path, s.query) for s in seen] == [
        ("GET", "/api/client/servers/abc/files/download", {"file": "/mods/a.jar"}),
        ("GET", "/download/file", {"token": "jwt"})]
    assert seen[1].authorization is None
    assert result == b"jar bytes"


# El cliente común


def test_concurrent_requests_on_one_client_each_get_their_own_response() -> None:
    """i-7985ee-236b45: un handler de respuesta compartido devuelve el cuerpo de otro request."""
    async def slow_project(request: web.Request, base: str) -> web.StreamResponse:
        slug = request.path.rsplit("/", 1)[-1]
        await asyncio.sleep(0.05 if slug == "first" else 0)
        return web.json_response({"slug": slug})

    async def call(base: str) -> list[Any]:
        api = modrinth(base)
        first: JsonResponse
        second: JsonResponse
        first, second = await asyncio.gather(
            api._request(method=METHOD.GET, path="project/first"), api._request(method=METHOD.GET, path="project/second"))
        return [first.json["slug"], second.json["slug"]]

    result, _ = run({("GET", "/project/first"): slow_project, ("GET", "/project/second"): slow_project}, call)
    assert result == ["first", "second"]
