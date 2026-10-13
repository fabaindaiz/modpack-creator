"""Las páginas de servidores del panel (etapa 3): lista, detalle, importar, actualizar y rollback.

Los servicios se sustituyen con dobles en el contenedor, como en `tests/test_web_modpacks.py`. Cada
doble anota qué recibió: toda mutación lleva el `actor` de la sesión, y una confirmación que no
coincide no llega nunca al servicio.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import re
from types import SimpleNamespace

import pytest
from beanie import PydanticObjectId
from dependency_injector import providers
from fastapi.testclient import TestClient

from src.exceptions import HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND, HTTP_409_CONFLICT, UpstreamError
from src.library.deduce import Deduction
from src.library.jarmeta import ModMetadata
from src.library.scan import Comparison, ExpectedEntry, ScanFile
from src.model.jobs import Job, RollbackState
from src.model.modpack import ModpackBuild, ModpackBundle, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthProject
from src.model.pterodactyl import BundleForServer, PanelServers, ImportCandidate, ImportResult, ProjectCandidate, PterodactylServer, ServerScan
from src.model.users import Actor
from src.model.users.actor import ActorRef
from src.model.utils import MinecraftLoaders
from tests._actors import EDITOR, OPERATOR, session_for
from tests._session import session_cookie
from tests._tables import assert_cells_labelled
from tests.conftest import MongoRunner
from tests.test_web_modpacks import visible

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
SERVER_ID = PydanticObjectId()
BUNDLE_ID = PydanticObjectId()
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
HTMX = {"HX-Request": "true", **SAME_ORIGIN}
EXACT_NAME = "Survival"
# The bundle the page was planned for: both update forms send it back.
LINKED = {"bundle_id": str(BUNDLE_ID)}
PENDING_ROLLBACK = "Hay una actualización fallida sin revertir"


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


NEW_BUNDLE_ID = PydanticObjectId()


def new_bundle(name: str = "Creativo") -> ModpackBundle:
    return ModpackBundle.model_construct(id=NEW_BUNDLE_ID, name=name, description="", version="1.21.1",
                                         loader=MinecraftLoaders.fabric, projects=[], latest_build=None,
                                         recommended_build=None)


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
        self.deduced: Optional[Deduction] = None
        self.version_labels: dict[str, str] = {}
        self.for_server: BundleForServer = BundleForServer(bundle=new_bundle(), imported=None, import_error=None)
        # What the last import job left (T1, S3); None if never imported by a job.
        self.pending: Optional[ImportResult] = None
        # The panel's list (U6): when it was asked; what is kept, and since when, if the panel fails.
        self.panel_at: Optional[datetime] = datetime.now(timezone.utc) - timedelta(minutes=12)
        self.panel_kept: list[PterodactylServer] = []
        self.panel_kept_at: Optional[datetime] = None
        self.calls: list[tuple[Any, ...]] = []
        self.error: Optional[Exception] = None

    def _record(self, *call: Any) -> None:
        self.calls.append(call)
        if self.error is not None:
            raise self.error

    async def server_external(self) -> list[PterodactylServer]:
        return self.external

    async def panel_servers(self, force: bool = False) -> PanelServers:
        try:
            return PanelServers(servers=await self.server_external(), fetched_at=self.panel_at)
        except Exception:
            return PanelServers(servers=self.panel_kept, fetched_at=self.panel_kept_at, failed=True)

    async def panel_refresh(self, actor: Actor) -> PanelServers:
        self._record("panel_refresh", actor)
        return await self.panel_servers(force=True)

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

    async def import_request(self, server_id: str, actor: Actor) -> Job:
        self._record("import_request", server_id, actor)
        return import_job()

    async def import_pending(self, server_id: str) -> Optional[ImportResult]:
        return self.pending

    async def import_confirm(self, server_id: str, path: str, project_id: str, actor: Actor) -> list[PackProject]:
        self._record("import_confirm", server_id, path, project_id, actor)
        return []

    async def import_local(self, server_id: str, path: str, actor: Actor) -> list[Any]:
        self._record("import_local", server_id, path, actor)
        return []

    async def deduction(self, server_id: str) -> Optional[Deduction]:
        return self.deduced

    async def compare_names(self, comparison: Comparison) -> dict[str, str]:
        return self.version_labels

    async def bundle_for_server(self, server_id: str, name: str, version: str, loader: MinecraftLoaders,
                                import_mods: bool, actor: Actor) -> BundleForServer:
        if not actor.is_operator:  # as the real service's operator_only
            raise HTTP_403_FORBIDDEN("This action requires the operator role")
        self._record("bundle_for_server", server_id, name, version, loader, import_mods, actor)
        return self.for_server


class FakeJobs:
    def __init__(self) -> None:
        self.jobs: list[Job] = []
        self.state: Optional[RollbackState] = None
        self.installed: Optional[int] = None
        self.simulated: Optional[int] = None
        self.calls: list[tuple[Any, ...]] = []

    async def installed_build(self, server_id: str) -> Optional[int]:
        return self.installed

    async def simulated_build(self, server_id: str, bundle_id: str) -> Optional[int]:
        return self.simulated

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


def change_preview(failed: bool = False) -> Any:
    from src.library.changes import Change, ChangeSet
    from src.services.modpack import ChangePreview

    changes = ChangeSet(added=[Change("P3", None, "v-new")], removed=[Change("P2", "v-p2", None)],
                        changed=[Change("P1", "v-old", "v-p1")], unchanged=4)
    return ChangePreview(changes=changes, changelogs={} if failed else {"v-p1": "Arregla un crash", "v-new": None},
                         failed={"v-p1", "v-new"} if failed else set())


class FakeModpack:
    def __init__(self) -> None:
        self.builds = True
        self.recommended: Optional[int] = 2
        # The pack's saved resolution and whether it is stale (B1's facts); None: never resolved.
        self.saved: Optional[tuple[Any, bool]] = None
        self.resolution_error: Optional[Exception] = None
        self.preview: Any = change_preview()
        self.preview_error: Optional[Exception] = None
        self.calls: list[tuple[Any, ...]] = []

    async def server_change_preview(self, server_id: str, build_number: int) -> Any:
        self.calls.append(("server_change_preview", server_id, build_number))
        if self.preview_error is not None:
            raise self.preview_error
        return self.preview

    async def bundle_find(self) -> list[ModpackBundle]:
        return [bundle(5, self.recommended) if self.builds else bundle(None, None)]

    async def resolution(self, bundle_id: str) -> Optional[tuple[Any, bool]]:
        if self.resolution_error is not None:
            raise self.resolution_error
        return self.saved

    async def bundle_get(self, bundle_id: str) -> ModpackBundle:
        return bundle()

    async def bundle_create(self, bundle: ModpackBundle, actor: Actor) -> ModpackBundle:
        self.calls.append(("bundle_create", bundle, actor))
        return new_bundle(bundle.name)

    async def build_find(self, bundle_id: str) -> list[ModpackBuild]:
        """Newest first, as ModpackDatabase.build_find gives them."""
        if not self.builds:
            return []
        return [ModpackBuild.model_construct(bundle_id=BUNDLE_ID, number=n, created_at=T0) for n in (5, 4, 3)]


class FakeModrinth:
    def __init__(self) -> None:
        self.filled: list[list[str]] = []
        # The Minecraft releases Modrinth lists (F09); None: Modrinth did not answer.
        self.game_versions: Optional[list[str]] = None

    async def game_versions_release(self) -> Optional[list[str]]:
        return self.game_versions

    async def projects_filled(self, project_ids: list[str]) -> dict[str, ModrinthProject]:
        self.filled.append(list(project_ids))
        titles = {"P1": "Sodium", "P2": "Lithium", "P3": "Iris", "P4": "Phosphor", "C1": "Odd Mod Project",
                  "J1": "JEI", "A1": "AppleSkin", "F1": "FerriteCore"}
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


def test_the_panel_list_says_its_age_and_offers_the_refresh() -> None:
    # U6 (owner, 2026-10-12): the list is kept 5 minutes; the page says how old it is.
    client, _ = setup()
    text = client.get("/servers").text
    section = " ".join(visible(text[text.index('id="altas"'):]).split())
    assert "Lista del panel de hace 12 min" in section and "Actualizar lista" in section
    assert 'hx-post="/servers/lista"' in text


def test_a_panel_that_does_not_answer_shows_the_kept_list_and_its_age() -> None:
    client, fakes = setup()

    async def down() -> list[PterodactylServer]:
        raise RuntimeError("panel down")

    fakes.ptero.server_external = down  # type: ignore[method-assign]
    fakes.ptero.panel_kept = [fakes.ptero.external[-1]]
    fakes.ptero.panel_kept_at = datetime.now(timezone.utc) - timedelta(hours=2)
    text = client.get("/servers").text
    section = " ".join(visible(text[text.index('id="altas"'):]).split())
    assert "El panel no respondió; lista de hace 2 h" in section and 'value="uuid-2"' in text


def test_the_refresh_asks_the_panel_and_goes_back_to_the_list() -> None:
    client, fakes = setup()
    response = client.post("/servers/lista", headers=HTMX)
    assert response.status_code == 200 and response.headers["HX-Redirect"] == "/servers"
    assert only(fakes.ptero.calls, "panel_refresh")[1:] == (EDITOR,)


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
    assert "2026-10-01 12:00" in text
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


def opening_tag(text: str, element_id: str) -> str:
    match = re.search(rf'<[a-z]+ [^>]*id="{element_id}"[^>]*>', text)
    assert match, f"no element #{element_id}"
    return match.group(0)


def test_server_main_reloads_itself_and_excludes_the_polling_row() -> None:
    """When a job ends (`job-finished`, sent by its finished row), #server-main reloads itself
    from the page. The polling row is outside it: the reload must not restart the polling, nor
    the row's own swap wipe the sections."""
    client, fakes = setup()
    running = job("server_scan", "running")
    fakes.jobs.jobs = [running]
    text = client.get(f"/servers/{SERVER_ID}").text
    tag = opening_tag(text, "server-main")
    for attribute in (f'hx-get="/servers/{SERVER_ID}"', 'hx-select="#server-main"',
                      'hx-trigger="job-finished from:body"', 'hx-swap="outerHTML"', 'hx-disinherit="*"'):
        assert attribute in tag
    main_at, job_at = text.index(tag), text.index('id="server-job"')
    assert job_at < main_at and text.index(f'hx-get="/jobs/{running.id}/row"') < main_at
    # The sections are inside it, in the order of the spec: summary, comparison, then the side column.
    order = [text.index(f'id="{section}"') for section in
             ("server-summary", "comparacion", "actualizar", "modpack", "escaneo", "importar")]
    assert main_at < order[0] and order == sorted(order)


