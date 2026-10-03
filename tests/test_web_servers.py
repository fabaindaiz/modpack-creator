"""Las páginas de servidores del panel (etapa 3): lista, detalle, importar, actualizar y rollback.

Los servicios se sustituyen con dobles en el contenedor, como en `tests/test_web_modpacks.py`. Cada
doble anota qué recibió: toda mutación lleva el `actor` de la sesión, y una confirmación que no
coincide no llega nunca al servicio.
"""
from datetime import datetime, timezone
from typing import Any, Optional

import re

import pytest
from beanie import PydanticObjectId
from dependency_injector import providers
from fastapi.testclient import TestClient

from src.exceptions import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT
from src.library.jarmeta import ModMetadata
from src.library.scan import Comparison, ExpectedEntry, ScanFile
from src.model.jobs import Job, RollbackState
from src.model.modpack import ModpackBuild, ModpackBundle, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthProject
from src.model.pterodactyl import ImportCandidate, ImportResult, ProjectCandidate, PterodactylServer, ServerScan
from src.model.users import Actor
from src.model.users.actor import ActorRef
from src.model.utils import MinecraftLoaders
from tests._actors import EDITOR, OPERATOR, session_for
from tests._session import session_cookie

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
SERVER_ID = PydanticObjectId()
BUNDLE_ID = PydanticObjectId()
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
HTMX = {"HX-Request": "true", **SAME_ORIGIN}
EXACT_NAME = "Survival"


def server(name: str = EXACT_NAME, bundle_id: Optional[str] = str(BUNDLE_ID), uuid: str = "uuid-1",
           server_id: Optional[PydanticObjectId] = None) -> PterodactylServer:
    return PterodactylServer.model_construct(
        id=server_id or SERVER_ID, identifier="abc123", internal_id=1, uuid=uuid, name=name, node="node-1",
        description="", status=None, is_suspended=False, is_installing=False, is_transferring=False,
        bundle_id=bundle_id)


def bundle(latest_build: Optional[int] = 2, recommended_build: Optional[int] = 2) -> ModpackBundle:
    return ModpackBundle.model_construct(
        id=BUNDLE_ID, name="Pack Uno", description="", version="1.21.1", loader=MinecraftLoaders.fabric,
        projects=[PackProject(project_id="P1", origin=ProjectOrigin.pack)], latest_build=latest_build, recommended_build=recommended_build)


def scan_file(path: str, project_id: Optional[str] = None, status: str = "identified") -> ScanFile:
    return ScanFile(path=path, size=10, modified_at=T0, status=status,  # type: ignore[arg-type]
                    sha1="s1", sha512="s5-" + path, project_id=project_id, version_id="v" if project_id else None)


def scan(drift: Optional[str] = "✅ todo en orden") -> ServerScan:
    return ServerScan.model_construct(id=PydanticObjectId(), server_id=SERVER_ID, directory="/mods", scanned_at=T0,
                                      full=False, files=[scan_file("/mods/sodium.jar", "P1")], drift=drift)


def job(kind: str = "server_scan", status: str = "queued", active: bool = True, payload: Optional[dict[str, Any]] = None,
        checkpoint: Optional[dict[str, Any]] = None, error: Optional[str] = None,
        requested_by: Optional[ActorRef] = None, key: Optional[str] = f"server:{SERVER_ID}") -> Job:
    return Job.model_construct(id=PydanticObjectId(), kind=kind, key=key, payload=payload or {}, status=status,
                               active=active, step="preflight", checkpoint=checkpoint or {}, error=error,
                               created_at=T0, finished_at=None if active else T0, requested_by=requested_by)


def comparison() -> Comparison:
    return Comparison(
        matching=[scan_file("/mods/sodium.jar", "P1")],
        different_version=[(scan_file("/mods/lithium-old.jar", "P2"), ExpectedEntry(project_id="P2", version_id="v9", server="include"))],
        missing=[ExpectedEntry(project_id="P3", version_id="v3", server="include")],
        extra=[scan_file("/mods/extra.jar", "P4")],
        unknown=[scan_file("/mods/mystery.jar", None, "unknown")])


