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


def test_update_by_hashes_sends_version_types_only_when_given(mongo: MongoRunner) -> None:
    """openapi v2.7.0 `POST /version_files/update`: `loaders` y `game_versions` obligatorios,
    `version_types` opcional (release, beta, alpha)."""
    _, seen = mongo(lambda: serve({("POST", "/version_files/update"): json_reply({"s512": VERSION})},
                                  lambda base: modrinth(base).files_update_hash_post_many(
                                      ["s512"], "sha512", ["fabric"], ["1.21.1"], version_types=["release", "beta"])))
    assert [s.json for s in seen] == [
        {"hashes": ["s512"], "algorithm": "sha512", "loaders": ["fabric"], "game_versions": ["1.21.1"],
         "version_types": ["release", "beta"]}]


def test_project_versions_ask_for_all_versions_without_changelogs(mongo: MongoRunner) -> None:
    """`featured` filtra a solo destacadas o solo no destacadas: sin él, vienen todas. La spec
    recomienda include_changelog=false."""
    result, seen = mongo(lambda: serve({("GET", "/project/iris/version"): json_reply([VERSION])},
                                       lambda base: modrinth(base).project_versions("iris", ["fabric"], ["26.3"])))
    assert [(s.method, s.path, s.query) for s in seen] == [
        ("GET", "/project/iris/version",
         {"loaders": '["fabric"]', "game_versions": '["26.3"]', "include_changelog": "false"})]
    assert [v.version_id for v in result] == ["v1"]


def test_project_versions_without_filters_sends_no_filter_params(mongo: MongoRunner) -> None:
    """`loaders` y `game_versions` son opcionales en la query: sin filtro se omiten, no se manda `[]`."""
    _, seen = mongo(lambda: serve({("GET", "/project/sodium/version"): json_reply([VERSION])},
                                  lambda base: modrinth(base).project_versions("sodium")))
    assert [(s.method, s.path, s.query) for s in seen] == [
        ("GET", "/project/sodium/version", {"include_changelog": "false"})]


def test_project_versions_with_filters_still_sends_them(mongo: MongoRunner) -> None:
    _, seen = mongo(lambda: serve({("GET", "/project/sodium/version"): json_reply([VERSION])},
                                  lambda base: modrinth(base).project_versions("sodium", ["fabric"], ["1.21.1"])))
    assert seen[0].query["loaders"] == '["fabric"]' and seen[0].query["game_versions"] == '["1.21.1"]'


def test_a_version_is_read_by_id_with_its_changelog(mongo: MongoRunner) -> None:
    """openapi v2.7.0: `GET /version/{id}` devuelve el Version completo; `changelog` es Markdown y
    puede ser null."""
    result, seen = mongo(lambda: serve(
        {("GET", "/version/v1"): json_reply({**VERSION, "changelog": "## Arreglos\n- un crash"})},
        lambda base: modrinth(base).version_get("v1")))
    assert [(s.method, s.path, s.query) for s in seen] == [("GET", "/version/v1", {})]
    assert (result.version_id, result.changelog) == ("v1", "## Arreglos\n- un crash")
    nothing, _ = mongo(lambda: serve({("GET", "/version/v1"): json_reply({**VERSION, "changelog": None})},
                                     lambda base: modrinth(base).version_get("v1")))
    assert nothing.changelog is None


def test_search_sends_the_query_and_facets_as_a_json_array() -> None:
    hits = [{"project_id": "gvQqBUqZ", "slug": "lithium", "title": "Lithium", "project_type": "mod"}]
    result, seen = run({("GET", "/search"): json_reply({"hits": hits, "offset": 0, "limit": 3, "total_hits": 1})},
                       lambda base: modrinth(base).search("Lithium", [["project_type:mod"]], limit=3))
    assert [(s.method, s.path, s.query) for s in seen] == [
        ("GET", "/search", {"query": "Lithium", "facets": '[["project_type:mod"]]', "limit": "3"})]
    assert result == hits


def test_game_versions_are_read_from_the_tag_in_the_order_modrinth_gives() -> None:
    tags = [{"version": "26.3", "version_type": "release", "date": "2026-09-15T11:23:02Z", "major": False},
            {"version": "1.21.11", "version_type": "release", "date": "2025-12-09T12:00:00Z", "major": False}]
    result, seen = run({("GET", "/tag/game_version"): json_reply(tags)},
                       lambda base: modrinth(base).game_versions())
    assert [(s.method, s.path) for s in seen] == [("GET", "/tag/game_version")]
    assert [t.version for t in result] == ["26.3", "1.21.11"]


# Metas de los loaders (investigación §2): rutas y forma de respuesta vistas en vivo el 2026-10-01.


def test_fabric_and_quilt_loader_versions_come_from_their_meta() -> None:
    from src.library.api.client.loaders import LoaderMetaAPI

    async def call(base: str) -> tuple[str | None, str | None, str | None]:
        api = LoaderMetaAPI(fabric_url=base + "fabric/", quilt_url=base + "quilt/")
        return (await api.loader_version("fabric", "26.3"), await api.loader_version("quilt", "26.3"),
                await api.loader_version("neoforge", "26.3"))

    result, seen = run({
        ("GET", "/fabric/v2/versions/loader/26.3"): json_reply([{"loader": {"version": "0.19.6", "stable": False}},
                                                              {"loader": {"version": "0.19.5", "stable": True}}]),
        ("GET", "/quilt/v3/versions/loader/26.3"): json_reply([{"loader": {"version": "0.20.0-beta.9"}},
                                                             {"loader": {"version": "0.24.0"}}]),
    }, call)
    assert result == ("0.19.5", "0.24.0", None)
    assert [(s.method, s.path) for s in seen] == [("GET", "/fabric/v2/versions/loader/26.3"),
                                                  ("GET", "/quilt/v3/versions/loader/26.3")]


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