def summary(text: str) -> str:
    """The summary line as read: without tags, its spaces collapsed."""
    start = text.index('id="server-summary"')
    return " ".join(re.sub(r"<[^>]+>", " ", text[text.index(">", start) + 1:text.index("</div>", start)]).split())


def test_summary_line_says_installed_recommended_and_differences() -> None:
    client, fakes = setup()
    fakes.jobs.installed = 3
    line = summary(client.get(f"/servers/{SERVER_ID}").text)
    assert "Pack Uno" in line and "Instalado: build 3" in line and "Recomendado: build 2" in line
    assert "2026-10-01 12:00" in line and "4 diferencias" in line  # otra versión, falta, sobra, desconocido
    fakes.jobs.installed = None
    fakes.ptero.comparison = Comparison(matching=[scan_file("/mods/sodium.jar", "P1")], different_version=[],
                                        missing=[], extra=[], unknown=[])
    line = summary(client.get(f"/servers/{SERVER_ID}").text)
    assert "sin actualización terminada desde el panel" in line and "Coincide" in line and "diferencia" not in line
    fakes.ptero.scan, fakes.ptero.comparison = None, None
    assert "Sin escaneo" in summary(client.get(f"/servers/{SERVER_ID}").text)


def test_comparison_uses_titles_and_version_numbers() -> None:
    client, fakes = setup()
    fakes.ptero.comparison = Comparison(
        matching=[],
        different_version=[(scan_file("/mods/jei-19.57.jar", "J1"),
                            ExpectedEntry(project_id="J1", version_id="EO4u1E2G", server="include"))],
        missing=[ExpectedEntry(project_id="A1", version_id="Ap9pLe5k", server="include")],
        extra=[], unknown=[])
    fakes.ptero.version_labels = {"EO4u1E2G": "19.51.0.418", "Ap9pLe5k": "21.1.0", "v": "jei-19.57.jar"}
    text = client.get(f"/servers/{SERVER_ID}").text
    assert "el build espera 19.51.0.418" in text and "versión 21.1.0" in text
    assert "EO4u1E2G" not in text and "Ap9pLe5k" not in text
    # The titles come from the catalog, filled from Modrinth for what it lacks.
    assert "JEI" in text and "AppleSkin" in text and set(fakes.modrinth.filled[-1]) == {"J1", "A1"}


def test_client_only_extra_says_it_does_not_go_on_the_server() -> None:
    client, fakes = setup()
    fakes.ptero.comparison = Comparison(matching=[], different_version=[], missing=[], unknown=[],
                                        extra=[scan_file("/mods/extra.jar", "P4"), scan_file("/mods/other.jar", "P1")],
                                        client_only=["P4"])
    text = client.get(f"/servers/{SERVER_ID}").text
    assert text.count("desactivado: no va en el servidor") == 1 and text.count("el build no lo incluye") == 1


def importar(text: str) -> str:
    start = text.index('id="importar"')
    return text[start:text.index("</section>", start)]