class FakePterodactyl:
    def __init__(self) -> None:
        self.server = server()
        self.external = [server(), server("Creative", None, "uuid-2", PydanticObjectId())]
        self.scan: Optional[ServerScan] = scan()
        self.comparison: Optional[Comparison] = comparison()
        self.import_result = ImportResult(
            added=["P5"], local=["/mods/local.jar"], too_large=["/mods/huge.jar"],
            candidates=[ImportCandidate(
                path="/mods/odd.jar", sha512="s5", metadata=ModMetadata(loader="fabric", mod_id="odd", name="Odd Mod", version="1"),
                candidates=[ProjectCandidate(project_id="C1", slug="odd", title="Odd Mod", via="slug")])])
        self.calls: list[tuple[Any, ...]] = []
        self.error: Optional[Exception] = None

    def _record(self, *call: Any) -> None:
        self.calls.append(call)
        if self.error is not None:
            raise self.error

    async def server_external(self) -> list[PterodactylServer]:
        return self.external

    async def server_find(self) -> list[PterodactylServer]:
        return [self.server]

    async def server_get(self, server_id: str) -> PterodactylServer:
        return self.server

    async def server_create(self, uuid: str, actor: Actor) -> PterodactylServer:
        self._record("server_create", uuid, actor)
        return self.server

    async def server_update(self, server_id: str, actor: Actor) -> PterodactylServer:
        self._record("server_update", server_id, actor)
        return self.server

    async def server_link_bundle(self, server_id: str, bundle_id: Optional[str], actor: Actor) -> PterodactylServer:
        self._record("server_link_bundle", server_id, bundle_id, actor)
        return self.server

    async def scan_latest(self, server_id: str) -> ServerScan:
        if self.scan is None:
            raise HTTP_404_NOT_FOUND("no scan")
        return self.scan

    async def compare(self, server_id: str, bundle_id: Optional[str] = None, build: Optional[int] = None) -> Comparison:
        if self.comparison is None:
            raise HTTP_409_CONFLICT("not linked")
        return self.comparison

    async def import_server(self, server_id: str, actor: Actor) -> ImportResult:
        self._record("import_server", server_id, actor)
        return self.import_result

    async def import_confirm(self, server_id: str, path: str, project_id: str, actor: Actor) -> list[PackProject]:
        self._record("import_confirm", server_id, path, project_id, actor)
        return []

    async def import_local(self, server_id: str, path: str, actor: Actor) -> list[Any]:
        self._record("import_local", server_id, path, actor)
        return []


class FakeJobs:
    def __init__(self) -> None:
        self.jobs: list[Job] = []
        self.state: Optional[RollbackState] = None
        self.calls: list[tuple[Any, ...]] = []

    async def enqueue_scan(self, server_id: str, actor: Actor, full: bool = False) -> Job:
        self.calls.append(("enqueue_scan", server_id, actor, full))
        return job("server_scan", requested_by=actor.ref())

    async def get(self, job_id: str) -> Job:
        found = next((j for j in self.jobs if str(j.id) == job_id), None)
        if found is None:
            raise HTTP_404_NOT_FOUND(f"Job '{job_id}' not found")
        return found

    async def recent(self, limit: int = 10) -> list[Job]:
        return self.jobs

    async def active_for_server(self, server_id: str) -> Optional[Job]:
        return next((j for j in self.jobs if j.active and j.key == f"server:{server_id}"), None)

    async def rollback_state(self, server_id: str) -> Optional[RollbackState]:
        return self.state


class FakeUpdate:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.errors: list[Optional[Exception]] = []

    def _record(self, *call: Any) -> None:
        self.calls.append(call)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error

    async def enqueue_update(self, server_id: str, actor: Actor, build: Optional[int] = None, dry_run: bool = True) -> Job:
        self._record("enqueue_update", server_id, actor, build, dry_run)
        return job("server_update", payload={"server_id": server_id, "dry_run": dry_run}, requested_by=actor.ref())

    async def enqueue_rollback(self, server_id: str, update_id: str, mode: str, actor: Actor) -> Job:
        self._record("enqueue_rollback", server_id, update_id, mode, actor)
        return job("server_rollback", requested_by=actor.ref())


class FakeModpack:
    def __init__(self) -> None:
        self.builds = True
        self.recommended: Optional[int] = 2

    async def bundle_find(self) -> list[ModpackBundle]:
        return [bundle(5, self.recommended) if self.builds else bundle(None, None)]

    async def bundle_get(self, bundle_id: str) -> ModpackBundle:
        return bundle()

    async def build_find(self, bundle_id: str) -> list[ModpackBuild]:
        """Newest first, as ModpackDatabase.build_find gives them."""
        if not self.builds:
            return []
        return [ModpackBuild.model_construct(bundle_id=BUNDLE_ID, number=n, created_at=T0) for n in (5, 4, 3)]


