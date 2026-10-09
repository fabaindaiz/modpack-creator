"""La actualización completa con el cliente HTTP real, contra un panel servido en localhost.

`tests/test_update.py` prueba el procedimiento contra un panel en memoria. Este lo corre de punta a
punta con `PterodactylAPI`: rutas, métodos y cuerpos del Client API (`routes/api-client.php`),
URLs firmadas de Wings para subir y bajar, la clave del panel que nunca llega a Wings, y la puerta
de escritura (PTERODACTYL_WRITES).
"""
from src.exceptions import UpstreamError
import json
from typing import Any, Awaitable, Callable

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.database.jobs import JobDatabase
from src.database.modpack import ModpackDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.library.api.client import pterodactyl as client_module
from src.library.api.client.pterodactyl import PterodactylAPI
from src.model.jobs import Job
from src.services.jobs import JobWorker
from src.services.pterodactyl import PterodactylService
from src.services.update import UpdateService, UpdateTiming
from tests._actors import OPERATOR, RecordingAudit
from tests._panel import StatefulPanel, sha512
from tests.conftest import MongoRunner
from tests.test_update import NOW, FakeTime, setup

pytestmark = pytest.mark.network
TOKEN = "Bearer test-panel-key"


def panel_app(panel: StatefulPanel, problems: list[str]) -> web.Application:
    """Las rutas del panel y de Wings sobre el estado de StatefulPanel."""
    def attrs(model: Any) -> dict[str, Any]:
        return {"attributes": json.loads(model.model_dump_json())}

    def panel_route(handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> Any:
        async def checked(request: web.Request) -> web.StreamResponse:
            if request.headers.get("Authorization") != TOKEN:
                problems.append(f"{request.path} without the panel key")
            return await handler(request)
        return checked

    def wings_route(handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> Any:
        async def checked(request: web.Request) -> web.StreamResponse:
            if "Authorization" in request.headers:
                problems.append(f"{request.path} received the panel key")
            return await handler(request)
        return checked

    async def details(r: web.Request) -> web.StreamResponse:
        s = await panel.server_state("x")
        return web.json_response({"attributes": {"status": s.status, "is_suspended": s.is_suspended,
                                                 "is_installing": s.is_installing, "is_transferring": s.is_transferring,
                                                 "feature_limits": {"backups": s.backup_limit}}})

    async def resources(r: web.Request) -> web.StreamResponse:
        s = await panel.resources("x")
        return web.json_response({"attributes": {"current_state": s.current_state, "is_suspended": s.is_suspended,
                                                 "resources": {"uptime": s.uptime_ms}}})

    async def listing(r: web.Request) -> web.StreamResponse:
        try:
            files = await panel.server_files("x", r.query["directory"])
        except UpstreamError as missing:  # a directory that does not exist: Wings' 404, passed on (A1)
            return web.json_response({"errors": [{"code": "NotFound"}]}, status=missing.upstream_status)
        return web.json_response({"data": [{"attributes": json.loads(f.model_dump_json())} for f in files]})

    async def signed(r: web.Request) -> web.StreamResponse:
        kind = r.path.rsplit("/", 1)[-1]
        token = r.query.get("file", "upload")
        return web.json_response({"object": "signed_url",
                                  "attributes": {"url": f"{r.url.origin()}/{kind}/file?token={token}"}})

    async def wings_download(r: web.Request) -> web.StreamResponse:
        return web.Response(body=panel.files[r.query["token"]])

    async def wings_upload(r: web.Request) -> web.StreamResponse:
        form = await r.post()
        part = form["files"]
        assert isinstance(part, web.FileField)
        await panel.server_files_upload("x", r.query["directory"], part.filename, part.file.read())
        return web.Response(status=200)

    async def folder(r: web.Request) -> web.StreamResponse:
        body = await r.json()
        await panel.files_create_folder("x", body["root"], body["name"])
        return web.Response(status=204)

    async def rename(r: web.Request) -> web.StreamResponse:
        body = await r.json()
        await panel.files_rename("x", body["root"], [(f["from"], f["to"]) for f in body["files"]])
        return web.Response(status=204)

    async def command(r: web.Request) -> web.StreamResponse:
        await panel.server_command("x", (await r.json())["command"])
        return web.Response(status=204)

    async def power(r: web.Request) -> web.StreamResponse:
        await panel.server_power("x", (await r.json())["signal"])
        return web.Response(status=204)

    async def backups(r: web.Request) -> web.StreamResponse:
        items, count = await panel.backups("x")
        return web.json_response({"data": [attrs(b) for b in items], "meta": {"backup_count": count}})

    async def backup_create(r: web.Request) -> web.StreamResponse:
        body = await r.json()
        return web.json_response(attrs(await panel.backup_create("x", body["name"], body["is_locked"])))

    async def backup_get(r: web.Request) -> web.StreamResponse:
        return web.json_response(attrs(await panel.backup_get("x", r.match_info["b"])))

    async def backup_lock(r: web.Request) -> web.StreamResponse:
        return web.json_response(attrs(await panel.backup_lock_toggle("x", r.match_info["b"])))

    base = "/api/client/servers/{s}"
    app = web.Application()
    for method, path, handler in [
        ("GET", "", details), ("GET", "/resources", resources), ("GET", "/files/list", listing),
        ("GET", "/files/download", signed), ("GET", "/files/upload", signed),
        ("POST", "/files/create-folder", folder), ("PUT", "/files/rename", rename),
        ("POST", "/command", command), ("POST", "/power", power), ("GET", "/backups", backups),
        ("POST", "/backups", backup_create), ("GET", "/backups/{b}", backup_get), ("POST", "/backups/{b}/lock", backup_lock),
    ]:
        app.router.add_route(method, base + path, panel_route(handler))
    app.router.add_get("/download/file", wings_route(wings_download))
    app.router.add_post("/upload/file", wings_route(wings_upload))
    return app


def test_an_update_runs_end_to_end_through_the_real_http_client(mongo: MongoRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "PTERODACTYL_WRITES", True)
    monkeypatch.setattr(client_module, "PTERODACTYL_TOKEN", None)
    problems: list[str] = []

    async def scenario() -> tuple[str, dict[str, str], str, Any]:
        s = await setup()
        async with TestServer(panel_app(s.panel, problems)) as server:
            api = PterodactylAPI()
            api.base_url = str(server.make_url("/api/"))
            from src.library.api.session import TokenAuthorization
            api.session_auth = TokenAuthorization(token="test-panel-key")
            servers = PterodactylService(pterodactylAPI=api, PterodactylDB=PterodactylDatabase(),
                                         modrinthAPI=s.update.servers.modrinthAPI, modrinthDB=s.update.servers.modrinthDB,
                                         modpackDB=ModpackDatabase(), audit=RecordingAudit())
            time = FakeTime()
            update = UpdateService(pterodactylAPI=api, pterodactylService=servers, pterodactylDB=PterodactylDatabase(),
                                   modpackDB=ModpackDatabase(), downloader=s.downloader, jobDB=JobDatabase(),  # type: ignore[arg-type]
                                   audit=RecordingAudit(),
                                   notifier=s.recorder, timing=UpdateTiming(save_wait=5, poll=10, sleep=time.sleep, clock=time.clock))  # type: ignore[arg-type]
            job = await update.enqueue_update(s.server_id, actor=OPERATOR, dry_run=False)
            worker = JobWorker(JobDatabase(), update.handlers(), owner="w1", clock=lambda: NOW)
            while await worker.run_once():
                pass
            await api.close()
            stored = await Job.get(job.id)
            assert stored is not None
            new = next(u.rsplit("/", 1)[-1] for u in s.contents if f"/{s.newest}/" in u)
            return stored.status, s.panel.mods(), new, (stored.error, s.panel.writes)

    status, mods, new, detail = mongo(scenario)
    assert problems == []
    assert status == "done", detail
    assert list(mods) == [new]