def test_import_is_always_shown_and_says_when_nothing_to_import() -> None:
    client, fakes = setup(OPERATOR)
    # A jar Modrinth does not know and a mod outside the pack: there is something to import.
    section = importar(client.get(f"/servers/{SERVER_ID}").text)
    assert f'hx-post="/servers/{SERVER_ID}/import"' in section and "No hay nada que importar" not in section
    # Everything explained (a second copy of a mod of the pack is extra, not foreign): nothing to import.
    fakes.ptero.comparison = Comparison(matching=[scan_file("/mods/sodium.jar", "P1")], different_version=[],
                                        missing=[], extra=[scan_file("/mods/sodium-2.jar", "P1")], unknown=[])
    section = importar(client.get(f"/servers/{SERVER_ID}").text)
    assert "No hay nada que importar: el modpack ya explica todos los jars del último escaneo" in section
    assert "hx-post" not in section and "disabled" in section
    # No scan: importing scans first (T1), so the button works and says so.
    fakes.ptero.scan, fakes.ptero.comparison = None, None
    section = importar(client.get(f"/servers/{SERVER_ID}").text)
    assert "Importar escanea el servidor primero" in section and f'hx-post="/servers/{SERVER_ID}/import"' in section
    assert "Escanea primero" not in section
    # Only jars too large to read: importing could not bring them either.
    fakes.ptero.scan = scan()
    fakes.ptero.comparison = Comparison(matching=[], different_version=[], missing=[], extra=[],
                                        unknown=[scan_file("/mods/huge.jar", None, "too_large")])
    section = importar(client.get(f"/servers/{SERVER_ID}").text)
    assert "demasiado grande" in section and "Importar los trae" not in section and "hx-post" not in section
    # A server without a modpack: link one, or create it, first.
    fakes.ptero.server, fakes.ptero.comparison = server(bundle_id=None), None
    section = importar(client.get(f"/servers/{SERVER_ID}").text)
    assert "Vincula el servidor a un modpack" in section and "hx-post" not in section
    # An editor sees it too, disabled.
    client, fakes = setup(EDITOR)
    section = importar(client.get(f"/servers/{SERVER_ID}").text)
    assert "requiere operador" in section and "hx-post" not in section


def test_import_result_offers_the_next_step_to_the_pack() -> None:
    client, fakes = setup(OPERATOR)
    fakes.ptero.pending = fakes.ptero.import_result
    text = client.get(f"/servers/{SERVER_ID}").text
    section = text[text.index('id="importar"'):]
    assert "Resolver y crear build" in section and f'href="/modpacks/{BUNDLE_ID}"' in section


def test_drift_pre_is_gone_and_dry_run_jars_fold() -> None:
    client, fakes = setup()
    fakes.ptero.scan = scan("⚠️ Survival no coincide con Pack Uno, build 2:\n- sobra /mods/extra.jar")
    text = client.get(f"/servers/{SERVER_ID}").text
    assert "<pre" not in text and "no coincide con Pack Uno" not in text
    html = row(finished_dry_run(3))
    folded = html[html.index("<details"):]
    assert html.index(f'id="preview-') < html.index("<details")
    assert "Se instalan" in folded and "Se quitan" in folded and "lithium-2.jar" in folded


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
    assert response.status_code == 200 and response.headers["HX-Redirect"] == f"/servers/{SERVER_ID}?vinculado=1"
    assert only(fakes.ptero.calls, "server_link_bundle")[1:] == (str(SERVER_ID), str(BUNDLE_ID), OPERATOR)


def test_unlink_bundle_with_an_empty_choice() -> None:
    client, fakes = setup(OPERATOR)
    response = client.put(f"/servers/{SERVER_ID}/bundle", headers=HTMX, data={"bundle_id": ""})
    assert only(fakes.ptero.calls, "server_link_bundle")[2] is None
    assert response.headers["HX-Redirect"] == f"/servers/{SERVER_ID}?desvinculado=1"


def notices_of(text: str) -> str:
    return text.split('id="notices"')[1].split("<main")[0]


@pytest.mark.parametrize("query,message", [("vinculado=1", "Modpack vinculado"),
                                           ("desvinculado=1", "Servidor desvinculado")])
def test_the_page_after_linking_says_so_and_closes_it_by_itself(query: str, message: str) -> None:
    client, _ = setup(OPERATOR)
    text = client.get(f"/servers/{SERVER_ID}?{query}").text
    corner = notices_of(text)
    assert message in corner and "data-notice-autoclose" in corner


@pytest.mark.parametrize("query", ["", "vinculado=0", "vinculado=%3Cscript%3E", "aviso=Modpack+vinculado"])
def test_the_link_notice_is_a_fixed_text_never_the_query(query: str) -> None:
    client, _ = setup(OPERATOR)
    text = client.get(f"/servers/{SERVER_ID}?{query}").text
    assert "data-notice-autoclose" not in notices_of(text) and "<script>" not in text


def test_importing_queues_the_servers_import_job_and_answers_its_row() -> None:
    # T1: the import is a job that shows its progress; its row polls until it ends.
    client, fakes = setup(OPERATOR)
    response = client.post(f"/servers/{SERVER_ID}/import", headers=HTMX)
    assert response.status_code == 200 and response.headers.get("HX-Trigger") == "job-started"
    assert only(fakes.ptero.calls, "import_request")[1:] == (str(SERVER_ID), OPERATOR)
    assert "Importación del servidor" in response.text and 'hx-trigger="every 2s"' in response.text


def test_the_server_shows_what_its_last_import_left_pending() -> None:
    # S3 (owner, 2026-10-09): the candidates outlive a reload; they live in the last import job.
    client, fakes = setup(OPERATOR)
    fakes.ptero.pending = fakes.ptero.import_result
    page = client.get(f"/servers/{SERVER_ID}").text
    text = page[page.index('id="importar"'):]
    assert "/mods/odd.jar" in text  # the full report (local, too large) is the job's row now
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
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={**LINKED, "confirm": EXACT_NAME, "build": "2"})
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


@pytest.mark.parametrize("typed", ["", "survival", "Surv", "Survival 2", "SURVIVAL"])
def test_b_after_a_requires_exact_name(typed: str) -> None:
    client, fakes = setup(OPERATOR)
    failed, done_a = reverted_with_a()
    fakes.jobs.state = RollbackState(update=failed, allowed=["B"], reverted_with_a=done_a)
    data = {"update_id": str(failed.id), "mode": "B", "confirm": typed}
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
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={**LINKED, "confirm": typed, "build": "2"})
    assert response.status_code == 422 and "Escribe el nombre exacto del servidor" in response.text
    assert fakes.update.calls == []