class FakeModrinth:
    async def projects_by_id(self, project_ids: list[str]) -> dict[str, ModrinthProject]:
        titles = {"P1": "Sodium", "P2": "Lithium", "P3": "Iris", "P4": "Phosphor", "C1": "Odd Mod Project"}
        return {i: ModrinthProject.model_construct(project_id=i, slug=i.lower(), title=titles[i])
                for i in project_ids if i in titles}


class Fakes:
    def __init__(self) -> None:
        self.ptero, self.jobs, self.update = FakePterodactyl(), FakeJobs(), FakeUpdate()
        self.modpack, self.modrinth = FakeModpack(), FakeModrinth()


PROVIDERS = ("pterodactylService", "jobService", "updateService", "modpackService", "modrinthService")


@pytest.fixture(autouse=True)
def reset_overrides() -> Any:
    yield
    import main

    for name in PROVIDERS:
        getattr(main.container, name).reset_override()


def setup(actor: Optional[Actor] = EDITOR) -> tuple[TestClient, Fakes]:
    import main

    fakes = Fakes()
    main.container.pterodactylService.override(providers.Object(fakes.ptero))
    main.container.jobService.override(providers.Object(fakes.jobs))
    main.container.updateService.override(providers.Object(fakes.update))
    main.container.modpackService.override(providers.Object(fakes.modpack))
    main.container.modrinthService.override(providers.Object(fakes.modrinth))
    client = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
    if actor is not None:
        client.cookies.set("session", session_cookie(session_for(actor)))
    return client, fakes


def only(calls: list[tuple[Any, ...]], name: str) -> tuple[Any, ...]:
    [found] = [c for c in calls if c[0] == name]
    return found


# Lista.

def test_list_shows_servers_and_additions_from_the_panel() -> None:
    client, _ = setup()
    response = client.get("/servers")
    assert response.status_code == 200
    assert f'href="/servers/{SERVER_ID}"' in response.text and "Survival" in response.text
    # "Creative" está en el panel y no aquí: se ofrece como alta; "Survival" ya está.
    assert 'value="uuid-2"' in response.text and 'value="uuid-1"' not in response.text
    assert 'action="/servers"' in response.text


def test_list_survives_an_unreachable_panel() -> None:
    client, fakes = setup()

    async def down() -> list[PterodactylServer]:
        raise RuntimeError("panel down")

    fakes.ptero.server_external = down  # type: ignore[method-assign]
    response = client.get("/servers")
    assert response.status_code == 200 and "Survival" in response.text
    assert "No se pudo consultar el panel" in response.text


def test_create_registers_the_server_with_the_actor() -> None:
    client, fakes = setup()
    response = client.post("/servers", headers=SAME_ORIGIN, data={"uuid": "uuid-2"})
    assert response.status_code == 303 and response.headers["location"] == f"/servers/{SERVER_ID}"
    assert only(fakes.ptero.calls, "server_create")[1:] == ("uuid-2", EDITOR)


# Detalle.

def test_detail_shows_bundle_scan_and_comparison() -> None:
    client, _ = setup()
    response = client.get(f"/servers/{SERVER_ID}")
    assert response.status_code == 200
    text = response.text
    assert "Survival" in text and f'href="/modpacks/{BUNDLE_ID}"' in text and "Pack Uno" in text
    assert "todo en orden" in text and "2026-10-01 12:00" in text
    assert f'hx-post="/servers/{SERVER_ID}/scan"' in text and f'hx-post="/servers/{SERVER_ID}/refresh"' in text
    assert 'id="compare"' in text and "Sodium" in text


def test_detail_without_scan_or_bundle_says_so() -> None:
    client, fakes = setup()
    fakes.ptero.server = server(bundle_id=None)
    fakes.ptero.scan = None
    fakes.ptero.comparison = None
    response = client.get(f"/servers/{SERVER_ID}")
    assert response.status_code == 200
    assert "Sin escaneo" in response.text and "Sin modpack" in response.text


def test_detail_of_a_modpack_without_builds_explains_it_in_spanish() -> None:
    client, fakes = setup()
    fakes.modpack.builds = False
    fakes.ptero.comparison = None  # the service would answer its English 409
    response = client.get(f"/servers/{SERVER_ID}")
    assert response.status_code == 200
    assert "todavía no tiene builds" in response.text
    assert "has no build to compare" not in response.text and 'id="compare"' not in response.text


def test_detail_of_a_missing_server_is_a_404() -> None:
    client, fakes = setup()

    async def missing(server_id: str) -> PterodactylServer:
        raise HTTP_404_NOT_FOUND("Server 'x' not found")

    fakes.ptero.server_get = missing  # type: ignore[method-assign]
    assert client.get(f"/servers/{SERVER_ID}").status_code == 404