BACKUP = {"object": "backup", "attributes": {
    "uuid": "b-1", "is_successful": True, "is_locked": True, "name": "pre-update job-1", "ignored_files": [],
    "checksum": "sha1:abc", "bytes": 1024, "created_at": "2026-10-01T12:00:00+00:00",
    "completed_at": "2026-10-01T12:01:00+00:00"}}


def test_the_server_state_reads_its_flags_and_backup_limit() -> None:
    details = {"object": "server", "attributes": {"status": None, "is_suspended": False, "is_installing": False,
                                                  "is_transferring": False, "feature_limits": {"databases": 0, "allocations": 1, "backups": 3}}}
    result, seen = run({("GET", "/api/client/servers/abc"): json_reply(details)},
                       lambda base: pterodactyl(base).server_state("abc"))
    assert [(s.method, s.path) for s in seen] == [("GET", "/api/client/servers/abc")]
    assert (result.status, result.is_suspended, result.backup_limit) == (None, False, 3)


def test_resources_read_the_power_state_and_uptime() -> None:
    stats = {"object": "stats", "attributes": {"current_state": "running", "is_suspended": False, "resources": {
        "memory_bytes": 1, "cpu_absolute": 1.0, "disk_bytes": 1, "network_rx_bytes": 1, "network_tx_bytes": 1, "uptime": 90000}}}
    result, seen = run({("GET", "/api/client/servers/abc/resources"): json_reply(stats)},
                       lambda base: pterodactyl(base).resources("abc"))
    assert (result.current_state, result.uptime_ms) == ("running", 90000)


def test_backups_are_listed_with_the_count_the_panel_holds_against_the_limit() -> None:
    listing = {"object": "list", "data": [BACKUP], "meta": {"backup_count": 1, "pagination": {}}}
    result, seen = run({("GET", "/api/client/servers/abc/backups"): json_reply(listing)},
                       lambda base: pterodactyl(base).backups("abc"))
    backups, count = result
    assert [b.uuid for b in backups] == ["b-1"] and count == 1


def test_creating_a_locked_backup_posts_its_name() -> None:
    result, seen = run({("POST", "/api/client/servers/abc/backups"): json_reply(BACKUP)},
                       lambda base: pterodactyl(base).backup_create("abc", "pre-update job-1", locked=True))
    assert [(s.method, s.path, s.json) for s in seen] == [
        ("POST", "/api/client/servers/abc/backups", {"name": "pre-update job-1", "is_locked": True})]
    assert result.uuid == "b-1"


def test_a_backup_is_read_and_its_lock_toggled() -> None:
    routes = {("GET", "/api/client/servers/abc/backups/b-1"): json_reply(BACKUP),
              ("POST", "/api/client/servers/abc/backups/b-1/lock"): json_reply(BACKUP)}

    async def call(base: str) -> Any:
        api = pterodactyl(base)
        return await api.backup_get("abc", "b-1"), await api.backup_lock_toggle("abc", "b-1")

    (read, toggled), seen = run(routes, call)
    assert [(s.method, s.path) for s in seen] == [("GET", "/api/client/servers/abc/backups/b-1"),
                                                  ("POST", "/api/client/servers/abc/backups/b-1/lock")]
    assert read.is_successful and toggled.uuid == "b-1"


def test_restoring_a_backup_posts_truncate_and_is_answered_204() -> None:
    result, seen = run({("POST", "/api/client/servers/abc/backups/b-1/restore"): empty_reply()},
                       lambda base: pterodactyl(base).backup_restore("abc", "b-1", truncate=True))
    assert [(s.method, s.json) for s in seen] == [("POST", {"truncate": True})] and result is True


def test_renaming_is_a_put_with_root_and_pairs() -> None:
    result, seen = run({("PUT", "/api/client/servers/abc/files/rename"): empty_reply()},
                       lambda base: pterodactyl(base).files_rename("abc", "/", [("mods/a.jar", "mods.old/j1/a.jar")]))
    assert [(s.method, s.path, s.json) for s in seen] == [
        ("PUT", "/api/client/servers/abc/files/rename", {"root": "/", "files": [{"from": "mods/a.jar", "to": "mods.old/j1/a.jar"}]})]
    assert result is True


def test_creating_a_folder_posts_root_and_name() -> None:
    result, seen = run({("POST", "/api/client/servers/abc/files/create-folder"): empty_reply()},
                       lambda base: pterodactyl(base).files_create_folder("abc", "/", "mods.staging"))
    assert [(s.method, s.json) for s in seen] == [("POST", {"root": "/", "name": "mods.staging"})] and result is True


def signed(path: str) -> Reply:
    async def reply(request: web.Request, base: str) -> web.StreamResponse:
        return web.json_response({"object": "signed_url", "attributes": {"url": f"{base}{path}?token=jwt"}})
    return reply


def test_uploading_asks_the_panel_for_a_signed_url_and_posts_the_file_to_wings_without_the_key() -> None:
    routes = {
        ("GET", "/api/client/servers/abc/files/upload"): signed("/upload/file"),
        ("POST", "/upload/file"): empty_reply(200),
    }
    # A name with "+", like most Modrinth jars (`+mc26.3`): it must arrive as is, not percent-encoded.
    result, seen = run(routes, lambda base: pterodactyl(base).server_files_upload("abc", "/mods", "a+mc26.3.jar", b"jar"))
    assert [(s.method, s.path) for s in seen] == [
        ("GET", "/api/client/servers/abc/files/upload"), ("POST", "/upload/file")]
    wings = seen[1]
    assert wings.query == {"token": "jwt", "directory": "/mods"}
    assert wings.form == {"files": ("a+mc26.3.jar", b"jar")}
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