def test_update_accepts_the_name_with_surrounding_whitespace() -> None:
    client, fakes = setup(OPERATOR)
    fakes.ptero.server = server("Survival")
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={**LINKED, "confirm": "  Survival ", "build": "2"})
    assert response.status_code == 200
    assert only(fakes.update.calls, "enqueue_update")[-1] is False


def test_update_without_the_field_is_the_same_422() -> None:
    client, fakes = setup(OPERATOR)
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={**LINKED, "build": "2"})
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
    first = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={**LINKED, "confirm": EXACT_NAME, "build": "2"})
    assert first.status_code == 200
    second = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={**LINKED, "confirm": EXACT_NAME, "build": "2"})
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
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={**LINKED, "build": "3", "confirm": EXACT_NAME})
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
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={**LINKED, "build": "9", "confirm": EXACT_NAME})
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


def finished_dry_run(build_number: int = 3, changes: bool = True, bundle_id: Optional[str] = str(BUNDLE_ID)) -> Job:
    plan: dict[str, Any] = {
        "build": build_number, "bundle": "Pack Uno", "remove": ["/mods/old.jar"] if changes else [],
        "install": [{"project_id": "P2", "filename": "lithium-2.jar", "url": "u", "sha512": "x", "size": 1}] if changes else []}
    if bundle_id is not None:
        plan["bundle_id"] = bundle_id
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
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX, data={**LINKED, "confirm": EXACT_NAME, "build": "2"})
    assert response.status_code == 403 and fakes.update.calls == []


def test_editor_with_mismatched_bundle_is_refused_before_comparing() -> None:
    client, fakes = setup(EDITOR)
    for bundle_id in ("", str(PydanticObjectId())):
        response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX,
                               data={"bundle_id": bundle_id, "confirm": "otro", "build": "2"})
        assert response.status_code == 403 and fakes.update.calls == []


def test_detail_and_import_survive_a_failing_title_lookup() -> None:
    client, fakes = setup(OPERATOR)

    async def down(project_ids: list[str]) -> dict[str, ModrinthProject]:
        raise RuntimeError("modrinth down")

    import main

    class Down(FakeModrinth):
        projects_filled = staticmethod(down)  # type: ignore[assignment]

    main.container.modrinthService.override(providers.Object(Down()))
    detail = client.get(f"/servers/{SERVER_ID}")
    assert detail.status_code == 200 and "P3" in detail.text
    fakes.ptero.pending = fakes.ptero.import_result
    pending = client.get(f"/servers/{SERVER_ID}")
    assert pending.status_code == 200 and "C1" in pending.text
    # Without the catalog's title, the candidate's own (from the search) names it, not its id.
    assert "Odd Mod" in pending.text


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


# Una actualización real descarta el rollback pendiente (R17): los dos diálogos lo dicen.

def test_direct_update_dialog_warns_when_a_rollback_is_pending() -> None:
    client, fakes = setup(OPERATOR)
    assert PENDING_ROLLBACK not in client.get(f"/servers/{SERVER_ID}").text
    fakes.jobs.state = RollbackState(update=failed_real_update({"swap": {}, "backup": {}}), allowed=["A", "B"])
    text = client.get(f"/servers/{SERVER_ID}").text
    start = text.index('id="update-dialog-direct"')
    assert start < text.index(PENDING_ROLLBACK, start) < text.index('name="confirm"', start)
    assert "el backup se restaura a mano en Pterodactyl" in text


def test_dry_run_dialog_warns_when_a_rollback_is_pending() -> None:
    state = RollbackState(update=failed_real_update({"backup": {}}), allowed=["B"])
    html = row(finished_dry_run(3), server_name="Survival", rollback=state)
    assert PENDING_ROLLBACK in html and html.index(PENDING_ROLLBACK) < html.index('name="confirm"')
    assert PENDING_ROLLBACK not in row(finished_dry_run(3), server_name="Survival", rollback=None)
    assert PENDING_ROLLBACK not in row(finished_dry_run(3), server_name="Survival")


# El build va atado al modpack para el que se planeó.

def test_both_update_forms_send_the_bundle_they_were_planned_for() -> None:
    client, _ = setup(OPERATOR)
    text = client.get(f"/servers/{SERVER_ID}").text
    start = text.index('id="update-dialog-direct"')
    assert text.index(f'name="bundle_id" value="{BUNDLE_ID}"', start) > start
    html = row(finished_dry_run(3, bundle_id="0123456789abcdef01234567"), server_name="Survival")
    assert 'name="bundle_id" value="0123456789abcdef01234567"' in html


def test_a_dry_run_without_its_bundle_does_not_offer_the_real_run() -> None:
    """A dry run from before the plan carried its bundle cannot be tied to one: simulate again."""
    html = row(finished_dry_run(3, bundle_id=None), server_name="Survival")
    assert 'name="confirm"' not in html and "vuelve a simular" in html


@pytest.mark.parametrize("sent", [{"bundle_id": "0123456789abcdef01234567"}, {"bundle_id": ""}, {}])
def test_an_update_for_another_bundle_is_refused_before_the_service(sent: dict[str, str]) -> None:
    client, fakes = setup(OPERATOR)
    response = client.post(f"/servers/{SERVER_ID}/update", headers=HTMX,
                           data={**sent, "build": "2", "confirm": EXACT_NAME})
    assert response.status_code == 409
    assert "El servidor cambió de modpack; vuelve a cargar la página" in response.text
    assert fakes.update.calls == []


# Errores del rollback que dicen qué pasó.

def test_after_done_a_mode_a_gets_the_service_conflict_not_the_name_error() -> None:
    client, fakes = setup(OPERATOR)
    failed, done_a = reverted_with_a()
    fakes.jobs.state = RollbackState(update=failed, allowed=["B"], reverted_with_a=done_a)
    fakes.update.errors = [HTTP_409_CONFLICT("Ya se revirtió con A: solo queda B")]
    response = client.post(f"/servers/{SERVER_ID}/rollback", headers=HTMX,
                           data={"update_id": str(failed.id), "mode": "A"})
    assert response.status_code == 409 and "solo queda B" in response.text
    assert only(fakes.update.calls, "enqueue_rollback")[1:] == (str(SERVER_ID), str(failed.id), "A", OPERATOR)


def test_a_stale_page_posting_b_after_a_is_told_to_reload() -> None:
    client, fakes = setup(OPERATOR)
    failed, done_a = reverted_with_a()
    fakes.jobs.state = RollbackState(update=failed, allowed=["B"], reverted_with_a=done_a)
    response = client.post(f"/servers/{SERVER_ID}/rollback", headers=HTMX,
                           data={"update_id": str(failed.id), "mode": "B"})
    assert response.status_code == 422
    assert ("La reversión con A ya terminó; vuelve a cargar la página para confirmar B con el nombre del servidor."
            in response.text)
    assert fakes.update.calls == []


# Un intento de rollback fallido se dice también antes de un A exitoso.