def test_detail_shows_the_active_job_polling() -> None:
    client, fakes = setup()
    running = job("server_scan", "running")
    fakes.jobs.jobs = [running, job("server_scan", "done", False, key="server:other")]
    text = client.get(f"/servers/{SERVER_ID}").text
    assert f'hx-get="/jobs/{running.id}/row"' in text


def test_compare_renders_every_category() -> None:
    client, _ = setup()
    text = client.get(f"/servers/{SERVER_ID}").text
    for fragment in ("Coincide", "Otra versión", "Falta", "Sobra", "Desconocido"):
        assert fragment in text
    for shown in ("Sodium", "sodium.jar", "lithium-old.jar", "Lithium", "Iris", "Phosphor", "extra.jar", "mystery.jar"):
        assert shown in text


# Escanear y refrescar.

def test_scan_returns_a_polling_job_row_with_the_actor() -> None:
    client, fakes = setup()
    response = client.post(f"/servers/{SERVER_ID}/scan", headers=HTMX)
    assert response.status_code == 200
    assert only(fakes.jobs.calls, "enqueue_scan")[1:] == (str(SERVER_ID), EDITOR, False)
    assert 'hx-trigger="every 2s"' in response.text and 'hx-swap="outerHTML"' in response.text
    assert "/row" in response.text and "Eddie Editor" in response.text


def test_scan_of_a_busy_server_is_a_conflict_notice() -> None:
    client, fakes = setup()

    async def busy(server_id: str, actor: Actor, full: bool = False) -> Job:
        raise HTTP_409_CONFLICT(f"There is already an active job for server:{server_id}")

    fakes.jobs.enqueue_scan = busy  # type: ignore[method-assign]
    response = client.post(f"/servers/{SERVER_ID}/scan", headers=HTMX)
    assert response.status_code == 409 and "There is already an active job" in response.text


def test_refresh_updates_from_the_panel_with_the_actor() -> None:
    client, fakes = setup()
    response = client.post(f"/servers/{SERVER_ID}/refresh", headers=HTMX)
    assert response.status_code == 200 and response.headers["HX-Redirect"] == f"/servers/{SERVER_ID}"
    assert only(fakes.ptero.calls, "server_update")[1:] == (str(SERVER_ID), EDITOR)


# Acciones de operador.

def test_link_bundle() -> None:
    client, fakes = setup(OPERATOR)
    response = client.put(f"/servers/{SERVER_ID}/bundle", headers=HTMX, data={"bundle_id": str(BUNDLE_ID)})
    assert response.status_code == 200 and response.headers["HX-Redirect"] == f"/servers/{SERVER_ID}"
    assert only(fakes.ptero.calls, "server_link_bundle")[1:] == (str(SERVER_ID), str(BUNDLE_ID), OPERATOR)


def test_unlink_bundle_with_an_empty_choice() -> None:
    client, fakes = setup(OPERATOR)
    client.put(f"/servers/{SERVER_ID}/bundle", headers=HTMX, data={"bundle_id": ""})
    assert only(fakes.ptero.calls, "server_link_bundle")[2] is None


def test_import_shows_added_local_too_large_and_candidates() -> None:
    client, fakes = setup(OPERATOR)
    response = client.post(f"/servers/{SERVER_ID}/import", headers=HTMX)
    assert response.status_code == 200
    assert only(fakes.ptero.calls, "import_server")[1:] == (str(SERVER_ID), OPERATOR)
    text = response.text
    assert "/mods/local.jar" in text and "/mods/huge.jar" in text and "/mods/odd.jar" in text
    assert "Odd Mod Project" in text or "Odd Mod" in text
    assert f'hx-post="/servers/{SERVER_ID}/import/confirm"' in text and f'hx-post="/servers/{SERVER_ID}/import/local"' in text
    assert 'name="project_id" value="C1"' in text


def test_import_confirm_and_local_carry_the_actor() -> None:
    client, fakes = setup(OPERATOR)
    response = client.post(f"/servers/{SERVER_ID}/import/confirm", headers=HTMX,
                           data={"path": "/mods/odd.jar", "project_id": "C1"})
    assert response.status_code == 200
    assert only(fakes.ptero.calls, "import_confirm")[1:] == (str(SERVER_ID), "/mods/odd.jar", "C1", OPERATOR)
    response = client.post(f"/servers/{SERVER_ID}/import/local", headers=HTMX, data={"path": "/mods/odd.jar"})
    assert response.status_code == 200
    assert only(fakes.ptero.calls, "import_local")[1:] == (str(SERVER_ID), "/mods/odd.jar", OPERATOR)


def test_preview_is_a_dry_run_and_returns_a_polling_row() -> None:
    client, fakes = setup(OPERATOR)
    response = client.post(f"/servers/{SERVER_ID}/update/preview", headers=HTMX)
    assert response.status_code == 200
    assert only(fakes.update.calls, "enqueue_update")[1:] == (str(SERVER_ID), OPERATOR, None, True)
    assert 'hx-trigger="every 2s"' in response.text


def test_update_with_the_exact_name_queues_a_real_run() -> None:
    client, fakes = setup(OPERATOR)
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={"confirm": EXACT_NAME, "build": "2"})
    assert response.status_code == 200
    assert only(fakes.update.calls, "enqueue_update")[1:] == (str(SERVER_ID), OPERATOR, 2, False)


def test_rollback_carries_the_mode_and_actor() -> None:
    client, fakes = setup(OPERATOR)
    update_id = str(PydanticObjectId())
    response = client.post(f"/servers/{SERVER_ID}/rollback", headers=HTMX, data={"update_id": update_id, "mode": "B"})
    assert response.status_code == 200
    assert only(fakes.update.calls, "enqueue_rollback")[1:] == (str(SERVER_ID), update_id, "B", OPERATOR)
    assert client.post(f"/servers/{SERVER_ID}/rollback", headers=HTMX,
                       data={"update_id": update_id, "mode": "C"}).status_code == 422


def failed_real_update(checkpoint: dict[str, Any]) -> Job:
    return job("server_update", "failed", False, {"server_id": str(SERVER_ID), "dry_run": False},
               checkpoint, "server stopped")


def test_rollback_buttons_follow_the_state() -> None:
    client, fakes = setup(OPERATOR)
    failed = failed_real_update({"swap": {}, "backup": {}})
    fakes.jobs.state = RollbackState(update=failed, allowed=["A", "B"])
    text = client.get(f"/servers/{SERVER_ID}").text
    assert f'name="update_id" value="{failed.id}"' in text
    assert 'name="mode" value="A"' in text and 'name="mode" value="B"' in text
    assert "rollback-dialog" not in text and text.count('name="confirm"') == 1  # the direct update's
    fakes.jobs.state = RollbackState(update=failed, allowed=["B"])
    text = client.get(f"/servers/{SERVER_ID}").text
    assert 'name="mode" value="A"' not in text and 'name="mode" value="B"' in text


def reverted_with_a() -> tuple[Job, Job]:
    failed = failed_real_update({"swap": {}, "backup": {}})
    done_a = job("server_rollback", "done", False, {"server_id": str(SERVER_ID), "update_id": str(failed.id), "mode": "A"})
    return failed, done_a


def test_after_done_a_only_b_with_name_confirmation() -> None:
    client, fakes = setup(OPERATOR)
    failed, done_a = reverted_with_a()
    fakes.jobs.state = RollbackState(update=failed, allowed=["B"], reverted_with_a=done_a)
    text = client.get(f"/servers/{SERVER_ID}").text
    assert "Revertido con A el" in text and "pierde lo jugado desde entonces" in text
    assert 'name="mode" value="B"' in text and 'name="mode" value="A"' not in text
    assert f'name="update_id" value="{failed.id}"' in text
    assert "rollback-dialog" in text and 'name="confirm"' in text and "<code" in text
    assert f'hx-post="/servers/{SERVER_ID}/rollback"' in text and "hx-confirm" not in text
    response = client.post(f"/servers/{SERVER_ID}/rollback", headers=HTMX,
                           data={"update_id": str(failed.id), "mode": "B", "confirm": f" {EXACT_NAME} "})
    assert response.status_code == 200
    assert only(fakes.update.calls, "enqueue_rollback")[1:] == (str(SERVER_ID), str(failed.id), "B", OPERATOR)


def test_after_done_a_an_editor_sees_b_disabled_without_the_dialog() -> None:
    client, fakes = setup(EDITOR)
    failed, done_a = reverted_with_a()
    fakes.jobs.state = RollbackState(update=failed, allowed=["B"], reverted_with_a=done_a)
    text = client.get(f"/servers/{SERVER_ID}").text
    assert "Revertido con A el" in text and "requiere operador" in text
    assert "rollback-dialog" not in text and f'hx-post="/servers/{SERVER_ID}/rollback"' not in text