@pytest.mark.parametrize("mode", ["A", "B"])
def test_the_newest_failed_rollback_attempt_is_said(mode: str) -> None:
    client, fakes = setup(OPERATOR)
    failed = failed_real_update({"swap": {}, "backup": {}})
    attempt = job("server_rollback", "failed", False, {"server_id": str(SERVER_ID), "update_id": str(failed.id), "mode": mode},
                  error="panel said no")
    fakes.jobs.state = RollbackState(update=failed, allowed=["A", "B"], last_failed=attempt)
    text = client.get(f"/servers/{SERVER_ID}").text
    assert f"El último intento de {mode} falló el" in text and "panel said no" in text
    assert text.index(f"El último intento de {mode} falló") < text.index('name="mode" value="A"')
    fakes.jobs.state = RollbackState(update=failed, allowed=["A", "B"])
    assert "El último intento de" not in client.get(f"/servers/{SERVER_ID}").text


# La vista previa de lo que cambia (N4).

def test_the_direct_dialog_loads_its_preview_lazily_and_the_page_never_waits_for_it() -> None:
    client, fakes = setup(OPERATOR)
    text = client.get(f"/servers/{SERVER_ID}").text
    assert fakes.modpack.calls == []  # the page does not build the preview
    assert 'id="update-preview"' in text and "Cargando cambios" in text and 'id="changes"' not in text
    assert f'hx-get="/servers/{SERVER_ID}/update/preview"' in text and 'hx-trigger="change"' in text
    assert 'hx-target="#update-preview"' in text and 'hx-include="this"' in text


def test_the_dialog_preview_is_requested_only_when_the_dialog_opens() -> None:
    """A closed <dialog> is not displayed: `intersect once` fires when it opens, never on the page
    load (a `load` trigger cost every visit up to 10 s of Modrinth and swapped notices in)."""
    client, fakes = setup(OPERATOR)
    text = client.get(f"/servers/{SERVER_ID}").text
    preview = re.search(r'<div id="update-preview"[^>]*>', text)
    assert preview is not None
    tag = preview.group(0)
    assert f'hx-get="/servers/{SERVER_ID}/update/preview?build=2"' in tag
    assert 'hx-trigger="intersect once"' in tag
    assert 'hx-trigger="load"' not in text.split('id="update-dialog-direct"', 1)[1].split("</dialog>", 1)[0]


def test_the_lazy_load_asks_for_the_default_build() -> None:
    client, fakes = setup(OPERATOR)
    fakes.modpack.recommended = 4
    text = client.get(f"/servers/{SERVER_ID}").text
    assert f'hx-get="/servers/{SERVER_ID}/update/preview?build=4"' in text


def test_changing_the_build_asks_for_its_preview_and_returns_the_fragment() -> None:
    client, fakes = setup(EDITOR)  # read: an editor loads it too
    response = client.get(f"/servers/{SERVER_ID}/update/preview", params={"build": 3}, headers=HTMX)
    assert response.status_code == 200
    assert ("server_change_preview", str(SERVER_ID), 3) in fakes.modpack.calls
    assert 'id="changes"' in response.text and "<html" not in response.text and "Arregla un crash" in response.text


def test_the_dialog_preview_warns_of_the_jars_that_would_stop_the_update() -> None:
    # F28 (d-7985ee-83e764): from the last scan, with its age and the fix; the button stays.
    from src.library.scan import Unexplained

    client, fakes = setup(OPERATOR)
    fakes.modpack.preview = change_preview().model_copy(update={
        "unexplained": Unexplained(in_pack=["/mods/local.jar"], foreign=["/mods/foo-1.2.jar", "/mods/bar.jar"]),
        "scanned_at": datetime.now(timezone.utc) - timedelta(hours=3)})
    text = client.get(f"/servers/{SERVER_ID}/update/preview", params={"build": 3}, headers=HTMX).text
    assert "La actualización se detendrá antes de tocar el servidor" in text
    assert "el escaneo de hace 3 h tiene 3 jars que el build no explica" in text
    assert "/mods/foo-1.2.jar" in text and "/mods/bar.jar" in text and "/mods/local.jar" in text
    assert "Importa el servidor para traer" in text and "crea un build que los incluya" in text
    fakes.modpack.preview = change_preview()
    assert "se detendrá" not in client.get(f"/servers/{SERVER_ID}/update/preview", params={"build": 3}, headers=HTMX).text


def test_the_build_of_the_preview_is_a_positive_number() -> None:
    client, fakes = setup(OPERATOR)
    assert client.get(f"/servers/{SERVER_ID}/update/preview", params={"build": 0}, headers=HTMX).status_code == 422
    assert client.get(f"/servers/{SERVER_ID}/update/preview", headers=HTMX).status_code == 422
    assert fakes.modpack.calls == []


@pytest.mark.parametrize("error", [HTTP_409_CONFLICT("Escanea el servidor antes de comparar"),
                                   HTTP_404_NOT_FOUND("Escanea el servidor antes de comparar")])
def test_the_preview_without_a_scan_is_an_inline_note_not_a_notice(error: Exception) -> None:
    """No scan (409) or a build gone (404): the dialog says it in place, with a 200. A notice in
    #notices would come from loading a preview nobody asked about."""
    client, fakes = setup(OPERATOR)
    fakes.modpack.preview_error = error
    response = client.get(f"/servers/{SERVER_ID}/update/preview", params={"build": 2}, headers=HTMX)
    assert response.status_code == 200
    assert "Escanea el servidor antes de comparar" in response.text and "data-notice" not in response.text
    assert "<html" not in response.text


def test_the_preview_with_modrinth_down_says_the_changelog_is_not_available() -> None:
    client, fakes = setup(OPERATOR)
    fakes.modpack.preview = change_preview(failed=True)
    response = client.get(f"/servers/{SERVER_ID}/update/preview", params={"build": 2}, headers=HTMX)
    assert response.status_code == 200
    assert "changelog no disponible" in response.text and "Sodium" in response.text


# Nuevo modpack para un servidor sin modpack (diseño §2): deducido del escaneo; el operador crea,
# vincula e importa de una vez, y el editor solo crea.

NEW_MODPACK = {"name": "Creativo", "version": "1.21.1", "loader": "fabric"}