def test_after_done_b_no_rollback_offered() -> None:
    """JobService.rollback_state is None after a done B (tests/test_operator_actions.py): the page
    offers nothing, and a request without a name goes to the service, which refuses it."""
    client, fakes = setup(OPERATOR)
    fakes.jobs.state = None
    text = client.get(f"/servers/{SERVER_ID}").text
    assert 'name="mode"' not in text and "rollback-dialog" not in text and "La última actualización falló" not in text
    fakes.update.errors = [HTTP_409_CONFLICT("La actualización ya se revirtió con B")]
    response = client.post(f"/servers/{SERVER_ID}/rollback", headers=HTMX,
                           data={"update_id": str(PydanticObjectId()), "mode": "A"})
    assert response.status_code == 409 and "ya se revirtió con B" in response.text


@pytest.mark.parametrize("typed", [None, "", "survival", "Surv", "Survival 2", "SURVIVAL"])
def test_b_after_a_requires_exact_name(typed: Optional[str]) -> None:
    client, fakes = setup(OPERATOR)
    failed, done_a = reverted_with_a()
    fakes.jobs.state = RollbackState(update=failed, allowed=["B"], reverted_with_a=done_a)
    data = {"update_id": str(failed.id), "mode": "B", **({"confirm": typed} if typed is not None else {})}
    response = client.post(f"/servers/{SERVER_ID}/rollback", headers=HTMX, data=data)
    assert response.status_code == 422 and "Escribe el nombre exacto del servidor" in response.text
    assert fakes.update.calls == []


def test_an_editor_is_refused_a_rollback_before_the_name_is_compared() -> None:
    client, fakes = setup(EDITOR)
    failed, done_a = reverted_with_a()
    fakes.jobs.state = RollbackState(update=failed, allowed=["B"], reverted_with_a=done_a)
    for confirm in ("", EXACT_NAME):
        response = client.post(f"/servers/{SERVER_ID}/rollback", headers=HTMX,
                               data={"update_id": str(failed.id), "mode": "B", "confirm": confirm})
        assert response.status_code == 403
    assert fakes.update.calls == []


# Los focos de revisión.

@pytest.mark.parametrize("typed", ["survival", "Surv", "Survival 2", "", "SURVIVAL", "Sur vival"])
def test_update_requires_exact_name(typed: str) -> None:
    client, fakes = setup(OPERATOR)
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={"confirm": typed, "build": "2"})
    assert response.status_code == 422 and "Escribe el nombre exacto del servidor" in response.text
    assert fakes.update.calls == []


def test_update_accepts_the_name_with_surrounding_whitespace() -> None:
    client, fakes = setup(OPERATOR)
    fakes.ptero.server = server("Survival")
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={"confirm": "  Survival ", "build": "2"})
    assert response.status_code == 200
    assert only(fakes.update.calls, "enqueue_update")[-1] is False


def test_update_without_the_field_is_the_same_422() -> None:
    client, fakes = setup(OPERATOR)
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={"build": "2"})
    assert response.status_code == 422 and fakes.update.calls == []


def test_update_without_a_build_is_a_notice_and_never_reaches_the_service() -> None:
    client, fakes = setup(OPERATOR)
    for data in ({"confirm": EXACT_NAME}, {"confirm": EXACT_NAME, "build": ""}, {"confirm": EXACT_NAME, "build": "x"}):
        response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data=data)
        assert response.status_code == 422 and "data-notice" in response.text
    assert fakes.update.calls == []


def test_second_real_update_shows_conflict_notice() -> None:
    client, fakes = setup(OPERATOR)
    fakes.update.errors = [None, HTTP_409_CONFLICT("There is already an active job for server:x")]
    first = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={"confirm": EXACT_NAME, "build": "2"})
    assert first.status_code == 200
    second = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={"confirm": EXACT_NAME, "build": "2"})
    assert second.status_code == 409
    assert "There is already an active job for server:x" in second.text and "data-notice" in second.text


def test_editor_sees_operator_buttons_disabled() -> None:
    client, _ = setup(EDITOR)
    text = client.get(f"/servers/{SERVER_ID}").text
    assert "requiere operador" in text and "disabled" in text
    assert f'hx-post="/servers/{SERVER_ID}/update/preview"' not in text
    assert f'hx-post="/servers/{SERVER_ID}/import"' not in text
    assert "update-dialog" not in text


def test_operator_sees_both_update_buttons() -> None:
    client, _ = setup(OPERATOR)
    text = client.get(f"/servers/{SERVER_ID}").text
    assert f'hx-post="/servers/{SERVER_ID}/update/preview"' in text and f'hx-post="/servers/{SERVER_ID}/import"' in text
    assert "Simular actualización" in text and "Actualizar…" in text and "requiere operador" not in text


def test_direct_update_dialog_lists_builds_with_recommended_selected() -> None:
    client, fakes = setup(OPERATOR)
    fakes.modpack.recommended = 4
    text = client.get(f"/servers/{SERVER_ID}").text
    assert f'hx-post="/servers/{SERVER_ID}/update"' in text and "Actualizar Survival" in text
    assert text.index("Build 5 (último)") < text.index("Build 4 (recomendado)") < text.index("Build 3")
    assert re.search(r'<option value="4" selected>\s*Build 4 \(recomendado\)', text)
    assert len(re.findall(r'<option value="\d+" selected>', text)) == 1 and "Actualizar al build elegido" in text
    assert 'name="build"' in text and 'name="confirm"' in text and "<code" in text
    # Sin recomendado, el último.
    fakes.modpack.recommended = None
    text = client.get(f"/servers/{SERVER_ID}").text
    assert re.search(r'<option value="5" selected>\s*Build 5 \(último\)', text)


def test_direct_update_posts_the_selected_build() -> None:
    client, fakes = setup(OPERATOR)
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={"build": "3", "confirm": EXACT_NAME})
    assert response.status_code == 200
    assert only(fakes.update.calls, "enqueue_update")[1:] == (str(SERVER_ID), OPERATOR, 3, False)


def test_direct_update_disabled_without_builds() -> None:
    client, fakes = setup(OPERATOR)
    fakes.modpack.builds = False
    text = client.get(f"/servers/{SERVER_ID}").text
    assert "update-dialog" not in text and f'hx-post="/servers/{SERVER_ID}/update"' not in text
    assert "Actualizar…" in text and "sin builds" in text
    fakes.modpack.builds = True
    fakes.ptero.server = server(bundle_id=None)
    text = client.get(f"/servers/{SERVER_ID}").text
    assert f'hx-post="/servers/{SERVER_ID}/update"' not in text and "sin modpack" in text


def test_direct_update_is_disabled_for_an_editor() -> None:
    client, _ = setup(EDITOR)
    text = client.get(f"/servers/{SERVER_ID}").text
    assert "Actualizar…" in text and "requiere operador" in text and "update-dialog" not in text


@pytest.mark.parametrize("error", [HTTP_404_NOT_FOUND("Build 9 not found"), HTTP_409_CONFLICT("El servidor no tiene modpack")])
def test_stale_build_is_a_notice(error: Exception) -> None:
    client, fakes = setup(OPERATOR)
    fakes.update.errors = [error]
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={"build": "9", "confirm": EXACT_NAME})
    assert response.status_code == error.status_code and "data-notice" in response.text  # type: ignore[attr-defined]
    assert str(error.detail) in response.text  # type: ignore[attr-defined]


def test_preview_result_still_offers_real_run_with_simulated_build() -> None:
    html = row(finished_dry_run(3), server_name="Survival")
    assert f'hx-post="/servers/{SERVER_ID}/update"' in html and 'name="build" value="3"' in html
    assert "Ejecutar de verdad" in html and 'name="confirm"' in html and "<select" not in html


def test_after_done_a_a_failed_b_attempt_is_said() -> None:
    client, fakes = setup(OPERATOR)
    failed, done_a = reverted_with_a()
    failed_b = job("server_rollback", "failed", False, {"server_id": str(SERVER_ID), "update_id": str(failed.id), "mode": "B"},
                   error="backup not found")
    fakes.jobs.state = RollbackState(update=failed, allowed=["B"], reverted_with_a=done_a, failed_b=failed_b)
    text = client.get(f"/servers/{SERVER_ID}").text
    assert "El último intento de B falló el" in text and "backup not found" in text
    assert text.index("El último intento de B falló") < text.index('name="mode" value="B"')
    fakes.jobs.state = RollbackState(update=failed, allowed=["B"], reverted_with_a=done_a)
    assert "intento de B falló" not in client.get(f"/servers/{SERVER_ID}").text


def finished_dry_run(build_number: int = 3, changes: bool = True) -> Job:
    plan = {"build": build_number, "bundle": "Pack Uno", "remove": ["/mods/old.jar"] if changes else [],
            "install": [{"project_id": "P2", "filename": "lithium-2.jar", "url": "u", "sha512": "x", "size": 1}] if changes else []}
    return job("server_update", "done", False, {"server_id": str(SERVER_ID), "dry_run": True},
               {"preflight": plan, "log": {"lines": ["stop server"]}})