def text_of(html: str) -> str:
    """What the page shows as text: tags removed and whitespace collapsed."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def unlinked(fakes: Fakes, deduced: Optional[Deduction] = None, scanned: bool = True) -> None:
    fakes.ptero.server = server(bundle_id=None)
    fakes.ptero.comparison = None
    fakes.ptero.scan = scan() if scanned else None
    fakes.ptero.deduced = deduced


def test_form_prefills_the_deduction_and_explains_it() -> None:
    client, fakes = setup(OPERATOR)
    unlinked(fakes, Deduction("fabric", "1.21.1", 9, {"fabric": 8, "quilt": 1}, {"1.21.1": 9, "1.21": 2}))
    response = client.get(f"/servers/{SERVER_ID}")
    assert response.status_code == 200
    form = response.text.split('id="new-modpack"')[1]
    assert "Nuevo modpack para este servidor" in response.text
    assert f'hx-post="/servers/{SERVER_ID}/modpack"' in form
    assert re.search(r'name="name"[^>]*value="Survival"', form)
    assert re.search(r'name="version"[^>]*value="1.21.1"', form)
    assert re.search(r'<option value="fabric" selected>', form)
    assert "8 de 9" in text_of(form) and "9 de 9" in text_of(form)
    assert re.search(r'name="import_mods"[^>]*checked', form)


def test_a_tie_prefills_nothing_and_asks_to_choose() -> None:
    client, fakes = setup(OPERATOR)
    unlinked(fakes, Deduction(None, None, 2, {"fabric": 1, "forge": 1}, {"1.21.1": 1, "1.20.1": 1}))
    form = client.get(f"/servers/{SERVER_ID}").text.split('id="new-modpack"')[1]
    assert re.search(r'name="version"[^>]*value=""', form)
    assert "<option value=\"fabric\" selected>" not in form
    assert "elige" in text_of(form)


def test_without_scan_offers_scanning_to_deduce() -> None:
    client, fakes = setup(OPERATOR)
    unlinked(fakes, scanned=False)
    form = client.get(f"/servers/{SERVER_ID}").text.split('id="new-modpack"')[1]
    assert "Escanear para deducir" in form
    assert re.search(rf'hx-post="/servers/{SERVER_ID}/scan"[^>]*hx-target="#server-job"[^>]*>Escanear para deducir<', form)
    assert re.search(r'name="import_mods"[^>]*disabled', form)


def test_a_linked_server_offers_no_new_modpack() -> None:
    client, _ = setup(OPERATOR)
    assert "Nuevo modpack para este servidor" not in client.get(f"/servers/{SERVER_ID}").text


def test_an_editor_form_posts_to_the_draft_and_says_linking_needs_an_operator() -> None:
    client, fakes = setup(EDITOR)
    unlinked(fakes, Deduction("fabric", "1.21.1", 1, {"fabric": 1}, {"1.21.1": 1}))
    form = client.get(f"/servers/{SERVER_ID}").text.split('id="new-modpack"')[1]
    assert f'hx-post="/servers/{SERVER_ID}/modpack/borrador"' in form
    assert re.search(r'name="import_mods"[^>]*disabled', form) and "requiere operador" in text_of(form)


def test_an_operator_form_posts_to_the_operator_route() -> None:
    client, fakes = setup(OPERATOR)
    unlinked(fakes)
    form = client.get(f"/servers/{SERVER_ID}").text.split('id="new-modpack"')[1]
    assert f'hx-post="/servers/{SERVER_ID}/modpack"' in form and "/modpack/borrador" not in form


def test_an_editor_posting_to_the_operator_route_is_refused_and_nothing_is_created() -> None:
    client, fakes = setup(EDITOR)
    unlinked(fakes)
    response = client.post(f"/servers/{SERVER_ID}/modpack", headers=HTMX, data={**NEW_MODPACK, "import_mods": "1"})
    assert response.status_code == 403
    assert fakes.ptero.calls == [] and fakes.modpack.calls == []


@pytest.mark.network
def test_editor_post_only_creates_the_pack_even_if_link_is_forged(mongo: MongoRunner) -> None:
    client, fakes = setup(EDITOR)
    unlinked(fakes)

    async def scenario() -> Any:  # init_beanie: crear el Document exige la base inicializada
        return client.post(f"/servers/{SERVER_ID}/modpack/borrador", headers=HTMX,
                           data={**NEW_MODPACK, "import_mods": "1", "link": "1", "import": "1"})

    response = mongo(scenario)
    assert response.status_code == 200
    [created] = [c for c in fakes.modpack.calls if c[0] == "bundle_create"]
    assert (created[1].name, created[1].version, created[1].loader, created[2]) == \
        ("Creativo", "1.21.1", MinecraftLoaders.fabric, EDITOR)
    assert fakes.ptero.calls == []
    assert "Pide a un operador que lo vincule" in response.text
    assert f'href="/modpacks/{NEW_BUNDLE_ID}"' in response.text


def test_an_editor_cannot_create_one_for_a_linked_server() -> None:
    client, fakes = setup(EDITOR)
    response = client.post(f"/servers/{SERVER_ID}/modpack/borrador", headers=HTMX, data=NEW_MODPACK)
    assert response.status_code == 409
    assert fakes.modpack.calls == []


def test_operator_post_shows_the_import_job_and_next_step() -> None:
    # T1: the new pack's import is the server's import job; its row shows how far it is.
    client, fakes = setup(OPERATOR)
    unlinked(fakes)
    fakes.ptero.for_server = BundleForServer(bundle=new_bundle(), import_error=None, imported=None, import_job=import_job())
    response = client.post(f"/servers/{SERVER_ID}/modpack", headers=HTMX, data={**NEW_MODPACK, "import_mods": "1"})
    assert response.status_code == 200
    assert only(fakes.ptero.calls, "bundle_for_server")[1:] == \
        (str(SERVER_ID), "Creativo", "1.21.1", MinecraftLoaders.fabric, True, OPERATOR)
    assert fakes.modpack.calls == []
    assert "Importación del servidor" in response.text and 'hx-trigger="every 2s"' in response.text
    assert "Resolver y crear build" in response.text and f'href="/modpacks/{NEW_BUNDLE_ID}"' in response.text
    assert 'id="notices" hx-swap-oob="beforeend"' in response.text


def test_operator_post_without_the_checkbox_does_not_import() -> None:
    client, fakes = setup(OPERATOR)
    unlinked(fakes)
    client.post(f"/servers/{SERVER_ID}/modpack", headers=HTMX, data=NEW_MODPACK)
    assert only(fakes.ptero.calls, "bundle_for_server")[5] is False


def test_a_failed_import_says_so_and_offers_importing_again() -> None:
    client, fakes = setup(OPERATOR)
    unlinked(fakes)
    fakes.ptero.for_server = BundleForServer(bundle=new_bundle(), imported=None,
                                             import_error="El modpack quedó creado y vinculado, pero la importación falló.")
    response = client.post(f"/servers/{SERVER_ID}/modpack", headers=HTMX, data={**NEW_MODPACK, "import_mods": "1"})
    assert response.status_code == 200
    assert "la importación falló" in response.text
    assert f'hx-post="/servers/{SERVER_ID}/import"' in response.text
    corner = response.text.split('id="notices" hx-swap-oob="beforeend"')[1]
    assert 'data-notice-tone="success"' not in corner and 'data-notice-tone="warning"' in corner
    assert "la importación falló" in corner


def link_form(text: str) -> str:
    tag = re.search(r'<form [^>]*id="link-bundle-form"[^>]*>', text)
    assert tag, "no #link-bundle-form"
    return text[tag.start():text.index("</form>", tag.start())]


def test_creating_and_linking_updates_the_link_selector_out_of_band() -> None:
    """The answer replaces only the form it came from: the selector above it would still say "Sin
    modpack" (and saving it would unlink), so it comes redrawn out of band, with the new pack."""
    client, fakes = setup(OPERATOR)
    unlinked(fakes)

    async def with_the_new_one() -> list[ModpackBundle]:
        return [bundle(), new_bundle()]

    fakes.modpack.bundle_find = with_the_new_one  # type: ignore[method-assign]
    text = client.post(f"/servers/{SERVER_ID}/modpack", headers=HTMX, data=NEW_MODPACK).text
    form = link_form(text)
    box = text[text.index('<div id="modpack-link"'):]
    assert 'hx-swap-oob="true"' in box.split(">")[0] and "Sin modpack." not in text_of(box.split("<form")[0])
    assert re.search(rf'<option value="{NEW_BUNDLE_ID}" selected>', form)
    assert not re.search(r'<option value="" selected>', form)
    assert 'id="server-main"' not in text  # the import candidates stay on the page