def row(job_: Job, actor: Optional[Actor] = OPERATOR, **context: Any) -> str:
    from src.web import templates

    return templates.get_template("activity/_job_row.html").render(job=job_, **({"actor": actor} if actor else {}), **context)


def test_real_run_form_appears_only_in_a_finished_dry_run_for_an_operator() -> None:
    html = row(finished_dry_run(3), server_name="Survival")
    assert f'hx-post="/servers/{SERVER_ID}/update"' in html and 'name="build" value="3"' in html
    assert 'name="confirm"' in html and "Ejecutar de verdad" in html and "<code" in html
    # Sin cambios, con la simulación fallida o en curso, o sin ser de la simulación: no hay formulario.
    assert 'name="confirm"' not in row(finished_dry_run(3, changes=False))
    failed = job("server_update", "failed", False, {"server_id": "x", "dry_run": True}, error="no backup slot left")
    assert 'name="confirm"' not in row(failed)
    running = job("server_update", "running", True, {"server_id": "x", "dry_run": True})
    assert 'name="confirm"' not in row(running)
    real = job("server_update", "done", False, {"server_id": "x", "dry_run": False},
               {"preflight": finished_dry_run().checkpoint["preflight"]})
    assert 'name="confirm"' not in row(real)
    # Un editor ve el botón deshabilitado, sin formulario.
    editor_html = row(finished_dry_run(3), EDITOR)
    assert 'name="confirm"' not in editor_html and "requiere operador" in editor_html and "hx-post" not in editor_html


def test_an_editor_is_refused_before_anything_is_compared() -> None:
    client, fakes = setup(EDITOR)
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={"confirm": EXACT_NAME, "build": "2"})
    assert response.status_code == 403 and fakes.update.calls == []


def test_detail_and_import_survive_a_failing_title_lookup() -> None:
    client, fakes = setup(OPERATOR)

    async def down(project_ids: list[str]) -> dict[str, ModrinthProject]:
        raise RuntimeError("modrinth down")

    import main

    class Down(FakeModrinth):
        projects_by_id = staticmethod(down)  # type: ignore[assignment]

    main.container.modrinthService.override(providers.Object(Down()))
    detail = client.get(f"/servers/{SERVER_ID}")
    assert detail.status_code == 200 and "P3" in detail.text
    imported = client.post(f"/servers/{SERVER_ID}/import", headers=HTMX)
    assert imported.status_code == 200 and "C1" in imported.text


# La fila de trabajo.

def test_job_row_polls_while_active_and_stops_when_finished() -> None:
    active = row(job("server_scan", "running", requested_by=ActorRef(iss="i", sub="s", name="Ana")))
    assert f'hx-get="/jobs/' in active and 'hx-trigger="every 2s"' in active and 'hx-swap="outerHTML"' in active
    assert "Ana" in active and "En curso" in active
    done = row(job("server_scan", "done", False))
    assert "hx-trigger" not in done and "programado" in done and "Listo" in done


def test_finished_dry_run_row_shows_the_plan_and_what_it_would_do() -> None:
    plan = {"build": 2, "bundle": "Pack Uno", "remove": ["/mods/old.jar"],
            "install": [{"project_id": "P2", "filename": "lithium-2.jar", "url": "u", "sha512": "x", "size": 1}]}
    finished = job("server_update", "done", False, {"server_id": "x", "dry_run": True},
                   {"preflight": plan, "log": {"lines": ["stop server", "<b>upload</b> mods"]}})
    html = row(finished)
    assert "lithium-2.jar" in html and "/mods/old.jar" in html and "stop server" in html
    assert "<b>upload</b>" not in html and "&lt;b&gt;upload" in html
    failed = job("server_update", "failed", False, {"server_id": "x", "dry_run": True}, error="no backup slot left")
    assert "no backup slot left" in row(failed)
    empty = job("server_update", "done", False, {"server_id": "x", "dry_run": True},
                {"preflight": {"build": 2, "bundle": "P", "remove": [], "install": []}})
    assert "Nada que cambiar" in row(empty)


def test_names_are_escaped() -> None:
    client, fakes = setup()
    fakes.ptero.server = server('<script>alert(1)</script>')
    text = client.get(f"/servers/{SERVER_ID}").text
    assert "<script>alert" not in text and "&lt;script&gt;" in text