@pytest.mark.network
def test_an_editor_creating_one_sees_it_in_the_selector_unselected(mongo: MongoRunner) -> None:
    client, fakes = setup(EDITOR)
    unlinked(fakes)

    async def with_the_new_one() -> list[ModpackBundle]:
        return [bundle(), new_bundle()]

    fakes.modpack.bundle_find = with_the_new_one  # type: ignore[method-assign]

    async def scenario() -> Any:  # init_beanie: crear el Document exige la base inicializada
        return client.post(f"/servers/{SERVER_ID}/modpack/borrador", headers=HTMX, data=NEW_MODPACK)

    form = link_form(mongo(scenario).text)
    assert re.search(rf'<option value="{NEW_BUNDLE_ID}">', form) and re.search(r'<option value="" selected>', form)


def test_the_detail_keeps_the_link_form() -> None:
    client, _ = setup(OPERATOR)
    form = link_form(client.get(f"/servers/{SERVER_ID}").text)
    assert f'hx-put="/servers/{SERVER_ID}/bundle"' in form and "hx-swap-oob" not in form
    assert re.search(rf'<option value="{BUNDLE_ID}" selected>', form)


def section_order(text: str) -> list[str]:
    return [m for m in re.findall(r'<section class="space-y-2" id="([a-z]+)"', text)
            if m in ("actualizar", "modpack", "escaneo", "importar")]


def test_without_a_modpack_the_modpack_section_comes_first() -> None:
    client, fakes = setup(OPERATOR)
    unlinked(fakes)
    assert section_order(client.get(f"/servers/{SERVER_ID}").text) == ["modpack", "actualizar", "escaneo", "importar"]


def test_with_a_modpack_actualizar_comes_first() -> None:
    client, _ = setup(OPERATOR)
    assert section_order(client.get(f"/servers/{SERVER_ID}").text) == ["actualizar", "modpack", "escaneo", "importar"]


def test_the_in_sync_status_does_not_close_itself() -> None:
    client, fakes = setup()
    fakes.ptero.comparison = Comparison(matching=[scan_file("/mods/sodium.jar", "P1")], different_version=[],
                                        missing=[], extra=[], unknown=[])
    compare = client.get(f"/servers/{SERVER_ID}").text.split('id="compare"')[1]
    assert "El servidor coincide con el build" in compare and "data-notice-autoclose" not in compare


def test_a_new_modpack_needs_a_name_and_a_known_loader() -> None:
    client, fakes = setup(OPERATOR)
    unlinked(fakes)
    for route in ("modpack", "modpack/borrador"):
        for data in ({**NEW_MODPACK, "name": "   "}, {**NEW_MODPACK, "loader": "optifine"},
                     {**NEW_MODPACK, "name": "x" * 121}):
            assert client.post(f"/servers/{SERVER_ID}/{route}", headers=HTMX, data=data).status_code == 422
    assert fakes.ptero.calls == [] and fakes.modpack.calls == []


# Every mod name opens its card (spec §2, "Macro ui.mod_link"), and a render fills the catalog once.

def mod_link(project_id: str) -> str:
    return f'<a href="/mods/{project_id}" hx-get="/mods/{project_id}/panel"'


def test_the_comparison_names_open_their_cards_with_one_fill() -> None:
    client, fakes = setup()
    text = client.get(f"/servers/{SERVER_ID}").text
    compare = text[text.index('id="compare"'):]
    for project_id in ("P1", "P2", "P3", "P4"):
        assert mod_link(project_id) in compare
    assert fakes.modrinth.filled == [["P1", "P2", "P3", "P4"]]


def test_the_import_result_names_and_candidates_open_their_cards_with_one_fill() -> None:
    client, fakes = setup(OPERATOR)
    fakes.ptero.pending = fakes.ptero.import_result
    text = client.get(f"/servers/{SERVER_ID}").text
    assert mod_link("C1") in text  # the added ones (P5) are in the job's row now (decision 3a)
    assert 'name="project_id" value="C1"' in text  # confirming the candidate still sends its id
    [filled] = fakes.modrinth.filled  # the page's one fill names them with the comparison's
    assert "C1" in filled


def test_the_update_preview_names_open_their_cards_with_one_fill() -> None:
    client, fakes = setup()
    text = client.get(f"/servers/{SERVER_ID}/update/preview", params={"build": 3}, headers=HTMX).text
    for project_id in ("P1", "P2", "P3"):
        assert mod_link(project_id) in text
    assert fakes.modrinth.filled == [["P1", "P2", "P3"]]


def test_the_confirmed_candidate_opens_its_card_by_title_with_one_fill() -> None:
    client, fakes = setup(OPERATOR)
    response = client.post(f"/servers/{SERVER_ID}/import/confirm", headers=HTMX,
                           data={"path": "/mods/odd.jar", "project_id": "C1"})
    assert response.status_code == 200
    assert mod_link("C1") + ' hx-target="#mod-drawer-body"' in response.text
    assert ">Odd Mod Project</a>" in response.text and ">C1<" not in response.text
    assert fakes.modrinth.filled == [["C1"]]



def import_job() -> Job:
    from datetime import datetime, timezone
    return Job.model_construct(
        id=PydanticObjectId(), kind="server_import", key=f"server:{SERVER_ID}", status="queued", active=True,
        payload={"server_id": str(SERVER_ID)}, created_at=datetime.now(timezone.utc), attempts=0, max_attempts=3,
        checkpoint={}, step=None, error=None, requested_by=OPERATOR.ref(), progress=None, waiting_until=None,
        updated_at=None, owner=None, lease_until=None, available_at=None, finished_at=None)


def test_v1_import_answers_202_with_the_servers_import_job() -> None:
    # U5/S7: /v1 answers the job, as update, rollback and scan do; asking again answers the same job.
    from fastapi import FastAPI
    from src.dependences import get_current_actor
    from src.routers.v1 import include_routers_v1

    _, fakes = setup(OPERATOR)
    app = FastAPI()
    include_routers_v1(app)
    app.dependency_overrides[get_current_actor] = lambda: OPERATOR
    response = TestClient(app, base_url="https://testserver").post(f"/v1/server/{SERVER_ID}/import")
    assert response.status_code == 202 and response.json()["kind"] == "server_import"
    assert only(fakes.ptero.calls, "import_request")[1:] == (str(SERVER_ID), OPERATOR)


def test_after_a_reload_the_server_shows_only_what_waits_for_a_decision() -> None:
    # Owner (2026-10-12, decision 3a): the last import's full report belongs to its job; the page
    # keeps only the candidates, and nothing when none is left.
    client, fakes = setup(OPERATOR)
    fakes.ptero.pending = fakes.ptero.import_result
    section = importar(client.get(f"/servers/{SERVER_ID}").text)
    assert "Pendientes de tu decisión" in section and "Mods agregados" not in section
    assert "Archivos locales registrados" not in section
    fakes.ptero.pending = fakes.ptero.import_result.model_copy(update={"candidates": []})
    section = importar(client.get(f"/servers/{SERVER_ID}").text)
    assert "Pendientes de tu decisión" not in section and "Mods agregados" not in section


def test_the_refresh_button_is_there_even_when_the_panel_never_answered() -> None:
    # Owner (2026-10-12, decision 3b).
    client, fakes = setup()

    async def down() -> list[PterodactylServer]:
        raise RuntimeError("panel down")

    fakes.ptero.server_external = down  # type: ignore[method-assign]
    text = client.get("/servers").text
    section = text[text.index('id="altas"'):]
    assert "No se pudo consultar el panel" in section and 'hx-post="/servers/lista"' in section


def test_a_candidate_modrinth_does_not_know_by_id_keeps_its_own_title() -> None:
    # Visual sweep (2026-10-12): an id the catalog lacks maps to itself among the titles, and the
    # candidate read «iris (iris)» instead of the search's «Iris Shaders».
    client, fakes = setup(OPERATOR)

    async def none_known(project_ids: list[str]) -> dict[str, ModrinthProject]:
        return {}

    import main

    class Unknown(FakeModrinth):
        projects_filled = staticmethod(none_known)  # type: ignore[assignment]

    main.container.modrinthService.override(providers.Object(Unknown()))
    fakes.ptero.pending = fakes.ptero.import_result
    text = importar(client.get(f"/servers/{SERVER_ID}").text)
    assert ">Odd Mod<" in text


def test_a_long_server_name_prefills_the_new_modpack_within_its_limit() -> None:
    # Roadmap (round 1 review): a server name over 120 characters filled the field past its
    # maxlength, and sending it gave a 422 with no explanation.
    import re

    client, fakes = setup(OPERATOR)
    unlinked(fakes)
    fakes.ptero.server = server(name="N" * 200, bundle_id=None)
    text = client.get(f"/servers/{SERVER_ID}").text
    value = re.search(r'id="new-modpack-name"[^>]*value="([^"]*)"', text)
    assert value is not None and len(value.group(1)) == 120


# Alta guiada (B1, d-7985ee-20935c).


def onboarding(text: str) -> str:
    start = text.index("data-onboarding")
    return text[start:text.index("</section>", start)]


def test_the_server_page_lists_what_is_left_to_set_up_with_each_action() -> None:
    client, fakes = setup()
    fakes.modpack.saved = (SimpleNamespace(lock=SimpleNamespace(undecided=["P2"], problems=[])), False)
    part = onboarding(client.get(f"/servers/{SERVER_ID}").text)
    shown = " ".join(visible(part).split())
    assert "Para terminar el alta (3 de 5)" in shown
    assert "Escaneado" in shown and "1 mods identificados" in shown and "Pack Uno" in shown
    assert "1 por decidir o con problemas" in shown
    assert f'href="/modpacks/{BUNDLE_ID}?vista=decidir"' in part
    assert "Simular muestra qué cambia" in shown and "Un build congela" not in shown  # a done step says no why


def test_a_set_up_server_folds_its_list_into_one_line() -> None:
    client, fakes = setup()
    fakes.modpack.saved = (SimpleNamespace(lock=SimpleNamespace(undecided=[], problems=[])), False)
    fakes.jobs.simulated = 5
    part = onboarding(client.get(f"/servers/{SERVER_ID}").text)
    assert "Alta completa" in visible(part) and "<details" in part and " open" not in part[:part.index(">")]


def test_a_server_without_scan_or_pack_starts_the_list() -> None:
    client, fakes = setup()
    fakes.ptero.server = server(bundle_id=None)
    fakes.ptero.scan = None
    fakes.ptero.comparison = None
    part = onboarding(client.get(f"/servers/{SERVER_ID}").text)
    assert "Para terminar el alta (0 de 5)" in " ".join(visible(part).split())
    assert f'href="/servers/{SERVER_ID}#escaneo"' in part and f'href="/servers/{SERVER_ID}#modpack"' in part
    assert "después de" in visible(part)


def test_a_new_pack_from_a_server_suggests_its_version_among_modrinth_s() -> None:
    client, fakes = setup()
    fakes.ptero.server = server(bundle_id=None)
    fakes.modrinth.game_versions = ["26.3", "1.21.11"]
    fakes.ptero.deduced = Deduction(loader="fabric", game_version="1.21.11", identified=3, loader_votes={"fabric": 3},
                                    version_votes={"1.21.11": 3})
    text = client.get(f"/servers/{SERVER_ID}").text
    field = text[text.index('id="new-modpack-version"') - 200:]
    field = field[field.index("<select"):field.index("</select>")]
    assert '<option value="1.21.11" selected>1.21.11</option>' in field and 'value="26.3"' in field


def test_every_table_cell_carries_its_header_for_the_phone() -> None:
    # B4 (d-7985ee-937ca8): the servers, the panel's missing ones and the comparison.
    client, _ = setup()
    assert assert_cells_labelled(client.get("/servers").text) >= 3 + 3
    assert assert_cells_labelled(client.get(f"/servers/{SERVER_ID}").text) >= 4


def test_a_set_up_fact_that_fails_to_read_leaves_its_step_undone_never_a_500() -> None:
    """Spec (Errores) and the final review of batch B: reading the pack's resolution can fail (Modrinth
    behind the floors, a missing build); the page shows, with that step not done."""
    client, fakes = setup()
    fakes.modpack.resolution_error = UpstreamError("modrinth", 502)
    response = client.get(f"/servers/{SERVER_ID}")
    assert response.status_code == 200
    assert "Pack resuelto" in visible(onboarding(response.text))
