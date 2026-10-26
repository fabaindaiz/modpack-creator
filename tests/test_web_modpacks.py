"""Las páginas de modpacks del panel (etapa 2): lista, detalle, búsqueda, lock, builds y reemplazos.

Los servicios se sustituyen con dobles en el contenedor, como en `tests/test_web_base.py`. Cada
doble anota qué recibió, para afirmar que toda mutación lleva el `actor` de la sesión.
"""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
from aiohttp import ClientConnectionError
from beanie import PydanticObjectId
from dependency_injector import providers
from fastapi.testclient import TestClient

from src.exceptions import CatalogPending, BuildBlocked, DowngradeRefused, ViaChangeRefused, HTTP_404_NOT_FOUND, HTTP_409_CONFLICT, UpstreamError
from src.library.resolution import Alternative, Lock, LockEntry, Problem, Suggestion
from src.library.scan import LocalFile
from src.model.modpack import (AvailableUpdate, ModpackBuild, ModpackBundle, ModpackReplacement, ModpackResolution,
                               PackProject, ProjectOrigin, UpdateCheck)
from src.model.modrinth import ModrinthProject
from src.model.pterodactyl import PterodactylServer
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from src.services.modpack import ModList, ModpackService, ModRow, PinDowngrade, ProjectRemoval, VersionOption
from src.services.modrinth import ModrinthService
from tests._actors import EDITOR, OPERATOR, session_for
from tests._session import session_cookie
from tests._tables import assert_cells_labelled
from tests.conftest import MongoRunner

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
BUNDLE_ID = PydanticObjectId()
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
HTMX = {"HX-Request": "true", **SAME_ORIGIN}


def bundle(projects: Optional[list[PackProject]] = None, decisions: Optional[dict[str, bool]] = None) -> ModpackBundle:
    # model_construct: un Document de Beanie no se instancia sin init_beanie.
    return ModpackBundle.model_construct(
        id=BUNDLE_ID, name="Pack Uno", description="Un pack", version="1.21.1", loader=MinecraftLoaders.fabric,
        projects=projects if projects is not None else [PackProject(project_id="P1", origin=ProjectOrigin.pack),
                                                       PackProject(project_id="P2", origin=ProjectOrigin.admin)],
        server_decisions=decisions if decisions is not None else {"OLD": True}, latest_build=2, recommended_build=1,
        channels=["release"], pins={"P1": "ver-p1"}, channel_exceptions={}, modrinth_checked_at=None,
        local_files=[LocalFile(filename="custom.jar", sha512="ab12cd34ef56" + "0" * 100, sha1="x", size=1536)])


def build(number: int, **fields: Any) -> ModpackBuild:
    return ModpackBuild.model_construct(id=PydanticObjectId(), bundle_id=BUNDLE_ID, number=number, created_at=T0,
                                        lock=lock(), loader_version="0.16", **{"test": False, "name": None, "starred": False, **fields})


def entry(project_id: str, server: str = "include", project_type: str = "mod") -> LockEntry:
    return LockEntry(project_id=project_id, origin="pack", version_id="v-" + project_id, version_number="1.0",
                     file=None, environment="client_and_server", server=server,  # type: ignore[arg-type]
                     reason="en el pack", requested_by=[], project_type=project_type)


def lock(*entries: LockEntry, problems: Optional[list[Problem]] = None,
         suggestions: Optional[list[Suggestion]] = None) -> Lock:
    return Lock(entries=list(entries), problems=problems or [], suggestions=suggestions or [], needs_projects=set(),
                needs_types=set(), needs_versions=set())


def resolution(saved: Lock) -> ModpackResolution:
    return ModpackResolution.model_construct(id=PydanticObjectId(), bundle_id=BUNDLE_ID, resolved_at=T0,
                                             request_digest="d", lock=saved)


def row(project_id: str, title: str, origin: Optional[str] = "pack", **changes: Any) -> ModRow:
    fields: dict[str, Any] = dict(
        project_id=project_id, title=title, icon_url=None, description="", downloads=0, categories=[], loaders=[],
        game_versions=[], supports_loader=None, supports_version=None, origin=origin, required_by=[], side=None,
        build_version=None, newer_version=None, pinned=False)
    return ModRow(**{**fields, **changes})


def card(text: str, project_id: str) -> str:
    """The HTML of one mod's row and card, up to the next mod's."""
    return text.split(f'data-project="{project_id}"')[1].split("data-project=")[0]


def visible(text: str) -> str:
    """What the page shows as text: the markup without its tags (and their attributes)."""
    return re.sub(r"<[^>]+>", " ", text)


def project(project_id: str, title: str) -> ModrinthProject:
    return ModrinthProject.model_construct(project_id=project_id, slug=project_id.lower(), title=title,
                                           description="", icon_url=None)


class FakeModpack:
    def __init__(self) -> None:
        self.bundle = bundle()
        self.builds = [build(2), build(1)]
        # What build_approve answers (O8 B1); None approves `number` with nothing newer.
        self.approved: Any = None
        # The builds installed on the pack's servers (O8 B3: never folded).
        self.installed: set[int] = set()
        self.rules = [ModpackReplacement.model_construct(project_id="OLDMOD", replacement="NEWMOD"),
                      ModpackReplacement.model_construct(project_id="DROPPED", replacement=None)]
        self.lock = lock(entry("P1"), entry("P2", "admin"))
        self.calls: list[tuple[Any, ...]] = []
        self.error: Optional[Exception] = None
        # The pack's active jobs, for its banner (spec 2026-10-09-economia-de-requests).
        self.jobs: list[Any] = []
        self.build_blocked_now = False
        self.failed_job: Any = None
        # The job build_request answers.
        self.queued: Any = None
        # What mod_list answers: `rows` as given, or one row per project of the bundle (with the
        # side and pin it has) and per entry of the saved resolution that is not in it.
        self.titles: dict[str, str] = {"P1": "Sodium", "P2": "Lithium"}
        self.rows: Optional[list[ModRow]] = None
        self.cards: dict[str, ModRow] = {}
        self.saved: Optional[ModpackResolution] = None
        self.stale = False
        self.checks_seen: list[Optional[UpdateCheck]] = []
        # The bundle each mod_list was handed (the page's own, so the list does not read it again).
        self.bundles_given: list[Optional[ModpackBundle]] = []
        # What project_dependents answers (a read, not recorded): who needs a mod being removed.
        self.dependents: Optional[list[str]] = []
        # What bridge_idle answers: the bridge mods left when no mod runs through a bridge.
        self.idle: list[str] = []
        # What mod_list says can go back to the bridge; None: every mod moved to native.
        self.via_back: Optional[set[str]] = None
        # A pin pin_set refuses as a step back unless confirmed (F27), and each call's confirmation.
        self.downgrade: Optional[PinDowngrade] = None
        self.confirmed: list[bool] = []
        # The server each origin choice named (one «servidor» origin per server, 2026-10-22).
        self.origin_servers: list[Optional[str]] = []
        self.versions = [VersionOption("ver-new", "0.8.14", "release", datetime(2026, 10, 1, tzinfo=timezone.utc)),
                         VersionOption("ver-beta", "0.9.0-beta.1", "beta", datetime(2026, 10, 2, tzinfo=timezone.utc))]
        self.versions_error: Optional[Exception] = None
        self.blocked: Optional[BuildBlocked] = None

    def _record(self, *call: Any) -> None:
        self.calls.append(call)
        if self.error is not None:
            raise self.error

    async def bundle_find(self) -> list[ModpackBundle]:
        return [self.bundle]

    async def bundle_create(self, bundle: ModpackBundle, actor: Actor) -> ModpackBundle:
        self._record("bundle_create", bundle, actor)
        return self.bundle

    async def bundle_get(self, bundle_id: str) -> ModpackBundle:
        return self.bundle

    async def decision_set(self, bundle_id: str, project_id: str, server: bool, actor: Actor) -> ModpackBundle:
        self._record("decision_set", bundle_id, project_id, server, actor)
        return self.bundle

    async def decisions_set(self, bundle_id: str, decisions: dict[str, bool], actor: Actor) -> ModpackBundle:
        self._record("decisions_set", bundle_id, decisions, actor)
        return self.bundle

    async def bundle_update(self, bundle_id: str, patch: Any, actor: Actor, confirm_downgrade: bool = False) -> ModpackBundle:
        self._record("bundle_update", bundle_id, patch, actor)
        # Like the service: the fields the patch sets.
        if hasattr(patch, "model_dump"):
            self.bundle = self.bundle.model_copy(update=patch.model_dump(exclude_unset=True))
        return self.bundle

    async def pin_set(self, bundle_id: str, project_id: str, version_id: str, actor: Actor,
                      confirm_downgrade: bool = False) -> ModpackBundle:
        self._record("pin_set", bundle_id, project_id, version_id, actor)
        self.confirmed.append(confirm_downgrade)
        if self.downgrade is not None and not confirm_downgrade:
            raise DowngradeRefused(self.downgrade.text, self.downgrade)
        self.bundle.pins = {**self.bundle.pins, project_id: version_id}
        return self.bundle

    async def via_set(self, bundle_id: str, project_id: str, native: bool, actor: Actor,
                      confirm: bool = False) -> ModpackBundle:
        self._record("via_set", bundle_id, project_id, native, actor, confirm)
        if not confirm:
            raise ViaChangeRefused("FabricMod: vía Connector → nativa.", project_id=project_id, title="FabricMod",
                                   to_native=native)
        return self.bundle

    async def pin_origin_select(self, bundle_id: str, project_id: str, source: Any, actor: Actor,
                                confirm_downgrade: bool = False, server_id: Optional[str] = None) -> ModpackBundle:
        self._record("pin_origin_select", bundle_id, project_id, source, actor)
        self.confirmed.append(confirm_downgrade)
        self.origin_servers.append(server_id)
        if self.downgrade is not None and not confirm_downgrade:
            raise DowngradeRefused(self.downgrade.text, self.downgrade)
        return self.bundle

    async def pin_unset(self, bundle_id: str, project_id: str, actor: Actor) -> ModpackBundle:
        self._record("pin_unset", bundle_id, project_id, actor)
        self.bundle.pins = {k: v for k, v in self.bundle.pins.items() if k != project_id}
        return self.bundle

    async def active_jobs(self, bundle_id: str) -> list[Any]:
        return list(self.jobs)

    async def catalog_refresh(self, bundle_id: str, actor: Actor) -> Any:
        self._record("catalog_refresh", bundle_id, actor)
        if self.error is not None:
            raise self.error
        return None

    async def channel_exception_set(self, bundle_id: str, project_id: str, channel: str, actor: Actor) -> ModpackBundle:
        self._record("channel_exception_set", bundle_id, project_id, channel, actor)
        self.bundle.channel_exceptions = {**self.bundle.channel_exceptions, project_id: channel}
        return self.bundle

    async def channel_exception_unset(self, bundle_id: str, project_id: str, actor: Actor) -> ModpackBundle:
        self._record("channel_exception_unset", bundle_id, project_id, actor)
        self.bundle.channel_exceptions = {k: v for k, v in self.bundle.channel_exceptions.items() if k != project_id}
        return self.bundle

    async def project_add(self, bundle_id: str, refs: list[str], origin: ProjectOrigin, actor: Actor) -> list[PackProject]:
        self._record("project_add", bundle_id, refs, origin, actor)
        owned = {p.project_id for p in self.bundle.projects}
        self.bundle.projects = self.bundle.projects + [PackProject(project_id=r, origin=origin) for r in refs if r not in owned]
        return self.bundle.projects

    async def bridge_companions_now(self, bundle_id: str, project_id: str, actor: Actor) -> list[tuple[str, str]]:
        """What `missing` says a bridged mod needs (batch C); nothing by default. With `pending`, the
        catalog lacks what it takes and its job is queued (O6): that CatalogPending."""
        self.calls.append(("bridge_companions_now", bundle_id, project_id, actor))
        pending = getattr(self, "pending", None)
        if pending is not None:
            raise pending
        return list(getattr(self, "missing", []))

    async def project_remove(self, bundle_id: str, project_id: str, actor: Actor) -> ProjectRemoval:
        self._record("project_remove", bundle_id, project_id, actor)
        self.bundle.projects = [p for p in self.bundle.projects if p.project_id != project_id]
        return ProjectRemoval(projects=self.bundle.projects, dependents=self.dependents, cleared={})

    async def project_dependents(self, bundle_id: str, project_id: str) -> Optional[list[str]]:
        # The removal says which dependents it used: a second read could contradict it (round 2 review).
        raise AssertionError("the route reads the dependents the removal used")

    async def bridge_idle(self, bundle_id: str) -> list[str]:
        return self.idle

    async def resolve(self, bundle_id: str, actor: Optional[Actor] = None) -> Lock:
        self._record("resolve", bundle_id, actor)
        if isinstance(self.error, CatalogPending):
            raise self.error  # the catalog lacks something: its job is queued (O6)
        self.saved = resolution(self.lock)
        return self.lock

    # Whether resolve_current resolves (O9 D2); off, it is a pack the catalog lacks: its job is
    # queued and nothing changes, as most tests want the resolution they set.
    resolves_by_itself = False

    async def resolve_current(self, bundle_id: str, actor: Optional[Actor] = None) -> Optional[Lock]:
        self._record("resolve_current", bundle_id, actor)
        if not self.resolves_by_itself:
            return None
        self.saved, self.stale = resolution(self.lock), False
        return self.lock

    async def mod_list(self, bundle_id: str, check: Optional[UpdateCheck],
                       bundle: Optional[ModpackBundle] = None) -> ModList:
        self.checks_seen.append(check)
        self.bundles_given.append(bundle)
        rows = self.rows
        if rows is None:
            entries = {e.project_id: e for e in self.saved.lock.entries} if self.saved else {}
            pack = {p.project_id: p.origin.value for p in self.bundle.projects}
            rows = [row(i, self.titles.get(i, i), origin, side=entries[i].server if i in entries else None,
                        pinned=i in self.bundle.pins, pinned_version=self.bundle.pins.get(i)) for i, origin in pack.items()]
            rows += [row(i, self.titles.get(i, i), None, side=e.server, pinned=i in self.bundle.pins,
                         pinned_version=self.bundle.pins.get(i))
                     for i, e in entries.items() if i not in pack]
        # Like the service: a title for each row, for what the resolution's suggestions and problems
        # name, and for the pins no row shows.
        shown = {r.project_id for r in rows}
        orphan_pins = [i for i in self.bundle.pins if i not in shown]
        problems = self.saved.lock.problems if self.saved else []
        named = [s.project_id for s in self.saved.lock.suggestions] if self.saved else []
        named += [p.project_id for p in problems if p.project_id] + [r for p in problems for r in p.requested_by]
        named += [p.conflict_with for p in problems if p.conflict_with]
        titles = {i: self.titles.get(i, i) for i in named + orphan_pins} | {r.project_id: r.title for r in rows}
        # A card for each problem's mod without a row, as `cards` says (or a bare one).
        problem_cards = {p.project_id: self.cards.get(p.project_id) or row(p.project_id, titles[p.project_id], None)
                         for p in problems if p.project_id and p.project_id not in shown}
        return ModList(rows=rows, resolution=self.saved, stale=self.stale, icons_missing=0, titles=titles,
                       orphan_pins=orphan_pins, problem_cards=problem_cards,
                       via_back=set(self.bundle.native_only) if self.via_back is None else self.via_back)

    async def project_versions(self, bundle_id: str, project_id: str) -> list[VersionOption]:
        self.calls.append(("project_versions", bundle_id, project_id))
        if self.versions_error is not None:
            raise self.versions_error
        return self.versions

    async def build_create(self, bundle_id: str, actor: Actor) -> ModpackBuild:
        self._record("build_create", bundle_id, actor)
        if self.blocked is not None:
            raise self.blocked
        return self.builds[0]

    async def build_request(self, bundle_id: str, actor: Actor, test: bool = False) -> Any:
        # A test build (O8 B1) is recorded with its mark; a normal one as before.
        self._record("build_request", bundle_id, actor, *(["test"] if test else []))
        if self.blocked is not None:
            raise self.blocked
        return self.queued

    async def last_build_blocked(self, bundle_id: str) -> bool:
        return self.build_blocked_now

    async def last_failed_job(self, bundle_id: str) -> Any:
        return self.failed_job

    async def resolution(self, bundle_id: str) -> Optional[tuple[ModpackResolution, bool]]:
        return (self.saved, self.stale) if self.saved else None

    async def build_find(self, bundle_id: str) -> list[ModpackBuild]:
        return self.builds

    async def build_recommend(self, bundle_id: str, number: int, actor: Actor) -> ModpackBundle:
        self._record("build_recommend", bundle_id, number, actor)
        return self.bundle

    async def installed_builds(self, bundle_id: str) -> set[int]:
        return self.installed

    async def build_name(self, bundle_id: str, number: int, name: Optional[str], actor: Actor) -> ModpackBuild:
        self._record("build_name", bundle_id, number, name, actor)
        return self.builds[0]

    async def build_star(self, bundle_id: str, number: int, starred: bool, actor: Actor) -> ModpackBuild:
        self._record("build_star", bundle_id, number, starred, actor)
        return self.builds[0]

    async def build_approve(self, bundle_id: str, number: int, actor: Actor, recommend: bool = False) -> Any:
        from src.services.modpack import ApproveResult
        self._record("build_approve", bundle_id, number, actor, recommend)
        return self.approved if self.approved is not None else \
            ApproveResult(bundle=self.bundle, approved=True, in_force=number, newer=[])

    async def build_mrpack(self, bundle_id: str, number: int) -> tuple[str, bytes]:
        self._record("build_mrpack", bundle_id, number)
        return "Pack-Uno-2.mrpack", b"PK-fake"

    async def replacement_find(self) -> list[ModpackReplacement]:
        return self.rules

    async def replacement_set(self, project_id: str, replacement: Optional[str], actor: Actor) -> ModpackReplacement:
        self._record("replacement_set", project_id, replacement, actor)
        return ModpackReplacement.model_construct(project_id=project_id, replacement=replacement)

    async def replacement_delete(self, project_id: str, actor: Actor) -> bool:
        self._record("replacement_delete", project_id, actor)
        return True


class FakeModrinth:
    def __init__(self) -> None:
        self.titles: dict[str, str] = {"P1": "Sodium", "P2": "Lithium"}
        self.hits: list[dict[str, Any]] = [
            {"project_id": "P3", "slug": "iris", "title": "Iris", "description": "Shaders", "project_type": "mod",
             "author": "coderbot", "downloads": 1234, "icon_url": "https://cdn.modrinth.com/data/P3/icon.png"}]
        self.error: Optional[Exception] = None
        self.searches: list[tuple[Any, ...]] = []
        self.filled: list[list[str]] = []
        # The Minecraft releases Modrinth lists (F09); None: Modrinth did not answer.
        self.game_versions: Optional[list[str]] = None

    async def game_versions_release(self) -> Optional[list[str]]:
        return self.game_versions

    async def search(self, query: str, game_version: Optional[str], loader: Optional[str], limit: int = 10) -> list[dict[str, Any]]:
        self.searches.append((query, game_version, loader, limit))
        if self.error is not None:
            raise self.error
        return self.hits

    async def projects_filled(self, project_ids: list[str]) -> dict[str, ModrinthProject]:
        """Counts its calls: a render fills the catalog once, for every mod it shows."""
        self.filled.append(list(project_ids))
        return {i: project(i, self.titles[i]) for i in project_ids if i in self.titles}



class FakeChecks:
    def __init__(self) -> None:
        self.check: Optional[UpdateCheck] = None

    async def latest(self, bundle_id: str) -> Optional[UpdateCheck]:
        return self.check


class FakeJobs:
    """What the pack page asks of the jobs (B1): the build each server last simulated, counted."""
    def __init__(self) -> None:
        self.simulated: dict[str, int] = {}
        self.reads: list[str] = []

    async def simulated_build(self, server_id: str, bundle_id: str) -> Optional[int]:
        self.reads.append("simulated_build")
        return self.simulated.get(server_id)

    async def simulated_builds(self, server_ids: list[str], bundle_id: str) -> dict[str, int]:
        self.reads.append("simulated_builds")
        return {s: self.simulated[s] for s in server_ids if s in self.simulated}


class FakePtero:
    def __init__(self) -> None:
        self.servers: list[PterodactylServer] = []

    async def server_find(self) -> list[PterodactylServer]:
        return self.servers

    async def server_link_bundle(self, server_id: str, bundle_id: Optional[str], actor: Actor,
                                 only_unlinked: bool = False) -> PterodactylServer:
        server = next(s for s in self.servers if str(s.id) == server_id)
        if only_unlinked and server.bundle_id is not None:
            raise HTTP_409_CONFLICT("Ese servidor ya tiene un modpack")
        self.linked = getattr(self, "linked", []) + [(server_id, bundle_id, actor)]
        return server


@pytest.fixture(autouse=True)
def reset_overrides() -> Any:
    yield
    import main

    for provider in (main.container.modpackService, main.container.modrinthService,
                     main.container.updateCheckService, main.container.pterodactylService,
                     main.container.jobService):
        provider.reset_override()


def setup_all(actor: Optional[Actor] = EDITOR) -> tuple[TestClient, FakeModpack, FakeModrinth, FakeChecks, FakePtero]:
    import main

    modpack, modrinth, checks, ptero = FakeModpack(), FakeModrinth(), FakeChecks(), FakePtero()
    main.container.modpackService.override(providers.Object(modpack))
    main.container.modrinthService.override(providers.Object(modrinth))
    main.container.updateCheckService.override(providers.Object(checks))
    main.container.pterodactylService.override(providers.Object(ptero))
    main.container.jobService.override(providers.Object(FakeJobs()))
    # Sin `with`: el lifespan abriría MongoDB y el proveedor OIDC.
    client = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
    if actor is not None:
        client.cookies.set("session", session_cookie(session_for(actor)))
    return client, modpack, modrinth, checks, ptero


def setup(actor: Optional[Actor] = EDITOR) -> tuple[TestClient, FakeModpack, FakeModrinth]:
    client, modpack, modrinth, _, _ = setup_all(actor)
    return client, modpack, modrinth


def call(modpack: FakeModpack, name: str) -> tuple[Any, ...]:
    [found] = [c for c in modpack.calls if c[0] == name]
    return found


# Lista y alta.

def test_list_shows_bundles_and_the_create_form() -> None:
    client, _, _ = setup()
    response = client.get("/modpacks")
    assert response.status_code == 200
    assert "Pack Uno" in response.text and f'href="/modpacks/{BUNDLE_ID}"' in response.text
    assert 'hx-post' not in response.text and 'action="/modpacks"' in response.text
    assert '<option value="fabric"' in response.text


@pytest.mark.network
def test_create_redirects_to_the_detail_with_the_actor(mongo: MongoRunner) -> None:
    client, modpack, _ = setup()

    async def scenario() -> Any:  # init_beanie: crear el Document exige la base inicializada
        return client.post("/modpacks", headers=SAME_ORIGIN, data={
            "name": "Nuevo", "description": "", "version": "1.21.1", "loader": "fabric", "loader_version": ""})

    response = mongo(scenario)
    assert response.status_code == 303 and response.headers["location"] == f"/modpacks/{BUNDLE_ID}"
    _, created, actor = call(modpack, "bundle_create")
    assert (created.name, created.version, created.loader, created.loader_version) == ("Nuevo", "1.21.1", MinecraftLoaders.fabric, None)
    assert actor == EDITOR


def test_create_with_a_missing_name_is_a_notice() -> None:
    client, modpack, _ = setup()
    response = client.post("/modpacks", headers=HTMX, data={"version": "1.21.1", "loader": "fabric"})
    assert response.status_code == 422 and "Revisa los datos" in response.text
    assert modpack.calls == []


# Detalle.

def test_detail_lists_the_mods_in_one_list_with_their_titles() -> None:
    client, _, _ = setup()
    response = client.get(f"/modpacks/{BUNDLE_ID}")
    assert response.status_code == 200
    text = response.text
    assert "Sodium" in text and "Lithium" in text
    assert text.index("Sodium") < text.index("Lithium")  # en el orden del pack
    assert 'id="mods"' in text and 'id="builds"' in text
    assert 'id="projects"' not in text and 'id="lock"' not in text and 'id="pins"' not in text
    assert 'id="search-results"' in text
    assert f'hx-get="/modpacks/{BUNDLE_ID}/search"' in text
    assert 'hx-trigger="input changed delay:300ms"' in text and 'hx-target="#search-results"' in text


def test_detail_of_a_missing_bundle_is_a_404() -> None:
    client, modpack, _ = setup()

    async def missing(bundle_id: str) -> ModpackBundle:
        raise HTTP_404_NOT_FOUND("Bundle 'x' not found")

    modpack.bundle_get = missing  # type: ignore[method-assign]
    assert client.get(f"/modpacks/{BUNDLE_ID}").status_code == 404


def test_project_title_from_modrinth_is_escaped() -> None:
    client, modpack, modrinth = setup()
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    modpack.rows = [row("P1", evil, description=evil, categories=[evil], loaders=[evil], game_versions=[evil],
                        required_by=[evil], build_version=evil, newer_version=evil)]
    modrinth.hits[0]["title"] = evil
    modrinth.hits[0]["description"] = evil
    for response in (client.get(f"/modpacks/{BUNDLE_ID}"), client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "x"})):
        assert response.status_code == 200
        assert "<script>alert" not in response.text and "<img src=x" not in response.text
        assert "&lt;script&gt;" in response.text


def test_three_hundred_projects_render() -> None:
    client, modpack, modrinth = setup()
    ids = [f"ID{i:03d}" for i in range(300)]
    modpack.bundle = bundle([PackProject(project_id=i, origin=ProjectOrigin.pack) for i in ids])
    modpack.titles = {i: "Mod " + i + " " + "x" * 120 for i in ids}
    response = client.get(f"/modpacks/{BUNDLE_ID}")
    assert response.status_code == 200
    assert len(re.findall(r'data-project="ID\d{3}"', response.text)) == 300


# Búsqueda.

def test_search_uses_the_bundles_version_and_loader() -> None:
    client, _, modrinth = setup()
    response = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "iris"}, headers=HTMX)
    assert response.status_code == 200
    assert modrinth.searches == [("iris", "1.21.1", "fabric", 10)]
    assert "Iris" in response.text and "@coderbot" in response.text
    assert f'hx-post="/modpacks/{BUNDLE_ID}/projects"' in response.text
    assert 'name="ref" value="P3"' in response.text


def test_search_without_text_does_not_ask_modrinth() -> None:
    client, _, modrinth = setup()
    response = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "  "}, headers=HTMX)
    assert response.status_code == 200 and modrinth.searches == []


def test_search_marks_what_the_bundle_already_has() -> None:
    client, _, modrinth = setup()
    modrinth.hits[0]["project_id"] = "P1"
    response = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "sodium"}, headers=HTMX)
    assert "Ya está" in response.text and 'name="ref"' not in response.text


@pytest.mark.parametrize("error", [ClientConnectionError("down"), HTTP_409_CONFLICT("x"), RuntimeError("boom")])
def test_search_survives_modrinth_failure(error: Exception) -> None:
    client, _, modrinth = setup()
    modrinth.error = error
    response = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "iris"}, headers=HTMX)
    assert response.status_code == 200
    assert "Modrinth no respondió; intenta de nuevo" in response.text


def test_search_survives_a_modrinth_5xx() -> None:
    from fastapi import HTTPException

    client, _, modrinth = setup()
    modrinth.error = HTTPException(502, "bad gateway")
    response = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "iris"}, headers=HTMX)
    assert response.status_code == 200 and "Modrinth no respondió" in response.text


def test_hit_shows_author_downloads_and_environment() -> None:
    client, _, modrinth = setup()
    modrinth.hits[0]["environment"] = ["client_only"]
    text = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "iris"}, headers=HTMX).text
    assert "@coderbot" in text and "1,2 k descargas" in text and ">cliente<" in text


def test_every_environment_value_has_a_label_or_is_unknown() -> None:
    from src.model.modrinth.project import ModrinthEnvironment
    from src.routers.web.modpacks import environment_label

    for value in ModrinthEnvironment:
        assert (environment_label([value.value]) is None) == (value is ModrinthEnvironment.unknown), value


def test_hit_without_environment_shows_no_side() -> None:
    client, _, modrinth = setup()
    for environment in ([], ["unknown"], ["something_new"], "client_only", None, [5]):
        modrinth.hits[0]["environment"] = environment
        text = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "iris"}, headers=HTMX).text
        assert "Iris" in text and "cliente" not in text and "servidor" not in text, environment
    del modrinth.hits[0]["environment"]
    text = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "iris"}, headers=HTMX).text
    assert "Iris" in text and "cliente" not in text and "servidor" not in text


def test_hit_tolerates_badly_typed_author_and_downloads() -> None:
    client, _, modrinth = setup()
    modrinth.hits[0].update(author=None, downloads="many", environment=["server_only"])
    text = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "iris"}, headers=HTMX).text
    assert "Iris" in text and ">servidor<" in text and "0 descargas" in text and "@" not in text


@pytest.mark.parametrize("count, shown", [
    (0, "0"), (999, "999"), (1000, "1 k"), (1234, "1,2 k"), (34000, "34 k"), (34999, "34 k"),
    (999999, "999 k"), (1000000, "1 M"), (1234567, "1,2 M"), (34000000, "34 M"), (-5, "0"),
])
def test_downloads_are_abbreviated(count: int, shown: str) -> None:
    from src.routers.web.modpacks import abbreviate_downloads

    assert abbreviate_downloads(count) == shown


@pytest.mark.parametrize("environment, label", [
    (["client_and_server"], "cliente y servidor"),
    (["client_only"], "cliente"),
    (["client_only_server_optional"], "cliente"),
    (["singleplayer_only"], "un jugador"),
    (["server_only"], "servidor"),
    (["server_only_client_optional"], "servidor"),
    (["dedicated_server_only"], "servidor"),
    (["client_or_server"], "cliente o servidor"),
    (["client_or_server_prefers_both"], "cliente o servidor"),
    (["client_only", "client_only_server_optional"], "cliente"),
    (["client_or_server", "client_or_server_prefers_both"], "cliente o servidor"),
    (["client_only", "server_only"], "varía según la versión"),
    (["client_and_server", "client_or_server"], "varía según la versión"),
    (["client_only", "unknown"], "cliente"),
    (["nope", "server_only", 7], "servidor"),
    (["unknown"], None), ([], None), (["nope"], None), ([None, 3], None), (None, None), ("client_only", None),
])
def test_environment_labels(environment: Any, label: Optional[str]) -> None:
    from src.routers.web.modpacks import environment_label

    assert environment_label(environment) == label


def test_only_modrinth_cdn_icons_are_images() -> None:
    client, _, modrinth = setup()
    modrinth.hits = [
        {"project_id": "A", "slug": "a", "title": "Good", "icon_url": "https://cdn.modrinth.com/data/A/i.png"},
        {"project_id": "B", "slug": "b", "title": "Evil", "icon_url": "https://evil.example/i.png"},
        {"project_id": "C", "slug": "c", "title": "Sneaky", "icon_url": "https://cdn.modrinth.com.evil.example/i.png"},
        {"project_id": "D", "slug": "d", "title": "NoIcon", "icon_url": None},
    ]
    text = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "x"}, headers=HTMX).text
    assert text.count("<img") == 1 and 'src="https://cdn.modrinth.com/data/A/i.png"' in text
    assert "evil.example" not in text


# Proyectos.

def test_add_project_is_an_admin_addition_by_the_actor() -> None:
    client, modpack, _ = setup()
    modpack.titles["P3"] = "Iris"
    response = client.post(f"/modpacks/{BUNDLE_ID}/projects", headers=HTMX, data={"ref": "P3"})
    assert response.status_code == 200 and 'id="mods"' in response.text and 'data-project="P3"' in response.text
    assert call(modpack, "project_add")[1:] == (str(BUNDLE_ID), ["P3"], ProjectOrigin.admin, EDITOR)
    assert '<div id="notices" hx-swap-oob="beforeend">' in response.text and "Agregado: Iris" in response.text
    # El formulario "por slug o id" no viene de un resultado de la búsqueda: nada que marcar fuera de banda.
    assert 'id="hit-P3"' not in response.text
    again = client.post(f"/modpacks/{BUNDLE_ID}/projects", headers=HTMX, data={"ref": "P3"})
    notice = again.text[again.text.index('<div id="notices"'):]
    assert "Ese mod ya estaba en el modpack" in notice and 'data-notice-tone="info"' in notice


def test_add_unknown_project_shows_the_reason_as_a_notice() -> None:
    client, modpack, _ = setup()
    modpack.error = HTTP_404_NOT_FOUND("Modrinth has no project nope")
    response = client.post(f"/modpacks/{BUNDLE_ID}/projects", headers=HTMX, data={"ref": "nope"})
    assert response.status_code == 404 and "Modrinth has no project nope" in response.text


def test_remove_project_with_the_actor() -> None:
    client, modpack, _ = setup()
    response = client.delete(f"/modpacks/{BUNDLE_ID}/projects/P2", headers=HTMX)
    assert response.status_code == 200 and 'id="mods"' in response.text
    assert call(modpack, "project_remove")[1:] == (str(BUNDLE_ID), "P2", EDITOR)
    assert "Lithium" not in response.text  # el doble lo quita del pack


# Lock y decisiones.

def test_the_mods_show_the_resolution_on_top() -> None:
    client, modpack, _ = setup()
    modpack.resolves_by_itself = True
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert call(modpack, "resolve_current")[1] == str(BUNDLE_ID)
    assert "Sodium" in text and "Lithium" in text
    # The panel counts what is pending; deciding is in its own tab, not above the list (spec §3).
    assert f'hx-post="/modpacks/{BUNDLE_ID}/decisions/bulk' not in text
    text = text[text.index('<div id="mods"'):]
    assert text.index("Resuelto") < text.index("1 por decidir") < text.index('data-tab="todos"') < text.index('data-project="P1"')


def test_resolving_by_itself_keeps_the_view_it_was_asked_from() -> None:
    client, modpack, _ = setup()
    modpack.resolves_by_itself = True
    modpack.lock = lock(entry("P1"), problems=[Problem(kind="no_file", project_id="P9", detail="v-P9 no tiene archivo")])
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "problemas"}).text
    assert 'aria-current="page"' in tab(text, "problemas") and 'data-problem="P9"' in view(text, "problemas")


def test_decision_is_one_key_with_the_actor() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/decisions", headers=HTMX, data={"project_id": "P2", "server": "no"})
    assert response.status_code == 200 and 'id="mods"' in response.text
    assert call(modpack, "decision_set")[1:] == (str(BUNDLE_ID), "P2", False, EDITOR)
    assert '<div id="notices" hx-swap-oob="beforeend">' in response.text and "Decisiones guardadas" in response.text


def test_decision_with_a_dotted_id_is_refused() -> None:
    client, modpack, _ = setup()
    for bad in ("a.b", "$set", "a/b"):
        response = client.post(f"/modpacks/{BUNDLE_ID}/decisions", headers=HTMX, data={"project_id": bad, "server": "no"})
        assert response.status_code == 422
    assert modpack.calls == []


def test_decision_must_be_yes_or_no() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/decisions", headers=HTMX, data={"project_id": "P2", "server": "maybe"})
    assert response.status_code == 422 and modpack.calls == []


def test_lock_offers_one_form_with_every_undecided_mod() -> None:
    client, modpack, _ = setup()
    modpack.resolves_by_itself = True
    modpack.lock = lock(entry("P1"), entry("P2", "admin"), entry("P3", "admin"))
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "decidir"}).text
    assert f'hx-post="/modpacks/{BUNDLE_ID}/decisions/bulk?vista=decidir"' in text
    assert 'name="decision.P2"' in text and 'name="decision.P3"' in text and 'name="decision.P1"' not in text
    assert "Guardar las 2 decisiones" in text
    form = text[text.index(f'hx-post="/modpacks/{BUNDLE_ID}/decisions/bulk?vista=decidir"'):]
    form = form[:form.index("</form>")]
    for undecided in ("P2", "P3"):  # once each, inside the bulk form, by title
        assert text.count(f'name="decision.{undecided}"') == form.count(f'name="decision.{undecided}"') == 1
        assert text.count(f'id="bulk-{undecided}"') == 1
    assert "Lithium" in form and ">P2<" not in form


def test_bulk_decisions_call_the_service_with_the_dict_and_skip_undecided() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/decisions/bulk", headers=HTMX,
                           data={"decision.P2": "si", "decision.P3": "no", "decision.P4": ""})
    assert response.status_code == 200 and 'id="mods"' in response.text and "Decisiones guardadas" in response.text
    assert call(modpack, "decisions_set")[1:] == (str(BUNDLE_ID), {"P2": True, "P3": False}, EDITOR)


def test_bulk_decisions_refuse_bad_values_ids_and_empty_forms() -> None:
    client, modpack, _ = setup()
    for data in ({"decision.P2": "yes"}, {"decision.P2": "si", "decision.a.b": "no"}, {"decision.$x": "si"}, {"other": "si"}, {}):
        response = client.post(f"/modpacks/{BUNDLE_ID}/decisions/bulk", headers=HTMX, data=data)
        assert response.status_code == 422, data
    assert modpack.calls == []


# Builds.

def test_builds_list_marks_the_recommended_one() -> None:
    client, _, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert f"/modpacks/{BUNDLE_ID}/builds/2/mrpack" in text and "Recomendado" in text
    assert f'hx-post="/modpacks/{BUNDLE_ID}/builds/2/recommend"' in text


def test_create_build_queues_its_job_with_the_actor_and_says_so() -> None:
    # Spec 2026-10-09-economia-de-requests (E9): the build is a job; the banner shows it.
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX)
    assert response.status_code == 200 and response.headers["HX-Trigger"] == "job-started"
    assert "Creando el build: revisando cambios en Modrinth" in response.text
    # Whatever was in the dialog (it may come from it) is emptied, so it closes.
    assert response.headers["HX-Retarget"] == "#build-dialog" and response.headers["HX-Reswap"] == "innerHTML"
    assert call(modpack, "build_request")[1:] == (str(BUNDLE_ID), EDITOR)


def test_build_conflict_shows_reason_as_notice() -> None:
    client, modpack, _ = setup()
    modpack.error = HTTP_409_CONFLICT("Mods sin decidir: x")
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX)
    assert response.status_code == 409 and "Mods sin decidir: x" in response.text
    assert 'data-notice' in response.text


def undecided_block(*ids: str, only: bool = True, detail: str = "Mods sin decidir") -> BuildBlocked:
    return BuildBlocked(detail, lock(*(entry(i, "admin") for i in ids)), only)


def test_blocked_by_undecided_answers_a_dialog_with_titles() -> None:
    client, modpack, _ = setup()
    modpack.blocked = undecided_block("P2")
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX)
    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#build-dialog" and response.headers["HX-Reswap"] == "innerHTML"
    text = response.text
    # Drawn closed and marked: app.js opens it with showModal() (a modal, not a static `open`).
    tag = text.split("<dialog")[1].split(">")[0]
    assert " open" not in tag and "data-autoopen" in tag
    assert "Lithium" in text and 'name="decision.P2"' in text and "Decidir y crear build" in text
    assert "data-dialog-close" in text
    assert '<option value="" selected>Por decidir</option>' in text and '<option value="no">Desactivado</option>' in text
    assert f'hx-post="/modpacks/{BUNDLE_ID}/builds"' in text
    assert [c[0] for c in modpack.calls] == ["build_request"]


def test_detail_has_the_dialog_container() -> None:
    client, _, _ = setup()
    assert 'id="build-dialog"' in client.get(f"/modpacks/{BUNDLE_ID}").text


def test_dialog_submit_saves_decisions_then_builds() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX, data={"decision.P2": "si", "decision.P3": ""})
    assert response.status_code == 200 and response.headers["HX-Trigger"] == "job-started"
    assert response.headers["HX-Retarget"] == "#build-dialog" and "<dialog" not in response.text
    assert [c[0] for c in modpack.calls] == ["decisions_set", "build_request"]
    assert call(modpack, "decisions_set")[1:] == (str(BUNDLE_ID), {"P2": True}, EDITOR)


def test_dialog_submit_with_bad_decisions_is_refused_before_saving() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX, data={"decision.P2": "quizá"})
    assert response.status_code == 422 and modpack.calls == []


OPAQUE = "No se puede congelar este lock: P3: falta decidir si va en el servidor"


def test_still_blocked_after_deciding_names_the_mods_by_title_inside_the_dialog() -> None:
    client, modpack, modrinth = setup()
    modpack.blocked = undecided_block("P3", detail=OPAQUE)
    modrinth.titles["P3"] = "Otro"
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX, data={"dialog": "1", "decision.P2": "si"})
    assert response.status_code == 200 and response.headers["HX-Retarget"] == "#build-dialog"
    reason = visible(response.text.split("data-notice-tone")[1].split("</p>")[0])
    assert "Falta decidir si van en el servidor: Otro" in reason
    assert "P3:" not in response.text and 'name="decision.P3"' in response.text


def test_blocked_by_problems_after_deciding_names_them_by_title_inside_the_dialog() -> None:
    client, modpack, _ = setup()
    modpack.blocked = BuildBlocked(OPAQUE, lock(problems=[Problem(kind="unresolvable", project_id="P2",
                                                                  detail="sin versión para 1.21.1")]), False)
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX, data={"dialog": "1", "decision.P2": "si"})
    assert response.status_code == 200 and "Lithium: sin versión para 1.21.1" in response.text
    assert OPAQUE not in response.text
    assert response.headers["HX-Retarget"] == "#build-dialog" and "Decidir y crear build" not in response.text


def test_the_dialog_sent_without_deciding_says_what_is_missing() -> None:
    client, modpack, _ = setup()
    modpack.blocked = undecided_block("P2", detail=OPAQUE)
    first = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX).text
    assert 'name="dialog"' in first and "data-notice" not in first  # the button's click: no reason yet
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX, data={"dialog": "1", "decision.P2": ""})
    assert response.status_code == 200 and response.headers["HX-Retarget"] == "#build-dialog"
    assert "Elige «En el servidor» o «Desactivado» para al menos un mod" in response.text
    assert "decisions_set" not in [c[0] for c in modpack.calls]


def test_blocked_by_problems_from_the_dialog_stays_in_the_dialog() -> None:
    client, modpack, _ = setup()
    modpack.blocked = BuildBlocked(OPAQUE, lock(problems=[Problem(kind="unresolvable", project_id="P2",
                                                                  detail="sin versión")]), False)
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX, data={"dialog": "1"})
    assert response.status_code == 200 and response.headers["HX-Retarget"] == "#build-dialog"
    assert "Lithium: sin versión" in response.text


def dialog_tag(text: str) -> str:
    return text.split("<dialog")[1].split(">")[0]


def assert_no_nested_forms(text: str) -> None:
    depth = 0
    for tag in re.finditer(r"<(/?)form\b", text):
        depth += -1 if tag.group(1) else 1
        assert depth in (0, 1), "a <form> inside another"
    assert depth == 0


def test_blocked_by_a_problem_and_a_pending_mod_opens_the_dialog_with_both() -> None:
    """Spec §1, "Al crear un build": the button's click opens the build dialog, its problems first
    (each a card with its actions, from the dialog) and then the mods to decide."""
    client, modpack, modrinth = setup()
    modrinth.titles["P3"] = "Iris"
    modpack.blocked = BuildBlocked(OPAQUE, lock(entry("P1"), entry("P3", "admin"), problems=[
        Problem(kind="no_file", project_id="P2", detail="v-P2 no tiene archivo")]), False)
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX)
    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#build-dialog" and response.headers["HX-Reswap"] == "innerHTML"
    text = response.text
    tag = dialog_tag(text)
    assert " open" not in tag and "data-autoopen" in tag and 'aria-labelledby="decide-dialog-title"' in tag
    title = re.search(r'<h2 id="decide-dialog-title"[^>]*>', text)
    assert title and 'tabindex="-1"' in title.group(0) and "autofocus" in title.group(0)
    lithium = problem_card(text, "P2")
    assert mod_link("P2") in lithium and "Quitar del modpack" in lithium
    assert f'hx-delete="/modpacks/{BUNDLE_ID}/projects/P2?contexto=dialogo"' in lithium
    assert 'hx-target="#build-dialog"' in lithium and 'hx-swap="innerHTML"' in lithium
    assert "contexto=problemas" not in text and 'hx-target="#mods"' not in text
    assert text.index('data-problem="P2"') < text.index('name="decision.P3"')
    assert "Iris" in text and "Decidir y crear build" in text and "P2:" not in text
    assert_no_nested_forms(text)
    assert [c[0] for c in modpack.calls] == ["build_request"]


def test_blocked_by_problems_only_opens_the_dialog_with_their_actions() -> None:
    client, modpack, _ = setup()
    alternative = Alternative(version_id="ver-b", version_number="1.0-beta", channel="beta", published=T0)
    modpack.blocked = BuildBlocked(OPAQUE, lock(entry("P1"), problems=[
        Problem(kind="unresolvable", project_id="P2", alternative=alternative, detail="sin versión")]), False)
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX)
    assert response.status_code == 200 and response.headers["HX-Retarget"] == "#build-dialog"
    use = response.text[response.text.index(f'hx-post="/modpacks/{BUNDLE_ID}/channel-exceptions"'):]
    use = use[:use.index("</form>")]
    assert 'name="contexto" value="dialogo"' in use and 'hx-target="#build-dialog"' in use
    assert 'name="channel" value="beta"' in use and "Usar la beta" in use
    assert "Decidir y crear build" not in response.text and "Todo resuelto" not in response.text
    assert "data-notice" not in response.text  # the button's click: the cards say it, no reason yet


DIALOG_ACTIONS = [
    ("POST", f"/modpacks/{BUNDLE_ID}/pins", {"project_id": "P2", "version_id": "ver-b", "contexto": "dialogo"}, "pin_set"),
    ("POST", f"/modpacks/{BUNDLE_ID}/channel-exceptions", {"project_id": "P2", "channel": "beta", "contexto": "dialogo"},
     "channel_exception_set"),
    ("DELETE", f"/modpacks/{BUNDLE_ID}/pins/P1?contexto=dialogo", None, "pin_unset"),
    ("DELETE", f"/modpacks/{BUNDLE_ID}/projects/P2?contexto=dialogo", None, "project_remove"),
    ("POST", "/replacements", {"project_id": "X9", "replacement": "", "bundle_id": str(BUNDLE_ID),
                               "contexto": "dialogo"}, "replacement_set"),
]


@pytest.mark.parametrize("method,path,data,action", DIALOG_ACTIONS)
def test_a_problem_action_in_the_dialog_resolves_and_answers_the_dialog_never_a_build(
        method: str, path: str, data: Optional[dict[str, str]], action: str) -> None:
    """Spec §1 (owner's decision): the action is applied, the pack resolved again and the dialog
    recalculated from that lock; the build is the owner's to create."""
    client, modpack, modrinth = setup()
    modpack.titles["P3"] = "Iris"
    modpack.lock = lock(entry("P1"), entry("P3", "admin"), problems=[Problem(kind="no_file", project_id="P1", detail="x")])
    response = client.request(method, path, headers=HTMX, data=data)
    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#build-dialog" and response.headers["HX-Reswap"] == "innerHTML"
    assert [c[0] for c in modpack.calls] == [action, "resolve"] and call(modpack, action)[-1] == EDITOR
    text = response.text
    assert "data-autoopen" in dialog_tag(text)
    # What is left, from the fresh lock: P2's problem is gone, P1's is there, and P3 still to decide.
    assert 'data-problem="P2"' not in text and 'data-problem="P1"' in text
    assert f'/projects/P1?contexto=dialogo"' in problem_card(text, "P1") and 'name="decision.P3"' in text
    assert "Iris" in text and "Todo resuelto" not in text
    # The titles and cards come from mod_list, which fills the catalog once for the dialog and
    # #mods behind it: the route asks Modrinth for nothing of its own.
    assert modrinth.filled == []


@pytest.mark.parametrize("method,path,data,action", DIALOG_ACTIONS)
def test_with_nothing_left_the_dialog_says_todo_resuelto_and_offers_crear_build(
        method: str, path: str, data: Optional[dict[str, str]], action: str) -> None:
    client, modpack, _ = setup()
    modpack.lock = lock(entry("P1"))
    response = client.request(method, path, headers=HTMX, data=data)
    assert response.status_code == 200 and response.headers["HX-Retarget"] == "#build-dialog"
    text = response.text
    assert "Todo resuelto" in said(text) and "data-problem" not in text and "decision." not in text
    create = text[text.rindex("<form", 0, text.index(f'hx-post="/modpacks/{BUNDLE_ID}/builds"')):]
    create = create[:create.index("</form>")]
    assert 'hx-target="#builds"' in create and 'hx-swap="outerHTML"' in create and ">Crear build<" in create
    assert 'name="dialog"' not in create and "Decidir y crear build" not in text
    assert "build_request" not in [c[0] for c in modpack.calls]


def dialog_part(text: str) -> str:
    """The build dialog of an answer, without what comes out of band after it."""
    return text[:text.index("</dialog>")]


def test_an_action_keeps_the_tab_the_page_was_on() -> None:
    # B1 (owner, 2026-10-09): after a dialog action or adding a mod, #mods stays in the page's tab;
    # htmx names the page's URL (the tabs push ?vista=) in HX-Current-URL. Unknown or none: "Todos".
    client, modpack, _ = setup()
    modpack.lock = lock(entry("P1"), problems=[Problem(kind="no_file", project_id="P1", detail="x")])
    on = {**HTMX, "HX-Current-URL": f"https://testserver/modpacks/{BUNDLE_ID}?vista=problemas"}
    text = client.delete(f"/modpacks/{BUNDLE_ID}/projects/P2", params={"contexto": "dialogo"}, headers=on).text
    behind = text[text.index('<div id="mods"'):]
    assert 'aria-current="page"' in tab(behind, "problemas")
    added = client.post(f"/modpacks/{BUNDLE_ID}/projects", data={"ref": "P3"},
                        headers={**HTMX, "HX-Current-URL": f"https://testserver/modpacks/{BUNDLE_ID}?vista=decidir"}).text
    assert 'aria-current="page"' in tab(added, "decidir")
    odd = client.post(f"/modpacks/{BUNDLE_ID}/projects", data={"ref": "P3"},
                      headers={**HTMX, "HX-Current-URL": f"https://testserver/modpacks/{BUNDLE_ID}?vista=xyz"}).text
    assert 'aria-current="page"' in tab(odd, "todos")


def test_a_dialog_action_also_redraws_the_mods_and_builds_behind_it() -> None:
    """Closing the dialog after an action shows the pack as it now is: #mods (in "Todos") and
    #builds go out of band in the same answer, from the same resolution."""
    client, modpack, _ = setup()
    modpack.lock = lock(entry("P1"), entry("P3", "admin"), problems=[Problem(kind="no_file", project_id="P1", detail="x")])
    text = client.delete(f"/modpacks/{BUNDLE_ID}/projects/P2", params={"contexto": "dialogo"}, headers=HTMX).text
    mods_tag = re.search(r'<div id="mods"[^>]*>', text)
    assert mods_tag and 'hx-swap-oob="true"' in mods_tag.group(0)
    behind = text[mods_tag.start():]
    assert text.index("</dialog>") < mods_tag.start() and 'id="mods"' not in dialog_part(text)
    assert 'data-project="P2"' not in behind and 'data-project="P1"' in behind  # P2 was removed
    assert 'aria-current="page"' in tab(behind, "todos")
    assert said(tab(behind, "decidir")) == "Por decidir 1" and said(tab(behind, "problemas")) == "Con problemas 1"
    builds = builds_part(behind)
    assert 'hx-swap-oob="true"' in builds[:builds.index(">")] and "Decidir y crear build…" in builds
    # Only the dialog says what was done: no second success notice behind it.
    assert text.count("Quitado del modpack") == 1 and "Quitado del modpack" in dialog_part(text)
    # With nothing left, the builds behind it say so too.
    modpack.lock = lock(entry("P1"))
    text = client.delete(f"/modpacks/{BUNDLE_ID}/projects/P1", params={"contexto": "dialogo"}, headers=HTMX).text
    assert "Todo resuelto" in said(dialog_part(text)) and "data-ready" in builds_part(text[text.index("</dialog>"):])


def test_the_dialog_keeps_its_success_and_the_dialog_actions_refuse_a_bundle_less_replacement() -> None:
    client, modpack, _ = setup()
    modpack.lock = lock(entry("P1"))
    text = client.delete(f"/modpacks/{BUNDLE_ID}/projects/P2", params={"contexto": "dialogo"}, headers=HTMX).text
    assert "Quitado del modpack" in said(text)
    response = client.post("/replacements", headers=HTMX, data={"project_id": "X9", "replacement": "", "contexto": "dialogo"})
    assert response.status_code == 422 and [c[0] for c in modpack.calls] == ["project_remove", "resolve"]


def test_removing_a_mod_another_one_needs_says_what_it_kept() -> None:
    """d-7985ee-881689: the notice names who still brings the mod, and that its pin, decision and
    exception stay; a mod nobody needs gets the usual answer (its row gone, no notice)."""
    client, modpack, _ = setup()
    modpack.dependents = ["P1"]
    text = client.delete(f"/modpacks/{BUNDLE_ID}/projects/P2", headers=HTMX).text
    assert "Quitado del modpack. Sigue como dependencia de Sodium: su fijación, su decisión y su excepción se mantienen." in text
    modpack.dependents = []
    text = client.delete(f"/modpacks/{BUNDLE_ID}/projects/P1", headers=HTMX).text
    assert "Sigue como dependencia" not in text


def test_removing_the_last_bridged_mod_suggests_removing_the_bridge() -> None:
    """d-7985ee-80ee9a: the notice names the bridge mods left, to remove by hand; nothing else goes."""
    client, modpack, _ = setup()
    modpack.idle = ["Connector", "Forgified Fabric API"]
    text = client.delete(f"/modpacks/{BUNDLE_ID}/projects/P2", headers=HTMX).text
    assert "Ya no hay mods por el puente: puedes quitar Connector y Forgified Fabric API." in text
    assert [c[0] for c in modpack.calls].count("project_remove") == 1


JEI_DOWN = PinDowngrade(project_id="P2", title="JEI", floor_version="ver-57", floor_number="19.57",
                        pin_version="ver-51", pin_number="19.51", used_by=["el build 7", "el servidor «Survival»"])


@pytest.mark.parametrize("contexto, target, swap", [(None, "#mods", "outerHTML"), ("problemas", "#mods", "outerHTML"),
                                                    ("dialogo", "#build-dialog", "innerHTML")])
def test_a_pin_that_goes_back_opens_a_dialog_to_confirm_it(contexto: Optional[str], target: str, swap: str) -> None:
    """F27 (d-7985ee-c62c61): the pin is not saved; the answer is a dialog, into #pin-dialog, that
    describes the step back and sends the same pin again with the confirmation, to the same place."""
    client, modpack, _ = setup()
    modpack.downgrade = JEI_DOWN
    data = {"project_id": "P2", "version_id": "ver-51", **({"contexto": contexto} if contexto else {})}
    response = client.post(f"/modpacks/{BUNDLE_ID}/pins", data=data, headers=HTMX)
    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#pin-dialog" and response.headers["HX-Reswap"] == "innerHTML"
    text = response.text
    assert "<dialog" in text and "data-autoopen" in text and "Baja de versión" in text
    assert "JEI 19.57 → 19.51" in text and "El build 7 y el servidor «Survival» usan 19.57" in text
    assert "Fijar igual" in text and "Cancelar" in text
    form = re.search(r"<form[^>]*>", text)
    assert form and f'hx-post="/modpacks/{BUNDLE_ID}/pins"' in form.group(0)
    assert f'hx-target="{target}"' in form.group(0) and f'hx-swap="{swap}"' in form.group(0)
    assert 'name="confirm_downgrade" value="1"' in text and 'value="ver-51"' in text
    assert modpack.bundle.pins.get("P2") != "ver-51" and modpack.confirmed == [False]
    # Sent with the confirmation, it is saved as any pin.
    client.post(f"/modpacks/{BUNDLE_ID}/pins", data={**data, "confirm_downgrade": "1"}, headers=HTMX)
    assert modpack.bundle.pins["P2"] == "ver-51" and modpack.confirmed == [False, True]


def test_a_pin_below_its_floor_is_marked_in_the_list() -> None:
    # d-7985ee-f43772.
    client, modpack, _ = setup()
    modpack.rows = [row("P1", "JEI", pinned=True, pin_floor="19.57"), row("P2", "Sodium", pinned=True)]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    first = text[text.index('data-project="P1"'):text.index('data-project="P2"')]
    assert "baja (piso 19.57)" in first and text.count("baja (piso") == 1


def test_the_pack_page_has_the_place_the_pin_dialog_opens_in() -> None:
    client, _, _ = setup()
    assert 'id="pin-dialog"' in client.get(f"/modpacks/{BUNDLE_ID}").text


def test_v1_patch_takes_the_confirmation_as_a_query() -> None:
    from fastapi import FastAPI
    from src.dependences import get_current_actor
    from src.routers.v1 import include_routers_v1

    _, modpack, _ = setup()
    seen: list[bool] = []

    async def bundle_update(bundle_id: str, patch: Any, actor: Actor, confirm_downgrade: bool = False) -> Any:
        seen.append(confirm_downgrade)
        if not confirm_downgrade:
            raise DowngradeRefused(JEI_DOWN.text, JEI_DOWN)
        return modpack.bundle

    modpack.bundle_update = bundle_update  # type: ignore[method-assign]
    app = FastAPI()
    include_routers_v1(app)
    app.dependency_overrides[get_current_actor] = lambda: EDITOR
    v1 = TestClient(app, base_url="https://testserver")
    refused = v1.patch(f"/v1/modpack/{BUNDLE_ID}", json={"pins": {"P2": "ver-51"}})
    assert refused.status_code == 409 and "JEI 19.57 → 19.51" in refused.json()["detail"]
    assert v1.patch(f"/v1/modpack/{BUNDLE_ID}", params={"confirm_downgrade": "true"},
                    json={"pins": {"P2": "ver-51"}}).status_code == 200
    assert seen == [False, True]


def test_v1_still_answers_a_blocked_build_with_the_same_409() -> None:
    # main.py mounts /v1 in its lifespan (it opens MongoDB): here on an app of its own, as in
    # tests/test_startup.py, with the same container (and its doubles) and the session's actor.
    from fastapi import FastAPI
    from src.dependences import get_current_actor
    from src.routers.v1 import include_routers_v1

    _, modpack, _ = setup()
    modpack.blocked = BuildBlocked(OPAQUE, lock(entry("P3", "admin"), problems=[
        Problem(kind="no_file", project_id="P2", detail="x")]), False)
    app = FastAPI()
    include_routers_v1(app)
    app.dependency_overrides[get_current_actor] = lambda: EDITOR
    response = TestClient(app, base_url="https://testserver").post(f"/v1/modpack/{BUNDLE_ID}/build")
    assert response.status_code == 409 and response.json() == {"detail": OPAQUE}
    # The same request as the panel's (build_request, its job and key): no race with it (A2, U5).
    assert "HX-Retarget" not in response.headers and [c[0] for c in modpack.calls] == ["build_request"]


def test_the_build_button_is_where_the_focus_goes_back_when_the_dialog_s_opener_is_gone() -> None:
    client, modpack, _ = setup()
    button = re.search(r'<button[^>]*id="build-create"[^>]*>', builds_part(client.get(f"/modpacks/{BUNDLE_ID}").text))
    assert button and f'hx-post="/modpacks/{BUNDLE_ID}/builds"' in button.group(0)
    modpack.blocked = undecided_block("P2")
    assert 'data-return-focus="build-create"' in dialog_tag(client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX).text)


def test_app_js_opens_a_swapped_in_dialog_as_a_modal_once() -> None:
    client, _, _ = setup()
    text = client.get("/static/app.js").text
    assert "[data-autoopen]" in text and "removeAttribute('data-autoopen')" in text
    assert "!dialog.open" in text and "showModal()" in text
    # A dialog the answer replaced is not closed (its close would move the focus out of the new one).
    assert "isConnected" in text and "data-return-focus" in text
    # Only a failed write closes it (not a card's read opened from it, which keeps its choices),
    # and the drawer remembers the link that opened it, not the swap target.
    assert "requestConfig.verb" in text and "verb !== 'get'" in text
    assert "drawerOpener = link instanceof HTMLElement" in text
    # The sender of a request whose answer took it off the page: htmx re-fires afterRequest on a
    # parent, with that parent as `elt` (checked in Chromium, see the task 7 report).
    assert "requestConfig.elt" in text


def test_the_update_page_build_opens_the_dialog_on_the_pack_when_undecided() -> None:
    client, modpack, _ = setup()
    modpack.blocked = undecided_block("P2")
    response = client.post(f"/modpacks/{BUNDLE_ID}/actualizaciones/build", headers=HTMX)
    # Never the dialog where the page has no place for it: the pack opens it (O9 user-walk, A9).
    assert response.status_code == 200 and "<dialog" not in response.text
    assert response.headers["HX-Redirect"] == f"/modpacks/{BUNDLE_ID}?construir=1#builds-section"


def test_the_build_button_says_decide_when_the_saved_resolution_has_pending() -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(lock(entry("P2", "admin")))
    assert "Decidir y crear build…" in client.get(f"/modpacks/{BUNDLE_ID}").text
    modpack.saved = resolution(lock(entry("P1")))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "Decidir y crear build…" not in text and ">Crear build<" in text


def builds_part(text: str) -> str:
    tag = re.search(r'<div id="builds"[^>]*>', text)
    assert tag, "no #builds"
    return text[tag.start():]


@pytest.mark.parametrize("route,data", [("decisions", {"project_id": "P2", "server": "yes"}),
                                        ("decisions/bulk", {"decision.P2": "si"})])
def test_decisions_refresh_the_builds_out_of_band(route: str, data: dict[str, str]) -> None:
    """After deciding, the builds section is redrawn in the same answer: its button says whether
    something is still pending, so it never keeps saying "Decidir y crear build…" until a reload."""
    client, modpack, _ = setup()
    modpack.resolves_by_itself = True
    modpack.lock = lock(entry("P1"), entry("P2", "admin"), entry("P3", "admin"))
    text = client.post(f"/modpacks/{BUNDLE_ID}/{route}", headers=HTMX, data=data).text
    assert 'hx-swap-oob="true"' in re.search(r'<div id="builds"[^>]*>', text).group(0)  # type: ignore[union-attr]
    builds = builds_part(text)
    assert "Decidir y crear build…" in builds and "No quedan mods por decidir" not in builds
    assert text.count("Decisiones guardadas") == 1


@pytest.mark.parametrize("route,data", [("decisions", {"project_id": "P2", "server": "yes"}),
                                        ("decisions/bulk", {"decision.P2": "si"})])
def test_deciding_the_last_mod_highlights_crear_build(route: str, data: dict[str, str]) -> None:
    client, modpack, _ = setup()
    modpack.resolves_by_itself = True
    modpack.lock = lock(entry("P1"), entry("P2"))
    builds = builds_part(client.post(f"/modpacks/{BUNDLE_ID}/{route}", headers=HTMX, data=data).text)
    assert "No quedan mods por decidir" in builds and ">Crear build<" in builds
    assert "Decidir y crear build…" not in builds and "data-ready" in builds


def test_the_page_does_not_highlight_crear_build_by_itself() -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(lock(entry("P1")))
    assert "No quedan mods por decidir" not in client.get(f"/modpacks/{BUNDLE_ID}").text


def test_a_new_build_offers_simulating_on_linked_servers() -> None:
    client, modpack, _, _, ptero = setup_all(OPERATOR)
    ptero.servers = [PterodactylServer.model_construct(id="srv1", name="Survival", bundle_id=str(BUNDLE_ID),
                                                       is_suspended=False, status=None),
                     PterodactylServer.model_construct(id="srv2", name="Otro", bundle_id="x", is_suspended=False, status=None)]
    modpack.builds[0].created_at = datetime.now(timezone.utc) - timedelta(seconds=20)  # its job just made it
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    # The build it offers is the new one, by number (O9 D3).
    assert "Simular instalación del build 2 en Survival" in text and 'href="/servers/srv1?build=2#actualizar"' in text
    assert "Otro" not in text
    modpack.builds[0].created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    assert "Simular instalación del build 2 en Survival" not in client.get(f"/modpacks/{BUNDLE_ID}").text


def test_the_pack_page_reads_its_servers_simulations_together_and_its_pack_once() -> None:
    """Review of batch B, minor 5: one read of the simulations for every linked server, and the mod
    list takes the pack the page already read."""
    import main

    client, modpack, _, _, ptero = setup_all(OPERATOR)
    jobs = FakeJobs()
    jobs.simulated = {"srv2": 2}
    main.container.jobService.override(providers.Object(jobs))
    ptero.servers = [PterodactylServer.model_construct(id=f"srv{n}", name=f"S{n}", bundle_id=str(BUNDLE_ID),
                                                       is_suspended=False, status=None) for n in (1, 2, 3)]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert jobs.reads == ["simulated_builds"]
    assert modpack.bundles_given == [modpack.bundle]
    assert "data-onboarding" in text


def test_recommend_build_with_the_actor() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds/2/recommend", headers=HTMX)
    assert response.status_code == 200 and 'id="builds"' in response.text
    assert call(modpack, "build_recommend")[1:] == (str(BUNDLE_ID), 2, EDITOR)


def test_mrpack_downloads_as_an_attachment() -> None:
    client, modpack, _ = setup()
    response = client.get(f"/modpacks/{BUNDLE_ID}/builds/2/mrpack")
    assert response.status_code == 200 and response.content == b"PK-fake"
    assert response.headers["content-disposition"] == 'attachment; filename="Pack-Uno-2.mrpack"'
    assert call(modpack, "build_mrpack")[1:] == (str(BUNDLE_ID), 2)


# Reemplazos globales.

def test_replacements_page_lists_the_rules() -> None:
    client, _, _ = setup()
    response = client.get("/replacements")
    assert response.status_code == 200
    assert "OLDMOD" in response.text and "NEWMOD" in response.text and "DROPPED" in response.text
    assert 'hx-post="/replacements"' in response.text


def test_set_replacement_with_the_actor() -> None:
    client, modpack, _ = setup()
    response = client.post("/replacements", headers=HTMX, data={"project_id": "OLDMOD", "replacement": "NEWMOD"})
    assert response.status_code == 200 and 'id="replacements"' in response.text
    assert call(modpack, "replacement_set")[1:] == ("OLDMOD", "NEWMOD", EDITOR)


def test_an_empty_replacement_drops_the_mod() -> None:
    client, modpack, _ = setup()
    client.post("/replacements", headers=HTMX, data={"project_id": "OLDMOD", "replacement": " "})
    assert call(modpack, "replacement_set")[1:] == ("OLDMOD", None, EDITOR)


def test_delete_replacement_with_the_actor() -> None:
    client, modpack, _ = setup()
    response = client.delete("/replacements/OLDMOD", headers=HTMX)
    assert response.status_code == 200 and 'id="replacements"' in response.text
    assert call(modpack, "replacement_delete")[1:] == ("OLDMOD", EDITOR)


def test_operator_may_use_the_pages_too() -> None:
    client, _, _ = setup(OPERATOR)
    assert client.get("/modpacks").status_code == 200


# ModrinthService.search y proyectos por id.

class FakeSearchApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[list[str]], int]] = []

    async def search(self, query: str, facets: list[list[str]], limit: int = 5) -> list[dict[str, Any]]:
        self.calls.append((query, facets, limit))
        return [{"project_id": "P"}]


class NoSearchCache:
    """A catalog that has no stored search: every search reaches the API double."""

    async def search_get(self, key: str, since: Any) -> None:
        return None

    async def search_save(self, key: str, hits: Any, when: Any) -> None:
        return None


def test_service_search_builds_facets_from_the_target() -> None:
    api = FakeSearchApi()
    service = ModrinthService(api, NoSearchCache())  # type: ignore[arg-type]
    assert asyncio.run(service.search("sodium", "1.21.1", "quilt", 7)) == [{"project_id": "P"}]
    [(query, facets, limit)] = api.calls
    assert (query, limit) == ("sodium", 7)
    assert facets[0] == ["project_type:mod", "project_type:shader", "project_type:resourcepack", "project_type:datapack"]
    assert facets[1] == ["versions:1.21.1"]
    # Quilt carga mods de Fabric; los shaders y packs tienen loaders propios: no se excluyen.
    assert {"categories:quilt", "categories:fabric", "categories:iris", "categories:datapack"} <= set(facets[2])


def test_service_search_without_a_target_has_only_the_types() -> None:
    api = FakeSearchApi()
    asyncio.run(ModrinthService(api, NoSearchCache()).search("x", None, None))  # type: ignore[arg-type]
    assert len(api.calls[0][1]) == 1


# Decisiones atómicas (base real).

def _stored_bundle() -> ModpackBundle:
    return ModpackBundle(name="pack", description="", version="1.21.1", loader=MinecraftLoaders.fabric,
                         server_decisions={"OLD": True})


@pytest.mark.network
def test_concurrent_decisions_both_survive(mongo: MongoRunner) -> None:
    from src.database import ModpackDatabase

    async def scenario() -> dict[str, bool]:
        database = ModpackDatabase()
        created = await database.bundle_insert(_stored_bundle())
        bundle_id = str(created.id)
        await asyncio.gather(*(database.bundle_decision_set(bundle_id, f"P{i}", i % 2 == 0) for i in range(20)))
        return (await database.bundle_get(bundle_id)).server_decisions

    assert mongo(scenario) == {"OLD": True, **{f"P{i}": i % 2 == 0 for i in range(20)}}


@pytest.mark.network
def test_decision_set_records_the_event_and_refuses_bad_ids(mongo: MongoRunner) -> None:
    from fastapi import HTTPException

    from src.database import ModpackDatabase
    from tests._actors import RecordingAudit

    async def scenario() -> tuple[Any, Any, dict[str, bool], int]:
        database, audit = ModpackDatabase(), RecordingAudit()
        service = ModpackService(database, None, None, None, audit)  # type: ignore[arg-type]
        created = await database.bundle_insert(_stored_bundle())
        bundle_id = str(created.id)
        await service.decision_set(bundle_id, "P1", False, actor=EDITOR)
        for bad in ("a.b", "$x", ""):
            with pytest.raises(HTTPException) as refused:
                await service.decision_set(bundle_id, bad, True, actor=EDITOR)
            assert refused.value.status_code == 422
        with pytest.raises(HTTPException) as missing:
            await service.decision_set(str(PydanticObjectId()), "P1", True, actor=EDITOR)
        assert missing.value.status_code == 404
        stored = (await database.bundle_get(bundle_id)).server_decisions
        return audit.events[0][:3], audit.events[0][4], stored, len(audit.events)

    (action, target, details), who, stored, count = mongo(scenario)
    assert (action, target.kind, details, who, count) == ("bundle.decision", "bundle", {"project_id": "P1", "server": False}, EDITOR.ref(), 1)
    assert stored == {"OLD": True, "P1": False}


@pytest.mark.network
def test_bulk_decisions_write_every_key_atomically(mongo: MongoRunner) -> None:
    from src.database import ModpackDatabase

    async def scenario() -> dict[str, bool]:
        database = ModpackDatabase()
        created = await database.bundle_insert(_stored_bundle())
        bundle_id = str(created.id)
        await database.bundle_decision_set(bundle_id, "KEPT", False)
        updated = await database.bundle_decisions_set(bundle_id, {"P1": True, "P2": False, "OLD": False})
        assert updated.server_decisions == (await database.bundle_get(bundle_id)).server_decisions
        return updated.server_decisions

    assert mongo(scenario) == {"OLD": False, "KEPT": False, "P1": True, "P2": False}


@pytest.mark.network
def test_bulk_with_one_bad_id_writes_nothing(mongo: MongoRunner) -> None:
    from fastapi import HTTPException

    from src.database import ModpackDatabase
    from tests._actors import RecordingAudit

    async def scenario() -> tuple[list[int], dict[str, bool], Any, int]:
        database, audit = ModpackDatabase(), RecordingAudit()
        service = ModpackService(database, None, None, None, audit)  # type: ignore[arg-type]
        created = await database.bundle_insert(_stored_bundle())
        bundle_id = str(created.id)
        statuses = []
        for bad in ({"P1": True, "a.b": False}, {"P1": True, "$x": False}, {"": True}, {}):
            with pytest.raises(HTTPException) as refused:
                await service.decisions_set(bundle_id, bad, actor=EDITOR)
            statuses.append(refused.value.status_code)
        with pytest.raises(HTTPException) as missing:
            await service.decisions_set(str(PydanticObjectId()), {"P1": True}, actor=EDITOR)
        statuses.append(missing.value.status_code)
        assert len(audit.events) == 0
        await service.decisions_set(bundle_id, {"P1": True, "P2": False}, actor=EDITOR)
        stored = (await database.bundle_get(bundle_id)).server_decisions
        return statuses, stored, audit.events[0][:3], len(audit.events)

    statuses, stored, (action, target, details), count = mongo(scenario)
    assert statuses == [422, 422, 422, 422, 404]
    assert stored == {"OLD": True, "P1": True, "P2": False}
    assert (action, target.kind, details, count) == ("bundle.decision", "bundle", {"decisions": {"P1": True, "P2": False}}, 1)


# Detalle (a): archivos locales, canales y pines.

def test_detail_shows_local_files_channels_and_pins() -> None:
    client, _, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert 'id="local-files"' in text and "custom.jar" in text and "1,5 KiB" in text and "ab12cd34ef56" in text
    assert "ab12cd34ef560" not in text
    assert 'id="channels"' in text and f'hx-post="/modpacks/{BUNDLE_ID}/channels"' in text
    assert re.search(r'name="channel" value="release"[^>]*checked', text)
    assert not re.search(r'name="channel" value="beta"[^>]*checked', text)
    assert "fijado" in card(text, "P1") and 'name="previous" value="ver-p1"' in card(text, "P1")
    assert "fijado" not in card(text, "P2") and "ver-p1" not in visible(text)


def test_pins_without_a_row_are_listed_and_can_be_removed() -> None:
    """A pin on a mod that left the pack has no card: a line names it, by title, with its own
    "Quitar fijación". A pinned dependency keeps it in its card."""
    client, modpack, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "Fijaciones de mods que ya no están" not in text
    modpack.bundle.pins = {"P1": "ver-p1", "GONE": "v-gone", "D1": "v-d1"}
    modpack.titles.update({"GONE": "Viejo", "D1": "Lib"})
    modpack.saved = resolution(lock(entry("P1"), entry("P2"), entry("D1")))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    orphans = text.split("Fijaciones de mods que ya no están")[1]
    assert "Viejo" in orphans and f'hx-delete="/modpacks/{BUNDLE_ID}/pins/GONE"' in orphans
    # The dependency's card unpins it from its «Versión» block (O9 D4), with «Deshacer».
    assert "pins/D1" not in orphans and 'name="previous" value="v-d1"' in card(text, "D1")


def test_detail_without_local_files_says_so() -> None:
    client, modpack, _ = setup()
    modpack.bundle.local_files = []
    assert "Archivos locales: ninguno" in client.get(f"/modpacks/{BUNDLE_ID}").text


def test_channels_are_saved_through_the_service_with_the_actor() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/channels", headers=HTMX, data={"channel": ["release", "beta"]})
    assert response.status_code == 200 and 'id="channels"' in response.text
    _, bundle_id, patch, who = call(modpack, "bundle_update")
    assert (bundle_id, patch.channels, who) == (str(BUNDLE_ID), ["release", "beta"], EDITOR)


def test_channels_need_at_least_one_and_only_known_ones() -> None:
    client, modpack, _ = setup()
    assert client.post(f"/modpacks/{BUNDLE_ID}/channels", headers=HTMX, data={}).status_code == 422
    assert client.post(f"/modpacks/{BUNDLE_ID}/channels", headers=HTMX, data={"channel": "nightly"}).status_code == 422
    assert modpack.calls == []


def test_pin_add_and_remove_call_the_service_with_the_actor() -> None:
    client, modpack, _ = setup()
    added = client.post(f"/modpacks/{BUNDLE_ID}/pins", headers=HTMX, data={"project_id": "P2", "version_id": "ver-p2"})
    assert added.status_code == 200 and 'id="mods"' in added.text and "fijado" in card(added.text, "P2")
    assert call(modpack, "pin_set")[1:] == (str(BUNDLE_ID), "P2", "ver-p2", EDITOR)
    removed = client.request("DELETE", f"/modpacks/{BUNDLE_ID}/pins/P1", headers=HTMX)
    assert removed.status_code == 200 and 'id="mods"' in removed.text and "fijado" not in card(removed.text, "P1")
    assert call(modpack, "pin_unset")[1:] == (str(BUNDLE_ID), "P1", EDITOR)


def test_pin_with_a_bad_id_is_refused_before_the_service() -> None:
    client, modpack, _ = setup()
    for bad in ("a.b", "$set", "a/b"):
        response = client.post(f"/modpacks/{BUNDLE_ID}/pins", headers=HTMX, data={"project_id": bad, "version_id": "v1"})
        assert response.status_code == 422
        response = client.post(f"/modpacks/{BUNDLE_ID}/pins", headers=HTMX, data={"project_id": "P1", "version_id": bad})
        assert response.status_code == 422
    assert modpack.calls == []


@pytest.mark.network
def test_pins_are_written_one_key_at_a_time(mongo: MongoRunner) -> None:
    from src.database import ModpackDatabase

    async def scenario() -> dict[str, str]:
        database = ModpackDatabase()
        created = await database.bundle_insert(_stored_bundle())
        bundle_id = str(created.id)
        await asyncio.gather(*(database.bundle_pin_set(bundle_id, f"P{i}", f"v{i}") for i in range(20)))
        await database.bundle_pin_unset(bundle_id, "P0")
        updated = await database.bundle_pin_unset(bundle_id, "NEVER")
        assert updated.pins == (await database.bundle_get(bundle_id)).pins
        for call_ in (database.bundle_pin_set(str(PydanticObjectId()), "P1", "v"), database.bundle_pin_unset(str(PydanticObjectId()), "P1")):
            with pytest.raises(Exception) as missing:
                await call_
            assert getattr(missing.value, "status_code") == 404
        return updated.pins

    assert mongo(scenario) == {f"P{i}": f"v{i}" for i in range(1, 20)}


@pytest.mark.network
def test_pin_with_bad_id_or_foreign_version_writes_nothing(mongo: MongoRunner) -> None:
    from typing import cast

    from fastapi import HTTPException

    from src.database import ModpackDatabase, ModrinthDatabase
    from tests._actors import RecordingAudit
    from tests._modrinth import IRIS, SODIUM, RecordedModrinthAPI

    async def scenario() -> tuple[list[tuple[str, int]], dict[str, str], int, list[Any], dict[str, str]]:
        database, audit, api = ModpackDatabase(), RecordingAudit(), RecordedModrinthAPI()
        service = ModpackService(database, ModrinthDatabase(), cast(Any, api), None, audit)  # type: ignore[arg-type]
        created = await database.bundle_insert(_stored_bundle())
        bundle_id = str(created.id)
        await service.project_add(bundle_id, ["sodium"], ProjectOrigin.admin, actor=EDITOR)
        audit.events.clear()
        api.calls.clear()
        refused: list[tuple[str, int]] = []
        attempts = [(SODIUM, "a.b"), ("a.b", "v4PSXean"), ("$x", "v1"), (SODIUM, ""),   # ids malos
                    (IRIS, "vTN4NRGW"),        # el proyecto no está en el modpack
                    (SODIUM, "vTN4NRGW"),      # la versión es de otro proyecto
                    (SODIUM, "nope")]          # Modrinth no la conoce
        for project_id, version_id in attempts:
            with pytest.raises(HTTPException) as error:
                await service.pin_set(bundle_id, project_id, version_id, actor=EDITOR)
            refused.append((str(error.value.detail), error.value.status_code))
        wrote = (await database.bundle_get(bundle_id)).pins
        events = len(audit.events)
        await service.pin_set(bundle_id, SODIUM, "v4PSXean", actor=EDITOR)
        # una versión ya en el catálogo no vuelve a pedirse a Modrinth
        await ModrinthDatabase().version_upsert_many([await api.version_get("v4PSXean")])
        api.calls.clear()
        await service.pin_set(bundle_id, SODIUM, "v4PSXean", actor=EDITOR)
        return refused, wrote, events, api.calls, (await database.bundle_get(bundle_id)).pins

    refused, wrote, events, calls, pins = mongo(scenario)
    assert [status for _, status in refused] == [422] * 7
    assert "La versión no es de ese proyecto" in refused[5][0]
    assert wrote == {} and events == 0
    assert calls == []
    assert pins == {"AANobbMI": "v4PSXean"}


# Comparar dos builds (N3).

def preview(changelog: Optional[str] = "Arregla un crash", failed: bool = False) -> Any:
    from src.library.changes import Change, ChangeSet
    from src.services.modpack import ChangePreview

    changes = ChangeSet(added=[Change("P3", None, "v-new")], removed=[Change("P2", "v-p2", None)],
                        changed=[Change("P1", "v-old", "v-p1")], unchanged=4)
    return ChangePreview(changes=changes, changelogs={"v-p1": changelog, "v-new": None},
                         failed={"v-p1"} if failed else set())


def test_the_builds_list_offers_to_compare_two_builds() -> None:
    client, _, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert f'action="/modpacks/{BUNDLE_ID}/builds/compare"' in text and 'method="get"' in text
    assert 'name="de"' in text and 'name="a"' in text


def test_compare_shows_the_four_sections_with_titles_and_changelogs() -> None:
    client, modpack, _ = setup()

    async def compare(bundle_id: str, de: int, a: int) -> Any:
        modpack.calls.append(("compare_builds", bundle_id, de, a))
        return preview()

    modpack.compare_builds = compare  # type: ignore[attr-defined]
    response = client.get(f"/modpacks/{BUNDLE_ID}/builds/compare", params={"de": 1, "a": 2})
    assert response.status_code == 200 and call(modpack, "compare_builds") == ("compare_builds", str(BUNDLE_ID), 1, 2)
    text = response.text
    for heading in ("Agregados", "Quitados", "Cambiados", "Sin cambios"):
        assert heading in text
    assert "Sodium" in text and "v-old" in text and "v-p1" in text
    assert "Arregla un crash" in text and "whitespace-pre-wrap" in text
    assert "sin changelog" in text  # P3: Modrinth no tiene texto
    assert "https://modrinth.com/project/P1/changelog" in text


def test_a_changelog_links_its_urls_and_nothing_else() -> None:
    # F29 (d-7985ee-58228f).
    client, modpack, _ = setup()

    async def compare(bundle_id: str, de: int, a: int) -> Any:
        return preview("Ver https://github.com/x/y/pull/9 y **negrita**")

    modpack.compare_builds = compare  # type: ignore[attr-defined]
    text = client.get(f"/modpacks/{BUNDLE_ID}/builds/compare", params={"de": 1, "a": 2}).text
    assert '<a href="https://github.com/x/y/pull/9" rel="noopener noreferrer nofollow" target="_blank"' in text
    assert "**negrita**" in text


def test_compare_marks_a_changelog_modrinth_could_not_give() -> None:
    client, modpack, _ = setup()

    async def compare(bundle_id: str, de: int, a: int) -> Any:
        return preview(None, failed=True)

    modpack.compare_builds = compare  # type: ignore[attr-defined]
    text = client.get(f"/modpacks/{BUNDLE_ID}/builds/compare", params={"de": 1, "a": 2}).text
    assert "changelog no disponible" in text


def test_a_huge_changelog_with_html_is_cut_and_escaped() -> None:
    client, modpack, _ = setup()
    evil = "<script>alert(1)</script>" + "a" * 10_000 + "FIN"

    async def compare(bundle_id: str, de: int, a: int) -> Any:
        return preview(evil)

    modpack.compare_builds = compare  # type: ignore[attr-defined]
    text = client.get(f"/modpacks/{BUNDLE_ID}/builds/compare", params={"de": 1, "a": 2}).text
    assert "<script>alert" not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "FIN" not in text and text.count("a" * 100) <= 40 and "a" * 4000 not in text


def test_compare_of_a_missing_build_is_a_notice() -> None:
    client, modpack, _ = setup()

    async def compare(bundle_id: str, de: int, a: int) -> Any:
        raise HTTP_404_NOT_FOUND("Bundle has no build 9")

    modpack.compare_builds = compare  # type: ignore[attr-defined]
    response = client.get(f"/modpacks/{BUNDLE_ID}/builds/compare", params={"de": 1, "a": 9})
    assert response.status_code == 404 and "no build 9" in response.text and "<nav" in response.text


def test_compare_needs_both_builds() -> None:
    client, _, _ = setup()
    assert client.get(f"/modpacks/{BUNDLE_ID}/builds/compare", params={"de": 1}).status_code == 422


# La lista de mods con su ficha, la resolución guardada y la disposición (spec §1 y §5).

def check(*updates: AvailableUpdate) -> UpdateCheck:
    return UpdateCheck.model_construct(id=PydanticObjectId(), bundle_id=BUNDLE_ID, build_number=2, checked_at=T0,
                                       updates=list(updates), error=None, notified_digest=None)


def test_mods_list_shows_card_data_not_ids() -> None:
    client, modpack, _, checks, _ = setup_all()
    checks.check = check()
    modpack.bundle = bundle([PackProject(project_id="AANobbMI", origin=ProjectOrigin.admin)])
    modpack.rows = [
        row("AANobbMI", "Sodium", "admin", icon_url="https://cdn.modrinth.com/data/AANobbMI/icon.png",
            downloads=1234567, side="exclude", build_version="0.8.13", newer_version="0.8.14", pinned=True,
            pinned_version="ver-813"),
        row("P9", "Fabric API", None, required_by=["Sodium"], side="include", build_version="0.100.0"),
        row("P8", "Mod Menu", "pack", side="admin")]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert modpack.checks_seen == [checks.check]  # la última revisión llega a la lista
    sodium = card(text, "AANobbMI")
    assert '<img src="https://cdn.modrinth.com/data/AANobbMI/icon.png"' in sodium
    for shown in ("Sodium", "agregado", "Desactivado", "0.8.13 → 0.8.14", "1,2 M descargas", "fijado"):
        assert shown in sodium, shown
    assert 'title="0.8.13 → 0.8.14"' in sodium and "truncate" in sodium  # versión larga con elipsis
    dependency = card(text, "P9")
    assert "dependencia de Sodium" in dependency and "En el servidor" in dependency and "0.100.0" in dependency
    assert "del pack" in card(text, "P8") and "Por decidir" in card(text, "P8")
    # Tester feedback (2026-10-08): "Solo cliente" read as a property of the mod, not a decision.
    assert "Solo cliente" not in text and "Sin decidir" not in text
    decide = row_select(text, "P8", "server")
    assert selected(decide) == "Por decidir" and '<option value="no">Desactivado</option>' in decide
    assert "AANobbMI" not in visible(text)


def test_icon_off_the_cdn_is_a_placeholder() -> None:
    client, modpack, _ = setup()
    modpack.rows = [row("P1", "Plain", icon_url="http://cdn.modrinth.com/x.png"),
                    row("P2", "Evil", icon_url="https://evil.example/x.png"),
                    row("P3", "Sneaky", icon_url="https://user@cdn.modrinth.com/x.png"),
                    row("P4", "None", icon_url=None)]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "<img" not in text and "evil.example" not in text and "cdn.modrinth.com" not in text
    for project_id in ("P1", "P2", "P3", "P4"):
        assert 'aria-hidden="true"' in card(text, project_id)  # el cuadro neutro


def test_a_card_with_only_a_beta_for_the_pack_says_solo_beta_not_a_check() -> None:
    client, modpack, _ = setup()
    modpack.rows = [row("P1", "Sodium", game_versions=["1.21.1"], supports_loader=True, supports_version=True,
                        version_channel="beta")]
    sodium = card(client.get(f"/modpacks/{BUNDLE_ID}").text, "P1")
    assert "1.21.1 · solo beta" in sodium and "1.21.1 ✓" not in sodium
    assert re.search(r'<span class="[^"]*text-warning[^"]*">1\.21\.1 · solo beta</span>', sodium)


def test_card_has_support_links_and_lazy_version_picker() -> None:
    client, modpack, _ = setup()
    modpack.rows = [
        row("P1", "Sodium", description="Rendimiento", categories=["optimization"], loaders=["fabric", "quilt"],
            game_versions=["1.20.1", "1.21"], supports_loader=True, supports_version=False,
            source_url="https://github.com/x/sodium", issues_url="https://github.com/x/sodium/issues"),
        row("P2", "Lithium", source_url="javascript:alert(1)", issues_url="http://insecure.example/issues")]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    sodium = card(text, "P1")
    assert "<details" in sodium and "<summary" in sodium
    for shown in ("Rendimiento", "optimization", "Loaders", "fabric ✓", "no soporta 1.21.1", "1.20.1"):
        assert shown in sodium, shown
    assert 'href="https://modrinth.com/project/P1"' in sodium
    assert 'href="https://github.com/x/sodium"' in sodium and 'href="https://github.com/x/sodium/issues"' in sodium
    assert re.search(rf'<details[^>]*hx-get="/modpacks/{BUNDLE_ID}/projects/P1/versions"[^>]*hx-trigger="toggle once"', sodium)
    assert "javascript:" not in text and "insecure.example" not in text
    # La fijación a mano por id de versión desapareció: solo el selector.
    assert not re.search(r'<input[^>]*name="version_id"', text)


def test_card_removes_only_what_the_admin_added() -> None:
    client, _, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert f'hx-delete="/modpacks/{BUNDLE_ID}/projects/P2"' in card(text, "P2")
    assert f'hx-delete="/modpacks/{BUNDLE_ID}/projects/P1"' not in text


def test_versions_fragment_posts_a_pin() -> None:
    client, modpack, _ = setup()
    response = client.get(f"/modpacks/{BUNDLE_ID}/projects/P1/versions", headers=HTMX)
    assert response.status_code == 200 and ("project_versions", str(BUNDLE_ID), "P1") in modpack.calls
    text = response.text
    assert re.search(r'<select[^>]*name="version_id"', text)
    assert '<option value="ver-new">0.8.14 (release, 2026-10-01)</option>' in text
    assert "0.9.0-beta.1 (beta, 2026-10-02)" in text
    assert f'hx-post="/modpacks/{BUNDLE_ID}/pins"' in text and 'hx-target="#mods"' in text
    assert '<input type="hidden" name="project_id" value="P1">' in text


def test_versions_fragment_says_when_there_is_none_or_modrinth_fails() -> None:
    client, modpack, _ = setup()
    modpack.versions = []
    text = client.get(f"/modpacks/{BUNDLE_ID}/projects/P1/versions", headers=HTMX).text
    assert "<select" not in text and "no tiene versiones" in text
    for down in (UpstreamError("Modrinth", 504, raw="timeout"), TimeoutError()):
        modpack.versions_error = down
        response = client.get(f"/modpacks/{BUNDLE_ID}/projects/P1/versions", headers=HTMX)
        assert response.status_code == 200 and "Modrinth no respondió" in response.text
        assert f'hx-get="/modpacks/{BUNDLE_ID}/projects/P1/versions"' in response.text  # reintentar


def test_versions_fragment_does_not_blame_modrinth_for_our_own_bug() -> None:
    client, modpack, _ = setup()
    modpack.versions_error = RuntimeError("a bug of ours")
    with pytest.raises(RuntimeError):  # the panel's 500 handling, not "Modrinth no respondió"
        client.get(f"/modpacks/{BUNDLE_ID}/projects/P1/versions", headers=HTMX)


def test_saved_resolution_shows_with_time_and_stale_notice() -> None:
    client, modpack, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "Todavía no se resolvió" in text and "Resuelto" not in text
    modpack.saved = resolution(lock(entry("P1"), entry("P2")))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "Resuelto" in text and '<time datetime="2026-10-01T12:00:00+00:00"' in text and "2 archivos" in text
    assert "el pack cambió desde la última resolución" not in text
    modpack.stale = True
    assert "el pack cambió desde la última resolución" in client.get(f"/modpacks/{BUNDLE_ID}").text


def test_suggestions_have_titles_and_an_add_button() -> None:
    # The titles come with the mod list (one Modrinth fill per render): the route fills nothing more.
    client, modpack, modrinth = setup()
    modpack.titles.update(P7="Mod Menu", P6="Roto")
    modpack.saved = resolution(lock(entry("P1"), suggestions=[Suggestion(project_id="P7", requested_by=["P1"])],
                                    problems=[Problem(kind="unresolvable", project_id="P6", detail="sin versión")]))
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "sugerencias"}).text
    suggestions = view(text, "sugerencias")
    # Tester feedback (2026-10-08): it was not clear what the suggestions are based on.
    shown = " ".join(visible(suggestions).split())
    assert "Mods que otros del pack declaran como dependencia opcional en Modrinth, e Iris si el pack tiene shaders sin él." in shown
    assert "Mod Menu — opcional para Sodium" in shown and "sugerida por" not in shown
    assert f'hx-post="/modpacks/{BUNDLE_ID}/projects"' in suggestions and 'name="ref" value="P7"' in suggestions
    assert "P7" not in visible(text)
    # A4 (owner, 2026-10-13): the suggestions are a tab of their own, no longer in every view.
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "problemas"}).text
    assert "Roto" in text and "sin versión" in text and 'name="ref" value="P7"' not in text
    assert "P7" not in visible(text) and "P6" not in visible(text)
    assert modrinth.filled == []


def test_suggestions_are_a_fourth_tab_with_their_count() -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(lock(entry("P1"), suggestions=[
        Suggestion(project_id="P7", requested_by=["P1"]), Suggestion(project_id="P8", requested_by=["P1"])]))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    link, url = tab(text, "sugerencias"), f"/modpacks/{BUNDLE_ID}?vista=sugerencias"
    assert f'hx-get="{url}"' in link and said(link) == "Sugerencias 2" and "bg-info/" in link
    assert text.index('data-tab="problemas"') < text.index('data-tab="sugerencias"')
    modpack.saved = resolution(lock(entry("P1")))
    link = tab(client.get(f"/modpacks/{BUNDLE_ID}").text, "sugerencias")
    assert said(link) == "Sugerencias 0" and "bg-info/" not in link


def test_the_blue_box_is_gone_and_the_summary_links_the_suggestions() -> None:
    client, modpack, _ = setup()
    modpack.titles.update(P7="Mod Menu")
    modpack.saved = resolution(lock(entry("P1"), entry("P2", "admin"),
                                    suggestions=[Suggestion(project_id="P7", requested_by=["P1"])]))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "border-info-line" not in text and 'name="ref" value="P7"' not in text
    summary = text[text.index("data-pending-summary"):]
    summary = summary[:summary.index("</p>")]
    assert said(summary[summary.index(">") + 1:]) == "1 por decidir · 1 sugerencia"
    assert f'href="/modpacks/{BUNDLE_ID}?vista=sugerencias"' in summary


def test_without_a_current_resolution_the_suggestions_tab_says_it_waits_for_modrinth() -> None:
    client, modpack, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "sugerencias"}).text
    assert "Lo pendiente se ve al traer de Modrinth lo que falta" in visible(view(text, "sugerencias"))


def test_add_from_search_marks_the_hit_out_of_band() -> None:
    client, modpack, modrinth = setup()
    hits = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "iris"}, headers=HTMX).text
    assert 'id="hit-P3"' in hits and 'id="hit-add-P3"' in hits and 'hx-target="#mods"' in hits
    modpack.titles["P3"] = "Iris"
    response = client.post(f"/modpacks/{BUNDLE_ID}/projects", headers={**HTMX, "HX-Trigger": "hit-add-P3"},
                           data={"ref": "P3"})
    assert response.status_code == 200 and 'id="mods"' in response.text
    oob = response.text[response.text.index('id="hit-P3"') - 5:]
    assert re.match(r'<div id="hit-P3" hx-swap-oob="true"', oob) and "Ya está" in oob


def row_select(text: str, project_id: str, name: str) -> str:
    """The form of one of a row's selects (A1, owner 2026-10-13), up to its closing tag: outside the
    <summary>, after the mod's <details>."""
    item = text[text.index(f'id="mod-row-{project_id}"'):]
    item = item[:item.index("</li>")]
    assert item.index(f'name="{name}"') > item.index("</summary>"), "a select inside the summary opens the card"
    start = item.rindex("<form", 0, item.index(f'<select name="{name}"'))
    return item[start:item.index("</form>", start)]


def selected(select: str) -> str:
    """The visible text of the selected option."""
    found = re.search(r"<option[^>]*\bselected\b[^>]*>([^<]*)</option>", select)
    assert found is not None, select
    return found.group(1).strip()


def test_each_row_has_a_side_select_with_its_value_selected() -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(lock(entry("P1"), entry("P2", "admin"), entry("P3", "exclude")))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    for project_id, shown in (("P1", "En el servidor"), ("P2", "Por decidir"), ("P3", "Desactivado")):
        form = row_select(text, project_id, "server")
        assert f'hx-post="/modpacks/{BUNDLE_ID}/decisions"' in form and 'hx-trigger="change"' in form
        assert 'hx-target="#mods"' in form and f'name="project_id" value="{project_id}"' in form
        assert selected(form) == shown
    assert "Por decidir" not in row_select(text, "P1", "server")  # only while undecided
    assert 'data-saved="yes"' in row_select(text, "P1", "server") and 'data-saved=""' in row_select(text, "P2", "server")


def test_choosing_a_side_decides_and_answers_mods() -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(lock(entry("P1"), entry("P2", "admin")))
    response = client.post(f"/modpacks/{BUNDLE_ID}/decisions", headers=HTMX, data={"project_id": "P2", "server": "yes"})
    assert response.status_code == 200 and response.text.lstrip().startswith('<div id="mods"')
    assert call(modpack, "decision_set")[1:] == (str(BUNDLE_ID), "P2", True, EDITOR)


def test_the_side_buttons_left_the_card() -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(lock(entry("P1"), entry("P2", "admin")))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "¿Va en el servidor?" not in text and 'name="server" value="yes"' not in card(text, "P2")


def test_each_row_has_a_channel_select_grey_without_an_exception() -> None:
    client, modpack, _ = setup()
    modpack.bundle.channel_exceptions = {"P2": "beta"}
    modpack.saved = resolution(lock(entry("P1"), entry("P2")))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    plain, beta = row_select(text, "P1", "channel"), row_select(text, "P2", "channel")
    assert f'hx-post="/modpacks/{BUNDLE_ID}/channel-exceptions"' in plain and 'hx-trigger="change"' in plain
    assert selected(plain) == "canal del pack" and "text-muted" in plain and 'data-saved=""' in plain
    assert selected(beta) == "acepta beta" and "text-muted" not in beta and 'data-saved="beta"' in beta
    assert [o for o in re.findall(r'<option value="([a-z]*)"', plain)] == ["", "beta", "alpha"]


def test_choosing_beta_sets_the_exception_and_the_pack_channel_removes_it() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/channel-exceptions", headers=HTMX,
                           data={"project_id": "P2", "channel": "beta"})
    assert response.status_code == 200 and response.text.lstrip().startswith('<div id="mods"')
    assert [c[0] for c in modpack.calls] == ["channel_exception_set", "resolve_current"]
    modpack.calls.clear()
    response = client.post(f"/modpacks/{BUNDLE_ID}/channel-exceptions", headers=HTMX,
                           data={"project_id": "P2", "channel": ""})
    assert response.status_code == 200 and response.text.lstrip().startswith('<div id="mods"')
    assert [c[0] for c in modpack.calls] == ["channel_exception_unset", "resolve_current"]
    assert call(modpack, "channel_exception_unset")[1:] == (str(BUNDLE_ID), "P2", EDITOR)


def test_the_exception_state_is_written_under_its_select() -> None:
    client, modpack, _ = setup()
    modpack.bundle.channel_exceptions = {"P1": "beta", "P2": "alpha"}
    used = lock(entry("P1"), entry("P2"))
    used.excepted = ["P1"]
    modpack.saved = resolution(used)
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    first = text[text.index('id="mod-row-P1"'):text.index('id="mod-row-P2"')]
    second = text[text.index('id="mod-row-P2"'):]
    assert "en uso: no hay una estable más nueva que la instalada" in said(first)
    assert "sin efecto: ya hay versión en los canales del pack" in said(second[:second.index("</li>")])
    modpack.stale = True
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "en uso" not in visible(text) and "sin efecto" not in visible(text)


def option(version_id: str, number: str) -> VersionOption:
    return VersionOption(version_id, number, "release", datetime(2026, 10, 1, tzinfo=timezone.utc))


def pin_rows(modpack: FakeModpack) -> None:
    """Sodium pinned to 0.8.13 with two versions to offer; Lithium unpinned with one; Iris without a
    list in the catalog."""
    modpack.rows = [
        row("P1", "Sodium", "admin", pinned=True, pinned_version="ver-p1",
            pin_options=[option("ver-new", "0.8.14"), option("ver-p1", "0.8.13")]),
        row("P2", "Lithium", "admin", pin_options=[option("l-1", "1.0")]),
        row("P3", "Iris", "admin")]


def row_line(text: str, project_id: str) -> str:
    """A row's line under its <summary>: its selects and marks, without the card."""
    item = text[text.index(f'id="mod-row-{project_id}"'):]
    item = item[:item.index("</li>")]
    return item[item.index("</details>"):]


def test_the_row_marks_the_pin_and_the_card_is_where_it_changes() -> None:
    """O9 D4 (owner, 2026-10-26): one place to pin, the card; the row only says it is pinned."""
    client, modpack, _ = setup()
    pin_rows(modpack)
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    pinned, free = row_line(text, "P1"), row_line(text, "P2")
    assert 'name="version_id"' not in pinned and 'name="version_id"' not in free
    assert "fijado 0.8.13" in visible(pinned) and "fijado" not in visible(free)


def test_the_cards_version_block_unpins_with_undo() -> None:
    client, modpack, _ = setup()
    pin_rows(modpack)
    block = card(client.get(f"/modpacks/{BUNDLE_ID}").text, "P1")
    block = block[block.index("data-version-block"):]
    assert "Versión" in visible(block[:400])
    unpin = block[block.rindex("<form", 0, block.index("Quitar fijación")):]
    unpin = unpin[:unpin.index("</form>")]
    assert f'hx-post="/modpacks/{BUNDLE_ID}/pins"' in unpin and 'name="version_id" value=""' in unpin
    assert 'name="from" value="fila"' in unpin and 'name="previous" value="ver-p1"' in unpin and "hx-delete" not in unpin


def test_another_version_is_used_from_the_card_with_undo() -> None:
    client, modpack, _ = setup()
    modpack.bundle.pins = {"P1": "ver-p1"}
    modpack.versions = [option("ver-new", "0.8.14"), option("ver-p1", "0.8.13")]
    text = client.get(f"/modpacks/{BUNDLE_ID}/projects/P1/versions", headers=HTMX).text
    assert 'name="from" value="fila"' in text and 'name="previous" value="ver-p1"' in text
    assert "Usar esta" in visible(text) and ">Fijar<" not in text


def test_choosing_a_version_pins_it_and_offers_undo() -> None:
    client, modpack, _ = setup()
    pin_rows(modpack)
    response = client.post(f"/modpacks/{BUNDLE_ID}/pins", headers=HTMX,
                           data={"project_id": "P2", "version_id": "l-1", "from": "fila", "previous": ""})
    assert response.status_code == 200 and response.text.lstrip().startswith('<div id="mods"')
    assert call(modpack, "pin_set")[1:3] == (str(BUNDLE_ID), "P2")
    notice = response.text[response.text.index('id="notices"'):]
    assert "Lithium fijado en 1.0" in said(notice)
    undo = notice[notice.index("<form"):notice.index("</form>")]
    assert f'hx-post="/modpacks/{BUNDLE_ID}/pins"' in undo and 'name="version_id" value=""' in undo
    assert 'name="project_id" value="P2"' in undo and 'name="from" value="fila"' in undo and "Deshacer" in undo


def test_choosing_sin_fijar_unpins_and_offers_undo_to_the_old_pin() -> None:
    client, modpack, _ = setup()
    pin_rows(modpack)
    response = client.post(f"/modpacks/{BUNDLE_ID}/pins", headers=HTMX,
                           data={"project_id": "P1", "version_id": "", "from": "fila", "previous": "ver-p1"})
    assert response.status_code == 200 and call(modpack, "pin_unset")[1:] == (str(BUNDLE_ID), "P1", EDITOR)
    notice = response.text[response.text.index('id="notices"'):]
    assert "Sodium sin fijar" in said(notice) and 'name="version_id" value="ver-p1"' in notice


def test_a_pin_from_elsewhere_has_no_undo() -> None:
    client, modpack, _ = setup()
    pin_rows(modpack)
    response = client.post(f"/modpacks/{BUNDLE_ID}/pins", headers=HTMX, data={"project_id": "P2", "version_id": "l-1"})
    assert response.status_code == 200 and "Deshacer" not in response.text


def test_a_downgrade_from_the_row_opens_the_pin_dialog_that_keeps_the_row() -> None:
    client, modpack, _ = setup()
    pin_rows(modpack)
    modpack.downgrade = JEI_DOWN
    response = client.post(f"/modpacks/{BUNDLE_ID}/pins", headers=HTMX,
                           data={"project_id": "P2", "version_id": "ver-51", "from": "fila", "previous": "l-1"})
    assert response.headers["HX-Retarget"] == "#pin-dialog"
    assert 'name="from" value="fila"' in response.text and 'name="previous" value="l-1"' in response.text


def filter_rows(modpack: FakeModpack) -> None:
    modpack.bundle.channel_exceptions = {"P1": "beta", "P3": "alpha"}
    modpack.rows = [row("P1", "Sodium", "admin", side="include"), row("P2", "Lithium", "admin", side="exclude"),
                    row("P3", "Iris", "pack", side="include")]


def bar(text: str) -> str:
    start = text.index("data-mod-filters")
    start = text.rindex("<form", 0, start)
    return text[start:text.index("</form>", start)]


def test_the_filter_bar_shows_each_option_with_its_count() -> None:
    client, modpack, _ = setup()
    filter_rows(modpack)
    form = bar(client.get(f"/modpacks/{BUNDLE_ID}").text)
    assert f'hx-get="/modpacks/{BUNDLE_ID}"' in form and 'hx-push-url="true"' in form
    assert 'name="vista" value="todos"' in form and 'name="nombre"' in form
    assert ">con excepción (2)</option>" in form and ">acepta alpha (1)</option>" in form
    assert ">Desactivado (1)</option>" in form and ">del pack (1)</option>" in form
    assert re.search(r'<option value="decidir"[^>]*text-faint[^>]*>Por decidir \(0\)</option>', form)


def test_filtering_never_replaces_the_name_being_typed() -> None:
    """Final review (2026-10-13): the bar swapped #mods, the name field with it, and what was typed
    while a request was in flight was lost. A filter swaps the list and the four selects (their
    counts), never the name field; a newer request replaces an older one in flight."""
    client, modpack, _ = setup()
    filter_rows(modpack)
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    form = bar(text)
    assert 'hx-select="#mods-list"' in form and 'hx-target="#mods-list"' in form and 'hx-sync="this:replace"' in form
    assert 'hx-select-oob="#f-lado,#f-canal,#f-fijacion,#f-origen,#f-version,#mod-filters-clear"' in form
    listed = text[text.index('id="mods-list"'):]
    assert 'data-project="P1"' in listed and 'name="nombre"' not in listed
    empty = client.get(f"/modpacks/{BUNDLE_ID}", params={"nombre": "zzz"}).text
    assert "Ningún mod coincide" in empty[empty.index('id="mods-list"'):]
    assert 'id="mod-filters-clear"' in text


def test_filtering_answers_mods_and_keeps_the_tab_counts() -> None:
    client, modpack, _ = setup()
    filter_rows(modpack)
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"canal": "excepcion", "lado": "servidor"}).text
    shown = view(text, "todos")
    assert 'data-project="P1"' in shown and 'data-project="P3"' in shown and 'data-project="P2"' not in shown
    assert said(tab(text, "todos")) == "Todos 3"
    assert selected(bar(text)[bar(text).index('name="canal"'):]) == "con excepción (2)"
    # The other lists count with the channel filter: Lithium has no exception.
    assert ">Desactivado (0)</option>" in bar(text)
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"nombre": "iris"}).text
    assert 'data-project="P3"' in view(text, "todos") and 'data-project="P1"' not in view(text, "todos")


def test_an_action_from_a_filtered_list_keeps_its_filters() -> None:
    client, modpack, _ = setup()
    filter_rows(modpack)
    headers = {**HTMX, "HX-Current-URL": f"http://testserver/modpacks/{BUNDLE_ID}?vista=todos&lado=desactivado"}
    response = client.post(f"/modpacks/{BUNDLE_ID}/decisions", headers=headers, data={"project_id": "P2", "server": "yes"})
    assert 'data-project="P2"' in response.text and 'data-project="P1"' not in response.text


def test_the_filter_bar_is_only_on_todos_and_says_when_nothing_matches() -> None:
    client, modpack, _ = setup()
    filter_rows(modpack)
    assert "data-mod-filters" not in client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "problemas"}).text
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"nombre": "zzz"}).text
    assert "Ningún mod coincide con los filtros." in visible(text)


def test_the_channels_section_has_no_exception_list() -> None:
    client, modpack, _ = setup()
    modpack.bundle.channel_exceptions = {"P1": "beta"}
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    section = text[text.index('id="channels-section"'):text.index('id="local-files-section"')]
    assert "Excepciones por mod" not in section and "channel-exceptions" not in section


def test_detail_layout_has_linked_servers_and_renamed_updates_link() -> None:
    client, _, _, _, ptero = setup_all(OPERATOR)
    linked = PterodactylServer.model_construct(id=PydanticObjectId(), name="Survival", node="n1", status=None,
                                               is_suspended=False, bundle_id=str(BUNDLE_ID))
    other = PterodactylServer.model_construct(id=PydanticObjectId(), name="Creativo", node="n1", status=None,
                                              is_suspended=False, bundle_id=str(PydanticObjectId()))
    ptero.servers = [linked, other]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "Servidores vinculados" in text and "Survival" in text and "Creativo" not in text
    assert f'href="/servers/{linked.id}#actualizar"' in text
    assert "Buscar actualizaciones de mods" in text and ">Actualizaciones<" not in text
    assert "xl:grid-cols-3" in text and "xl:col-span-2" in text
    # The builds first, in one column (O9 D1).
    order = [text.index(marker) for marker in ('id="builds-section"', 'id="mods-section"', "Servidores vinculados",
                                               'id="channels-section"', "Archivos locales")]
    assert order == sorted(order)


def test_empty_sections_fold_to_one_line() -> None:
    client, modpack, _ = setup()
    modpack.bundle.local_files = []
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "Archivos locales: ninguno" in text and "sha512" not in text.lower()
    assert "Servidores vinculados: ninguno" in text


# Every mod name opens its card (spec §2, "Macro ui.mod_link"), and a render fills the catalog once.

def mod_link(project_id: str, pack: bool = True) -> str:
    """The start of `ui.mod_link` for a mod; inside a pack's pages it carries the pack."""
    query = f"?pack={BUNDLE_ID}" if pack else ""
    return f'<a href="/mods/{project_id}{query}" hx-get="/mods/{project_id}/panel{query}"'


def test_every_mod_of_the_list_and_who_asked_for_it_opens_its_card() -> None:
    client, modpack, modrinth = setup()
    modpack.bundle.pins = {"GONE": "v-gone"}
    modpack.rows = [row("P1", "Sodium"), row("D1", "Indium", None, required_by=["Sodium"], requested_by=["P1"])]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert mod_link("P1") in card(text, "P1") and mod_link("D1") in card(text, "D1")
    assert mod_link("P1") in card(text, "D1")  # "dependencia de Sodium"
    assert mod_link("GONE") in text  # a pin of a mod that left the pack
    assert modrinth.filled == []  # mod_list filled the catalog: the route does not fill again


def test_problems_suggestions_and_undecided_mods_open_their_cards() -> None:
    client, modpack, _ = setup()
    modpack.titles.update(P7="Mod Menu", P6="Roto")
    modpack.saved = resolution(lock(entry("P1"), entry("P2", "admin"),
                                    suggestions=[Suggestion(project_id="P7", requested_by=["P1"])],
                                    problems=[Problem(kind="unresolvable", project_id="P6", detail="sin versión")]))
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "problemas"}).text
    assert mod_link("P6") in view(text, "problemas")
    suggestions = view(client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "sugerencias"}).text, "sugerencias")
    assert mod_link("P7") in suggestions and mod_link("P1") in suggestions  # sugerida por Sodium
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "decidir"}).text
    assert mod_link("P2") in view(text, "decidir")


def test_the_decide_dialog_names_open_their_cards_with_one_fill() -> None:
    client, modpack, modrinth = setup()
    modpack.blocked = undecided_block("P2", "P3")
    text = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX).text
    assert mod_link("P2") in text and mod_link("P3") in text
    assert modrinth.filled == [["P2", "P3"]]


def test_search_asks_modrinth_once_per_keystroke_and_each_hit_opens_its_card() -> None:
    # The hit already carries its title and icon; a card the catalog lacks fetches its own project
    # when opened (mod_page). Filling the catalog here doubled the calls on every keystroke.
    client, _, modrinth = setup()
    ids = [f"H{i}" for i in range(10)]
    modrinth.hits = [{"project_id": i, "title": f"Hit {i}", "project_type": "mod"} for i in ids]
    text = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "hit"}, headers=HTMX).text
    assert modrinth.filled == []
    for i in ids:
        assert mod_link(i) in text


def test_compare_names_open_their_cards_with_one_fill() -> None:
    client, modpack, modrinth = setup()

    async def compare(bundle_id: str, de: int, a: int) -> Any:
        return preview()

    modpack.compare_builds = compare  # type: ignore[attr-defined]
    text = client.get(f"/modpacks/{BUNDLE_ID}/builds/compare", params={"de": 1, "a": 2}).text
    changes = preview().changes
    shown = [c.project_id for c in changes.added + changes.removed + changes.changed]
    for project_id in shown:
        assert mod_link(project_id) in text
    assert len(modrinth.filled) == 1 and sorted(modrinth.filled[0]) == sorted(set(shown))


def test_replacement_rules_open_their_cards_with_one_fill() -> None:
    client, _, modrinth = setup()
    text = client.get("/replacements").text
    for project_id in ("OLDMOD", "NEWMOD", "DROPPED"):
        assert mod_link(project_id, pack=False) in text  # a global page: no pack
    assert modrinth.filled == [["DROPPED", "NEWMOD", "OLDMOD"]]


# Internal tabs of the Mods view (spec §3) and a problem as a card with its actions (spec §1).

def tab(text: str, name: str) -> str:
    """The link of one tab, up to its closing tag."""
    start = text.rindex("<a", 0, text.index(f'data-tab="{name}"'))
    return text[start:text.index("</a>", start)]


def view(text: str, name: str) -> str:
    """The content of the view the fragment shows, which must be `name`."""
    assert f'data-vista="{name}"' in text, f"not the {name} view"
    return text[text.index(f'data-vista="{name}"'):]


def problem_card(text: str, project_id: str) -> str:
    """The HTML of one problem's card, up to the next one's."""
    return text.split(f'data-problem="{project_id}"')[1].split("data-problem=")[0]


def pending_item(text: str, project_id: str) -> str:
    return text.split(f'data-pending="{project_id}"')[1].split("data-pending=")[0]


def said(text: str) -> str:
    """The visible text with its whitespace collapsed."""
    return " ".join(visible(text).split())


def problems_page(modpack: FakeModpack, client: TestClient, *problems: Problem) -> str:
    modpack.saved = resolution(lock(entry("P1"), entry("P2"), problems=list(problems)))
    response = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "problemas"})
    assert response.status_code == 200
    return view(response.text, "problemas")


def test_tabs_link_to_each_view_with_their_counts_and_colors() -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(lock(entry("P1"), entry("P2", "admin"), problems=[
        Problem(kind="no_file", project_id="P1", detail="x"), Problem(kind="no_file", project_id="P2", detail="y")]))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    for name in ("todos", "decidir", "problemas", "sugerencias"):
        link, url = tab(text, name), f"/modpacks/{BUNDLE_ID}?vista={name}"
        assert f'href="{url}"' in link and f'hx-get="{url}"' in link and 'hx-push-url="true"' in link
        assert 'hx-select="#mods"' in link and 'hx-target="#mods"' in link and 'hx-swap="outerHTML"' in link
    # It starts on "Todos", with the pending counts highlighted (owner's decision).
    assert 'aria-current="page"' in tab(text, "todos") and "aria-current" not in tab(text, "decidir")
    assert said(tab(text, "todos")) == "Todos 2" and said(tab(text, "decidir")) == "Por decidir 1"
    assert said(tab(text, "problemas")) == "Con problemas 2"
    assert "bg-warning/" in tab(text, "decidir") and "bg-danger/" in tab(text, "problemas")
    assert 'data-vista="todos"' in text and 'data-project="P1"' in text
    modpack.saved = resolution(lock(entry("P1")))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert said(tab(text, "decidir")) == "Por decidir 0" and said(tab(text, "problemas")) == "Con problemas 0"
    assert "bg-amber-" not in tab(text, "decidir") and "bg-red-" not in tab(text, "problemas")


@pytest.mark.parametrize("vista", ["nada", "", "PROBLEMAS", "x" * 500])
def test_an_unknown_view_is_todos(vista: str) -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(lock(entry("P1"), entry("P2", "admin")))
    response = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": vista})
    assert response.status_code == 200 and 'aria-current="page"' in tab(response.text, "todos")
    assert 'data-project="P1"' in view(response.text, "todos")


def test_the_selected_tab_is_marked_and_only_its_view_is_drawn() -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(lock(entry("P1"), entry("P2", "admin")))
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "decidir"}).text
    assert 'aria-current="page"' in tab(text, "decidir") and "aria-current" not in tab(text, "todos")
    assert 'data-project="P1"' not in text and 'data-vista="todos"' not in text


def test_tabs_without_a_current_resolution_say_it_waits_for_modrinth() -> None:
    client, modpack, _ = setup()
    for stale in (None, True):
        if stale:
            modpack.saved, modpack.stale = resolution(lock(entry("P1"), entry("P2", "admin"))), True
        text = client.get(f"/modpacks/{BUNDLE_ID}").text
        assert "lo pendiente se ve al traer de modrinth lo que falta" in said(text).lower()
        assert said(tab(text, "decidir")) == "Por decidir" and said(tab(text, "problemas")) == "Con problemas"
        for name in ("decidir", "problemas"):
            shown = view(client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": name}).text, name)
            assert "lo pendiente se ve al traer de modrinth lo que falta" in said(shown).lower() and 'name="decision.P2"' not in shown


def test_the_pack_panel_counts_what_is_pending_with_links_to_the_tabs() -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(lock(entry("P1"), entry("P2", "admin"), problems=[
        Problem(kind="no_file", project_id="P1", detail="x"), Problem(kind="no_file", project_id="P2", detail="y")]))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    panel = text[text.index("Resuelto"):text.index('data-tab="todos"')]
    assert "1 por decidir · 2 con problemas" in said(panel)
    for name in ("decidir", "problemas"):
        assert f'hx-get="/modpacks/{BUNDLE_ID}?vista={name}"' in panel
    # It no longer lists the problems nor the bulk decisions: those are the tabs.
    assert "Problemas (" not in text and "decisions/bulk" not in text and 'data-problem="P1"' not in text
    modpack.saved = resolution(lock(entry("P1"), entry("P2")))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "por decidir ·" not in text and "con problemas" not in text.split('data-tab="todos"')[0]


def test_pending_view_shows_each_undecided_mod_with_its_card_and_shortcuts() -> None:
    client, modpack, _ = setup()
    dependency = entry("D1", "admin").model_copy(update={
        "origin": None, "requested_by": ["P1"], "environment": "client_or_server", "version_number": "2.3.4"})
    modpack.saved = resolution(lock(entry("P1"), entry("P2", "admin"), dependency))
    modpack.rows = [
        row("P1", "Sodium", side="include"),
        row("P2", "Lithium", "admin", side="admin", icon_url="https://cdn.modrinth.com/data/P2/icon.png",
            description="Optimiza <todo>"),
        row("D1", "Indium", None, side="admin", required_by=["Sodium"], requested_by=["P1"])]
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "decidir"}).text
    pending = view(text, "decidir")
    form = pending[pending.index(f'hx-post="/modpacks/{BUNDLE_ID}/decisions/bulk?vista=decidir"'):]
    form = form[:form.index("</form>")]
    assert 'hx-target="#mods"' in pending and 'hx-swap="outerHTML"' in pending
    lithium, indium = pending_item(form, "P2"), pending_item(form, "D1")
    assert '<img src="https://cdn.modrinth.com/data/P2/icon.png"' in lithium and mod_link("P2") in lithium
    assert "Optimiza &lt;todo&gt;" in lithium and "<todo>" not in lithium
    assert "Modrinth dice: cliente y servidor" in said(lithium) and "1.0" in said(lithium)
    assert "Modrinth dice: cliente o servidor" in said(indium) and "2.3.4" in said(indium)
    assert "dependencia de Sodium" in said(indium) and mod_link("P1") in indium
    for undecided in ("P2", "D1"):
        assert text.count(f'name="decision.{undecided}"') == form.count(f'name="decision.{undecided}"') == 1
    assert 'name="decision.P1"' not in text
    # The shortcuts mark every select without saving (app.js); saving is the form's button.
    for value, label in (("si", "Todos al servidor"), ("no", "Desactivar todos")):
        button = form[form.rindex("<button", 0, form.index(f'data-set-all="{value}"')):]
        button = button[:button.index("</button>")]
        assert 'type="button"' in button and label in button and "hx-" not in button
    assert "Guardar las 2 decisiones" in form
    assert '<option value="" selected>Por decidir</option>' in form and '<option value="no">Desactivado</option>' in form


def test_pending_view_without_mods_to_decide_says_so() -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(lock(entry("P1"), entry("P2")))
    shown = view(client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "decidir"}).text, "decidir")
    assert "No quedan mods por decidir" in shown and "decisions/bulk" not in shown


def test_the_bulk_decisions_from_the_pending_view_stay_in_it() -> None:
    client, modpack, _ = setup()
    modpack.resolves_by_itself = True
    modpack.lock = lock(entry("P1"), entry("P2", "admin"), entry("P3", "admin"))
    text = client.post(f"/modpacks/{BUNDLE_ID}/decisions/bulk", params={"vista": "decidir"}, headers=HTMX,
                       data={"decision.P2": "si"}).text
    assert 'aria-current="page"' in tab(text, "decidir") and 'name="decision.P3"' in view(text, "decidir")


# Beta and alpha accept the channel for the mod (owner, 2026-10-09): no version number, since the
# newest one is used while none fits the pack's channels. A release outside them is still pinned.
@pytest.mark.parametrize("channel,number,label", [("beta", "1.0-beta", "Usar la beta"),
                                                  ("alpha", "2.0-a", "Usar la alpha"),
                                                  ("release", "1.0", "Usar la versión 1.0 (release)")])
def test_a_mod_without_a_version_in_the_channels_offers_its_alternative_and_removal(
        channel: str, number: str, label: str) -> None:
    client, modpack, _ = setup()
    alternative = Alternative(version_id="ver-b", version_number=number, channel=channel, published=T0)
    shown = problems_page(modpack, client, Problem(kind="unresolvable", project_id="P2", alternative=alternative,
                                                   detail="sin versión para 1.21.1 con fabric en los canales release"))
    lithium = problem_card(shown, "P2")
    assert mod_link("P2") in lithium and label in lithium
    route = "pins" if channel == "release" else "channel-exceptions"
    use = lithium[lithium.index(f'hx-post="/modpacks/{BUNDLE_ID}/{route}"'):]
    use = use[:use.index("</form>")]
    assert 'name="project_id" value="P2"' in use
    assert ('name="version_id" value="ver-b"' if channel == "release" else f'name="channel" value="{channel}"') in use
    assert 'name="contexto" value="problemas"' in use and 'hx-target="#mods"' in use and label in use
    assert "Quitar del modpack" in lithium
    assert f'hx-delete="/modpacks/{BUNDLE_ID}/projects/P2?contexto=problemas"' in lithium
    assert "Ignorar esta dependencia" not in lithium and "P2" not in said(lithium)
    assert "1.21.1" in said(lithium) and number in said(lithium)


def test_a_dependency_without_a_version_in_the_channels_offers_its_alternative() -> None:
    # The owner's call (2026-10-05): a library only in beta is the common case.
    client, modpack, _ = setup()
    modpack.titles["X9"] = "Roto"
    alternative = Alternative(version_id="ver-b", version_number="1.0-beta", channel="beta", published=T0)
    shown = problems_page(modpack, client, Problem(kind="unresolvable", project_id="X9", requested_by=["P1"],
                                                   alternative=alternative, detail="sin versión; pedido por P1"))
    roto = problem_card(shown, "X9")
    use = roto[roto.index(f'hx-post="/modpacks/{BUNDLE_ID}/channel-exceptions"'):]
    use = use[:use.index("</form>")]
    assert 'name="project_id" value="X9"' in use and 'name="channel" value="beta"' in use
    assert "Usar la beta" in use
    assert "Quitar Sodium" in said(roto) and "Quitar del modpack" not in roto
    for absent in (*REPLACE_OR_IGNORE, "Fijar otra"):
        assert absent not in roto, absent


def test_a_dependency_without_versions_offers_removing_who_asked_a_replacement_or_ignoring_it() -> None:
    client, modpack, _ = setup()
    modpack.titles.update(X9="Roto <b>", D5="Otra dependencia")
    modpack.cards["X9"] = row("X9", "Roto <b>", None, description="Un mod roto",
                              icon_url="https://cdn.modrinth.com/data/X9/icon.png")
    shown = problems_page(modpack, client, Problem(
        kind="unresolvable", project_id="X9", requested_by=["P1", "D5"],
        detail="sin versión para 1.21.1 con fabric en los canales release; pedido por P1"))
    roto = problem_card(shown, "X9")
    assert mod_link("X9") in roto and '<img src="https://cdn.modrinth.com/data/X9/icon.png"' in roto
    assert "Un mod roto" in roto and "dependencia de Sodium" in said(roto) and mod_link("D5") in roto
    assert "Quitar Sodium" in said(roto) and f'hx-delete="/modpacks/{BUNDLE_ID}/projects/P1?contexto=problemas"' in roto
    assert "/projects/D5" not in roto  # not in the pack: nothing to remove
    assert "Crear un reemplazo" in roto and 'href="/replacements"' in roto
    start = roto.rindex("<form", 0, roto.index('hx-post="/replacements"'))
    ignore = roto[start:roto.index("</form>", start)]
    assert 'hx-confirm="Se descarta Roto &lt;b&gt; en todos los packs. ¿Seguir?"' in ignore
    for name, value in (("project_id", "X9"), ("replacement", ""), ("bundle_id", str(BUNDLE_ID)),
                        ("contexto", "problemas")):
        assert f'name="{name}" value="{value}"' in ignore
    assert "Ignorar esta dependencia" in ignore and 'hx-target="#mods"' in ignore
    assert "Quitar del modpack" not in roto and "Usar la" not in roto
    assert "X9" not in said(roto) and "P1" not in said(roto)


def test_ignoring_is_only_for_a_dependency_and_a_pack_mod_is_removed() -> None:
    client, modpack, _ = setup()
    shown = problems_page(modpack, client, Problem(kind="unresolvable", project_id="P2", requested_by=["P1"],
                                                   detail="sin versión"))
    lithium = problem_card(shown, "P2")
    assert "Quitar del modpack" in lithium and f'/projects/P2?contexto=problemas"' in lithium
    assert "Ignorar esta dependencia" not in lithium and 'hx-post="/replacements"' not in lithium


def test_removing_a_mod_of_the_mrpack_says_it_comes_back() -> None:
    client, modpack, _ = setup()
    shown = problems_page(modpack, client, Problem(kind="no_file", project_id="P1", detail="v-P1 no tiene archivo"),
                          Problem(kind="no_file", project_id="P2", detail="v-P2 no tiene archivo"))
    sodium, lithium = problem_card(shown, "P1"), problem_card(shown, "P2")  # P1 came with the .mrpack
    assert "Quitar del modpack" in sodium and "Quitar del modpack" in lithium
    assert "vuelve si se reemplaza el contenido del pack" in said(sodium) and 'href="/replacements"' in sodium
    assert "vuelve si se reemplaza" not in said(lithium)
    assert "no tiene archivo" in said(sodium) and "v-P1" not in said(sodium)


@pytest.mark.parametrize("pinned", [True, None])  # None: a problem stored before `pinned`
@pytest.mark.parametrize("kind", ["unresolvable", "pin_mismatch", "pin_conflict"])
def test_a_pinned_version_problem_offers_unpinning_or_pinning_another(kind: str, pinned: Optional[bool]) -> None:
    client, modpack, _ = setup()  # P1 is pinned to ver-p1
    shown = problems_page(modpack, client, Problem(kind=kind, project_id="P1", pinned=pinned,  # type: ignore[arg-type]
                                                   detail="ver-p1, fijada por admin, ya no está en Modrinth"))
    sodium = problem_card(shown, "P1")
    assert "Quitar la fijación" in sodium and f'hx-delete="/modpacks/{BUNDLE_ID}/pins/P1?contexto=problemas"' in sodium
    assert "Fijar otra" in sodium and f'href="/modpacks/{BUNDLE_ID}?vista=todos#mod-row-P1"' in sodium
    assert "Quitar del modpack" not in sodium and "ver-p1" not in said(sodium) and "fijada" in said(sodium)
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert 'id="mod-row-P1"' in text  # where "Fijar otra" lands: the mod in the list, with its versions


@pytest.mark.parametrize("kind", ["unresolvable", "pin_mismatch"])
def test_a_dependency_asking_for_another_version_of_a_pinned_mod_does_not_blame_the_pin(kind: str) -> None:
    # Lithium asks for a version of Sodium that is gone or does not fit; Sodium's own pin is fine.
    client, modpack, _ = setup()  # P1 is pinned to ver-p1
    shown = problems_page(modpack, client, Problem(kind=kind, project_id="P1", pinned=False,  # type: ignore[arg-type]
                                                   requested_by=["P2"], detail="P2 pide v-old, que Modrinth ya no tiene"))
    sodium = problem_card(shown, "P1")
    assert "fijada" not in said(sodium) and "Quitar la fijación" not in sodium and "Fijar otra" not in sodium
    assert "Lithium pide v-old, que Modrinth ya no tiene" in said(sodium)
    assert "Quitar Lithium" in said(sodium) and f'/projects/P2?contexto=problemas"' in sodium


def test_an_incompatibility_offers_removing_either_side_in_the_pack() -> None:
    client, modpack, _ = setup()
    modpack.titles["Z1"] = "Fuera"
    shown = problems_page(modpack, client,
                          Problem(kind="incompatible", project_id="P2", conflict_with="P1", detail="P1 es incompatible con P2"),
                          Problem(kind="incompatible", project_id="P1", conflict_with="Z1", detail="Z1 es incompatible con P1"))
    lithium = problem_card(shown, "P2")
    assert "Sodium es incompatible con Lithium" in said(lithium) and mod_link("P1") in lithium
    assert "Quitar Lithium" in said(lithium) and f'/projects/P2?contexto=problemas"' in lithium
    assert "Quitar Sodium" in said(lithium) and f'/projects/P1?contexto=problemas"' in lithium
    sodium = problem_card(shown, "P1")
    assert "Fuera es incompatible con Sodium" in said(sodium) and "Quitar Sodium" in said(sodium)
    assert "/projects/Z1" not in sodium and "Quitar Fuera" not in said(sodium)


def test_a_problem_saved_before_these_fields_shows_its_text_and_no_action_that_needs_them() -> None:
    # A resolution saved before requested_by, alternative and conflict_with existed: as stored.
    client, modpack, _ = setup()
    modpack.titles["X9"] = "Roto"
    stored = Lock.model_validate({
        "entries": [entry("P1").model_dump(), entry("P2").model_dump()], "suggestions": [],
        "needs_projects": [], "needs_types": [], "needs_versions": [], "problems": [
            {"kind": "unresolvable", "project_id": "X9",
             "detail": "sin versión para 1.21.1 con fabric en los canales release; pedido por P1"},
            {"kind": "incompatible", "project_id": "P2", "detail": "P1 es incompatible con P2"},
            {"kind": "unresolvable", "project_id": None, "detail": "P1 requiere una dependencia sin proyecto ni versión (a.jar)"}]})
    modpack.saved = resolution(stored)
    response = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "problemas"})
    assert response.status_code == 200
    shown = view(response.text, "problemas")
    roto, lithium = problem_card(shown, "X9"), problem_card(shown, "P2")
    assert "sin versión para 1.21.1 con fabric en los canales release; pedido por Sodium" in said(roto)
    assert "Usar la" not in roto and "Ignorar esta dependencia" not in roto and "Quitar Sodium" not in said(roto)
    assert "Sodium es incompatible con Lithium" in said(lithium) and "Quitar Sodium" not in said(lithium)
    assert "Sodium requiere una dependencia sin proyecto ni versión (a.jar)" in said(shown)
    assert "Con problemas 3" == said(tab(response.text, "problemas"))


def test_a_dependency_without_project_nor_version_offers_removing_who_asked() -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(Lock.model_validate({
        "entries": [entry("P1").model_dump(), entry("P2").model_dump()], "suggestions": [],
        "needs_projects": [], "needs_types": [], "needs_versions": [], "problems": [
            {"kind": "unresolvable", "project_id": None, "requested_by": ["P1"],
             "detail": "P1 requiere una dependencia sin proyecto ni versión (a.jar)"}]}))
    response = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "problemas"})
    assert response.status_code == 200
    card = problem_card(view(response.text, "problemas"), "")
    assert "Quitar Sodium" in said(card) and f'/projects/P1?contexto=problemas"' in card


# (A link to /replacements may stay: the note of a mod that came with the .mrpack has one.)
REPLACE_OR_IGNORE = ("Crear un reemplazo", "Ignorar esta dependencia", 'hx-post="/replacements"')


def test_a_dependency_with_an_alternative_is_not_replaced_nor_ignored() -> None:
    """Spec §1 table: "Crear un reemplazo" and "Ignorar esta dependencia" are only for a dependency
    without versions and without an alternative."""
    client, modpack, _ = setup()
    modpack.titles["X9"] = "Roto"
    alternative = Alternative(version_id="ver-b", version_number="1.0-beta", channel="beta", published=T0)
    shown = problems_page(modpack, client, Problem(kind="unresolvable", project_id="X9", requested_by=["P1"],
                                                   alternative=alternative, detail="sin versión"))
    roto = problem_card(shown, "X9")
    assert "Quitar Sodium" in said(roto)
    for absent in REPLACE_OR_IGNORE:
        assert absent not in roto, absent


@pytest.mark.parametrize("project_id,requested_by", [("P2", []), ("X9", ["P1"])])
def test_a_mod_without_a_file_is_only_removed(project_id: str, requested_by: list[str]) -> None:
    """Spec §1 table: `no_file` → "Quitar del modpack" (for a dependency, who asked for it), nothing else."""
    client, modpack, _ = setup()
    modpack.titles["X9"] = "Roto"
    alternative = Alternative(version_id="ver-b", version_number="1.0-beta", channel="beta", published=T0)
    shown = problems_page(modpack, client, Problem(kind="no_file", project_id=project_id, requested_by=requested_by,
                                                   alternative=alternative, detail="sin archivo"))
    found = problem_card(shown, project_id)
    assert ("Quitar del modpack" if project_id == "P2" else "Quitar Sodium") in said(found)
    for absent in (*REPLACE_OR_IGNORE, "Usar la", "Quitar la fijación", "Fijar otra", "/pins", "/channel-exceptions"):
        assert absent not in found, absent


def test_pinning_another_is_only_for_a_mod_of_the_pack() -> None:
    """Spec §1 table: "Fijar otra" opens the mod in the pack's list; a pinned dependency has no row
    there, so it only offers unpinning."""
    client, modpack, _ = setup()
    modpack.titles["X9"] = "Roto"
    modpack.bundle.pins = {**modpack.bundle.pins, "X9": "ver-x9"}
    shown = problems_page(modpack, client, Problem(kind="pin_mismatch", project_id="X9", requested_by=["P1"],
                                                   detail="ver-x9 no es para 1.21.1"))
    roto = problem_card(shown, "X9")
    assert "Quitar la fijación" in roto and f'/pins/X9?contexto=problemas"' in roto
    assert "Fijar otra" not in roto and "#mod-row-X9" not in roto
    for absent in REPLACE_OR_IGNORE:
        assert absent not in roto, absent


@pytest.mark.parametrize("method,path,data,action", [
    ("POST", "pins", {"project_id": "P2", "version_id": "ver-b", "contexto": "problemas"}, "pin_set"),
    ("DELETE", "pins/P1?contexto=problemas", None, "pin_unset"),
    ("DELETE", "projects/P2?contexto=problemas", None, "project_remove"),
])
def test_a_problem_action_applies_then_resolves_and_answers_the_problems_view(
        method: str, path: str, data: Optional[dict[str, str]], action: str) -> None:
    client, modpack, _ = setup()
    modpack.resolves_by_itself = True
    modpack.lock = lock(entry("P1"), problems=[Problem(kind="no_file", project_id="P1", detail="x")])
    response = client.request(method, f"/modpacks/{BUNDLE_ID}/{path}", headers=HTMX, data=data)
    assert response.status_code == 200 and response.text.lstrip().startswith('<div id="mods"')
    assert [c[0] for c in modpack.calls] == [action, "resolve_current"]
    assert call(modpack, action)[-1] == EDITOR and call(modpack, "resolve_current")[1] == str(BUNDLE_ID)
    assert 'aria-current="page"' in tab(response.text, "problemas") and 'data-problem="P1"' in view(response.text, "problemas")
    assert 'hx-swap-oob="true"' in re.search(r'<div id="builds"[^>]*>', response.text).group(0)  # type: ignore[union-attr]


def test_a_problem_action_that_leaves_nothing_highlights_crear_build() -> None:
    client, modpack, _ = setup()
    modpack.resolves_by_itself = True
    modpack.lock = lock(entry("P1"))
    text = client.delete(f"/modpacks/{BUNDLE_ID}/projects/P2", params={"contexto": "problemas"}, headers=HTMX).text
    assert "No hay problemas" in view(text, "problemas") and "data-ready" in builds_part(text)


@pytest.mark.parametrize("method,path,data", [
    ("POST", "pins", {"project_id": "P2", "version_id": "ver-b"}),
    ("DELETE", "pins/P1", None),
    ("DELETE", "projects/P2", None),
])
def test_without_contexto_an_action_resolves_by_itself_too(method: str, path: str, data: Optional[dict[str, str]]) -> None:
    """O9 D2 (owner, 2026-10-26): every action on the mods leaves the pack resolved, not only a
    problem's card; the answer stays in the tab it was asked from."""
    client, modpack, _ = setup()
    modpack.resolves_by_itself = True
    response = client.request(method, f"/modpacks/{BUNDLE_ID}/{path}", headers=HTMX, data=data)
    assert response.status_code == 200 and [c[0] for c in modpack.calls].count("resolve_current") == 1
    assert 'aria-current="page"' in tab(response.text, "todos")


@pytest.mark.parametrize("method,path,data", [
    ("POST", "pins", {"project_id": "P2", "version_id": "ver-b", "contexto": "otro"}),
    ("DELETE", "pins/P1?contexto=otro", None),
    ("DELETE", "projects/P2?contexto=", None),
])
def test_an_unknown_contexto_is_refused_before_the_service(method: str, path: str, data: Optional[dict[str, str]]) -> None:
    client, modpack, _ = setup()
    response = client.request(method, f"/modpacks/{BUNDLE_ID}/{path}", headers=HTMX, data=data)
    assert response.status_code == 422 and modpack.calls == []


def test_ignoring_a_dependency_drops_it_everywhere_then_resolves_the_pack() -> None:
    client, modpack, _ = setup()
    response = client.post("/replacements", headers=HTMX, data={
        "project_id": "X9", "replacement": "", "bundle_id": str(BUNDLE_ID), "contexto": "problemas"})
    assert response.status_code == 200 and response.text.lstrip().startswith('<div id="mods"')
    assert modpack.calls == [("replacement_set", "X9", None, EDITOR), ("resolve_current", str(BUNDLE_ID), EDITOR)]
    assert 'aria-current="page"' in tab(response.text, "problemas")


@pytest.mark.parametrize("extra", [{"bundle_id": str(BUNDLE_ID)}, {"contexto": "problemas"},
                                   {"bundle_id": "nope", "contexto": "problemas"},
                                   {"bundle_id": str(BUNDLE_ID), "contexto": "otro"}])
def test_replacements_take_both_the_pack_and_the_context_or_neither(extra: dict[str, str]) -> None:
    client, modpack, _ = setup()
    response = client.post("/replacements", headers=HTMX, data={"project_id": "X9", "replacement": "", **extra})
    assert response.status_code == 422 and modpack.calls == []


def test_app_js_marks_every_select_of_the_form_and_opens_a_linked_mod() -> None:
    client, _, _ = setup()
    text = client.get("/static/app.js").text
    assert "[data-set-all]" in text and "closest('form')" in text and "querySelectorAll('select')" in text
    assert "mod-row-" in text and "details" in text
    # Already on ?vista=todos, "Fijar otra" only changes the hash: no load, so hashchange too, and
    # the build dialog it was clicked in closes (F3 of the final review).
    assert "addEventListener('hashchange', openLinkedMod)" in text and "addEventListener('load', openLinkedMod)" in text


# Excepciones de canal por mod (owner, 2026-10-09): se ven y se quitan junto a los canales.


def test_a_channel_exception_from_the_mods_tab_resolves_and_answers_the_problems_view() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/channel-exceptions", headers=HTMX,
                           data={"project_id": "P2", "channel": "beta", "contexto": "problemas"})
    assert response.status_code == 200
    assert [c[0] for c in modpack.calls] == ["channel_exception_set", "resolve_current"]
    assert call(modpack, "channel_exception_set")[1:] == (str(BUNDLE_ID), "P2", "beta", EDITOR)
    assert "Beta aceptada para este mod" in response.text
    alpha = client.post(f"/modpacks/{BUNDLE_ID}/channel-exceptions", headers=HTMX,
                        data={"project_id": "P2", "channel": "alpha", "contexto": "problemas"})
    assert "Alpha aceptada para este mod" in alpha.text


@pytest.mark.parametrize("data", [{"project_id": "P2", "channel": "release"}, {"project_id": "P2", "channel": "x"},
                                  {"project_id": "../x", "channel": "beta"}])
def test_a_bad_channel_exception_is_refused_before_the_service(data: dict[str, str]) -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/channel-exceptions", headers=HTMX, data=data)
    assert response.status_code == 422 and modpack.calls == []


def test_removing_an_exception_answers_mods() -> None:
    client, modpack, _ = setup()
    modpack.bundle.channel_exceptions = {"P1": "beta"}
    response = client.delete(f"/modpacks/{BUNDLE_ID}/channel-exceptions/P1", headers=HTMX)
    assert response.status_code == 200 and "HX-Redirect" not in response.headers
    assert response.text.lstrip().startswith('<div id="mods"')
    assert call(modpack, "channel_exception_unset")[1:] == (str(BUNDLE_ID), "P1", EDITOR)


def test_iris_suggested_for_a_shader_says_it_is_needed_not_optional() -> None:
    client, modpack, _ = setup()
    modpack.titles.update(YL57xq9U="Iris", S1="Complementary")
    modpack.saved = resolution(lock(entry("P1"), entry("S1", project_type="shader"),
                                    suggestions=[Suggestion(project_id="YL57xq9U", requested_by=["S1"])]))
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "sugerencias"}).text
    shown = " ".join(visible(view(text, "sugerencias")).split())
    assert "Iris — necesario para Complementary" in shown and "opcional para Complementary" not in shown


def test_a_panel_request_names_its_user_for_the_modrinth_limiter() -> None:
    # Spec 2026-10-09-economia-de-requests (E4): the per-user quota is counted against this name.
    from src.library.api.limiter import CURRENT_USER

    client, _, modrinth = setup()
    seen: list[Optional[str]] = []

    async def search(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        seen.append(CURRENT_USER.get())
        return []

    modrinth.search = search  # type: ignore[method-assign]
    client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "sodium"}, headers=HTMX)
    assert seen == [f"{EDITOR.iss}|{EDITOR.sub}"]


def test_a_user_without_quota_reads_how_long_to_wait_and_nothing_else() -> None:
    from src.exceptions import QuotaExceeded

    client, _, modrinth = setup()
    modrinth.error = QuotaExceeded("Demasiadas consultas a Modrinth: intenta en 30 s", 30.0)
    response = client.get(f"/modpacks/{BUNDLE_ID}/search", params={"q": "sodium"}, headers=HTMX)
    shown = " ".join(visible(response.text).split())
    assert "Demasiadas consultas a Modrinth: intenta en 30 s" in shown and "no responde" not in shown



def test_when_a_build_job_its_check_blocked_ends_the_dialog_opens() -> None:
    client, modpack, _ = setup()
    modpack.build_blocked_now = True
    ended = client.get(f"/modpacks/{BUNDLE_ID}/trabajo?activo=1", headers=HTMX)
    assert ended.headers["HX-Trigger"] == "job-finished, build-blocked"
    page = client.get(f"/modpacks/{BUNDLE_ID}").text
    container = page[page.index('<div id="build-dialog"'):]
    container = container[:container.index(">")]
    assert f'hx-get="/modpacks/{BUNDLE_ID}/builds/dialogo"' in container and "build-blocked from:body" in container
    assert page.index('id="build-dialog"') < page.index('id="pack-main"')  # outside the part that reloads
    modpack.lock = lock(entry("P2", "admin"))
    dialog = client.get(f"/modpacks/{BUNDLE_ID}/builds/dialogo", headers=HTMX)
    assert "<dialog" in dialog.text and 'name="decision.P2"' in dialog.text



def test_v1_build_queues_the_build_job_and_answers_202_with_it() -> None:
    # Owner's decision U5 (2026-10-09): /v1 answers 202 with the job, with the panel's keys (A2).
    from datetime import datetime, timezone
    from fastapi import FastAPI
    from src.dependences import get_current_actor
    from src.model.jobs import Job
    from src.routers.v1 import include_routers_v1

    _, modpack, _ = setup()
    modpack.queued = Job.model_construct(
        id=PydanticObjectId(), kind="build_create", key=f"build:{BUNDLE_ID}", status="queued", active=True,
        payload={"bundle_id": str(BUNDLE_ID)}, created_at=datetime.now(timezone.utc), attempts=0, max_attempts=3,
        checkpoint={}, step=None, error=None, requested_by=EDITOR.ref(), progress=None, waiting_until=None, updated_at=None)
    app = FastAPI()
    include_routers_v1(app)
    app.dependency_overrides[get_current_actor] = lambda: EDITOR
    response = TestClient(app, base_url="https://testserver").post(f"/v1/modpack/{BUNDLE_ID}/build")
    assert response.status_code == 202 and response.json()["kind"] == "build_create"
    assert response.json()["_id"] == str(modpack.queued.id)  # as /v1 update, rollback, scan


# Alta guiada (B1, d-7985ee-20935c).


def test_the_pack_page_lists_what_is_left_to_set_up() -> None:
    client, modpack, _ = setup(OPERATOR)
    modpack.saved = resolution(lock(entry("P1"), entry("P2", "admin")))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    part = text[text.index("data-onboarding"):]
    part = part[:part.index("</section>")]
    shown = said(part)
    assert "Para terminar el alta" in shown and "Con mods" in shown and "1 por decidir o con problemas" in shown
    assert f'href="/modpacks/{BUNDLE_ID}?vista=decidir"' in part
    assert f'href="/modpacks/{BUNDLE_ID}#servers-section"' in part  # no server linked: link one, here (O9 D8)


# F09 (d-7985ee-a44a91): the Minecraft version is chosen from Modrinth's releases.


def test_creating_a_pack_offers_modrinth_s_releases_to_choose_from() -> None:
    client, _, modrinth = setup()
    modrinth.game_versions = ["26.3", "1.21.11"]
    text = client.get("/modpacks").text
    field = text[text.index('id="new-version"') - 200:]
    field = field[field.index("<select"):field.index("</select>")]
    assert 'name="version"' in field and '<option value="26.3" selected>26.3</option>' in field
    assert '<option value="1.21.11">1.21.11</option>' in field


def test_without_modrinth_the_version_is_typed() -> None:
    client, _, modrinth = setup()
    text = client.get("/modpacks").text
    assert re.search(r'<input id="new-version" name="version" type="text"', text)


@pytest.mark.parametrize("path, cells", [("/modpacks", 5), (f"/modpacks/{BUNDLE_ID}", 4 + 3), ("/replacements", 3)])
def test_every_table_cell_carries_its_header_for_the_phone(path: str, cells: int) -> None:
    # B4 (d-7985ee-937ca8): below sm: each row is a card, its cells labelled by their header.
    client, _, _ = setup()
    assert assert_cells_labelled(client.get(path).text) >= cells


# Loaders secundarios (batch C; d-7985ee-a8d395, d-7985ee-45c2ba).


def bridges_form(text: str) -> str:
    start = text.index('id="bridges"')
    start = text.rindex("<form", 0, start)
    return text[start:text.index("</form>", start)]


def test_channels_offer_the_bridges_of_the_pack_s_loader_and_version() -> None:
    client, modpack, _ = setup()  # Fabric 1.21.1: Kilt
    form = bridges_form(client.get(f"/modpacks/{BUNDLE_ID}").text)
    assert f'hx-post="/modpacks/{BUNDLE_ID}/bridges"' in form and 'name="bridge" value="kilt"' in form
    shown = said(form)
    # Its own section (O9 D12): the heading is the section's.
    assert 'id="bridges-section"' in client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "Usar mods de Forge (vía Kilt)" in shown and "experimental" in shown
    assert "checked" not in form[form.index('value="kilt"'):].split(">")[0]


def test_a_pack_whose_version_has_no_bridge_offers_none() -> None:
    client, modpack, _ = setup()
    modpack.bundle = modpack.bundle.model_copy(update={"version": "1.21"})
    assert 'id="bridges"' not in client.get(f"/modpacks/{BUNDLE_ID}").text


def test_saving_the_bridges_patches_the_pack() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/bridges", headers=HTMX, data={"bridge": "kilt"})
    assert response.status_code == 200
    patch = call(modpack, "bundle_update")[2]
    assert patch.secondary_loaders == ["kilt"]
    modpack.calls.clear()
    client.post(f"/modpacks/{BUNDLE_ID}/bridges", headers=HTMX, data={})
    assert call(modpack, "bundle_update")[2].secondary_loaders == []


def test_a_bridge_kept_for_another_version_says_it_has_no_effect() -> None:
    client, modpack, _ = setup()
    modpack.bundle = modpack.bundle.model_copy(update={"version": "1.20.1", "secondary_loaders": ["kilt", "old"]})
    form = bridges_form(client.get(f"/modpacks/{BUNDLE_ID}").text)
    assert 'value="kilt" checked' in form
    modpack.bundle = modpack.bundle.model_copy(update={"version": "1.21", "secondary_loaders": ["kilt"]})
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "Kilt está activo, pero no sirve en 1.21: no tiene efecto." in visible(text)


def test_v1_patch_turns_a_bridge_on() -> None:
    """Batch C: `/v1` sets the pack's bridges through the same patch, checked by the service."""
    from fastapi import FastAPI
    from src.dependences import get_current_actor
    from src.routers.v1 import include_routers_v1

    _, modpack, _ = setup()
    app = FastAPI()
    include_routers_v1(app)
    app.dependency_overrides[get_current_actor] = lambda: EDITOR
    v1 = TestClient(app, base_url="https://testserver")
    assert v1.patch(f"/v1/modpack/{BUNDLE_ID}", json={"secondary_loaders": ["kilt"]}).status_code == 200
    assert call(modpack, "bundle_update")[2].secondary_loaders == ["kilt"]


# The way of a mod (batch C, task 7).


def bridged(modpack: FakeModpack) -> None:
    modpack.bundle = modpack.bundle.model_copy(update={"loader": MinecraftLoaders.neoforge, "secondary_loaders": ["connector"]})
    modpack.rows = [row("N", "NativeMod", "admin", side="include"),
                    row("F", "FabricMod", "admin", side="include", via="connector", native="alcanza", server_risky=True),
                    row("G", "OtherMod", "admin", side="exclude", via="connector", native="detrás"),
                    row("H", "LoneMod", "admin", side="include", via="connector")]


def test_a_bridged_row_says_its_way_that_it_is_not_preferred_and_that_the_server_should_test_it() -> None:
    client, modpack, _ = setup()
    bridged(modpack)
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    first = said(card(text, "F"))
    assert "vía Connector (Fabric)" in first and "no preferible" in first and "servidor: probar antes" in first
    assert "vía Connector" not in said(card(text, "N"))
    assert "servidor: probar antes" not in said(card(text, "G"))  # disabled on the server


def test_the_via_tab_lists_the_bridged_mods_with_their_state_and_offers_moving_to_native() -> None:
    client, modpack, _ = setup(OPERATOR)
    bridged(modpack)
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "puente"}).text
    assert said(tab(text, "puente")) == "Vía puente 3"
    shown = view(text, "puente")
    assert "hay nativa: se puede pasar" in said(shown) and "hay nativa más vieja que la instalada" in said(shown)
    assert "sin versión nativa" in said(shown) and "NativeMod" not in shown
    assert f'hx-post="/modpacks/{BUNDLE_ID}/via"' in shown and 'name="native" value="1"' in shown


def test_moving_a_mod_s_way_asks_with_a_dialog_then_saves_with_the_confirmation() -> None:
    client, modpack, _ = setup()
    bridged(modpack)
    asked = client.post(f"/modpacks/{BUNDLE_ID}/via", headers=HTMX, data={"project_id": "F", "native": "1"})
    assert asked.headers["HX-Retarget"] == "#pin-dialog" and "<dialog" in asked.text
    shown = said(asked.text)
    assert "Cambiar de vía no siempre funciona" in shown and "Pasar igual" in shown
    assert 'name="confirm" value="1"' in asked.text
    done = client.post(f"/modpacks/{BUNDLE_ID}/via", headers=HTMX, data={"project_id": "F", "native": "1", "confirm": "1"})
    assert done.status_code == 200 and done.text.lstrip().startswith('<div id="mods"')
    assert [c[5] for c in modpack.calls if c[0] == "via_set"] == [False, True]


def test_the_filter_bar_has_a_loader_list_when_a_bridge_is_on() -> None:
    client, modpack, _ = setup()
    bridged(modpack)
    form = bar(client.get(f"/modpacks/{BUNDLE_ID}").text)
    assert ">vía puente (3)</option>" in form and ">nativos (1)</option>" in form
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"loader": "nativos"}).text
    assert 'data-project="N"' in view(text, "todos") and 'data-project="F"' not in view(text, "todos")


def test_v1_moves_a_mod_s_way_only_with_the_confirmation() -> None:
    """Batch C (d-7985ee-7e602f): `/v1` answers 409 describing the change, and repeats with
    `confirm_via=true`."""
    from fastapi import FastAPI
    from src.dependences import get_current_actor
    from src.routers.v1 import include_routers_v1

    _, modpack, _ = setup()
    app = FastAPI()
    include_routers_v1(app)
    app.dependency_overrides[get_current_actor] = lambda: EDITOR
    v1 = TestClient(app, base_url="https://testserver")
    refused = v1.post(f"/v1/modpack/{BUNDLE_ID}/via", json={"project_id": "F", "native": True})
    assert refused.status_code == 409 and "FabricMod" in refused.json()["detail"]
    done = v1.post(f"/v1/modpack/{BUNDLE_ID}/via", params={"confirm_via": "true"}, json={"project_id": "F", "native": True})
    assert done.status_code == 200
    assert [c[5] for c in modpack.calls if c[0] == "via_set"] == [False, True]


def test_an_editor_is_never_offered_a_simulation_on_the_pack_page() -> None:
    """Simulating is an operator's (O9 user-walk, A10; the class K2 of 2026-10-13): an editor reads «Ver»."""
    client, modpack, _, _, ptero = setup_all(EDITOR)
    ptero.servers = [PterodactylServer.model_construct(id="srv1", name="Survival", bundle_id=str(BUNDLE_ID),
                                                       is_suspended=False, status=None)]
    modpack.builds[0].created_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    servers = text[text.index('id="servers-section"'):]
    servers = servers[:servers.index("</section>")]
    assert "Simular" not in servers and ">Ver<" in servers and 'href="/servers/srv1"' in servers
    assert "Simular instalación en Survival" not in text


def test_the_pack_resolves_by_itself_when_its_resolution_is_missing_or_old() -> None:
    """O9 D2 (owner, 2026-10-26): drawing the mods resolves the pack when what is saved is missing
    or older than the pack, so every action and every visit show it current, with no button."""
    client, modpack, _ = setup()
    modpack.resolves_by_itself = True
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert call(modpack, "resolve_current")[1] == str(BUNDLE_ID) and "1 por decidir" in text
    modpack.calls.clear()
    client.get(f"/modpacks/{BUNDLE_ID}")
    assert not [c for c in modpack.calls if c[0] == "resolve_current"]
    modpack.stale = True
    client.post(f"/modpacks/{BUNDLE_ID}/decisions", headers=HTMX, data={"project_id": "P2", "server": "no"})
    assert [c[0] for c in modpack.calls if c[0].startswith("resolve")] == ["resolve_current"]


def test_the_pack_has_no_resolve_button_and_brings_from_modrinth() -> None:
    """O9 D2 and D6: «Resolver» goes; asking Modrinth again is «Traer de Modrinth»."""
    client, modpack, _ = setup()
    modpack.saved = resolution(modpack.lock)
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "/resolve" not in text and ">Resolver" not in visible(text)
    assert "Traer de Modrinth" in visible(text) and f'hx-post="/modpacks/{BUNDLE_ID}/catalogo"' in text
    assert client.post(f"/modpacks/{BUNDLE_ID}/resolve", headers=HTMX).status_code in (404, 405)


def test_a_mod_from_an_imported_pack_says_del_pack() -> None:
    """O9 A8 (owner's text, 2026-10-26): a Prism or CurseForge zip is not an .mrpack."""
    client, modpack, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "del .mrpack" not in text and "del pack" in visible(text)


def test_below_1280_the_builds_come_before_the_mods() -> None:
    """O9 D1 (owner, 2026-10-26): in one column «Crear build» was under every mod; at 1280 px and up
    the side column holds them, as before."""
    client, _, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert text.index('id="builds-section"') < text.index('id="mods-section"')
    grid = re.search(r'<div class="grid grid-cols-1[^"]*"', text).group(0)  # type: ignore[union-attr]
    assert "xl:grid-cols-3" in grid and "xl:grid-rows-[auto_1fr]" in grid
    builds = text[text.rindex('<div class="', 0, text.index('id="builds-section"')):]
    assert "xl:col-start-3" in builds[:builds.index('>')] and "xl:row-start-1" in builds[:builds.index('>')]


def unlinked_servers(ptero: FakePtero) -> None:
    ptero.servers = [PterodactylServer.model_construct(id="srv1", name="Survival", bundle_id=None, is_suspended=False, status=None),
                     PterodactylServer.model_construct(id="srv2", name="Otro", bundle_id="x", is_suspended=False, status=None)]


def test_an_operator_links_a_server_without_a_pack_from_the_pack() -> None:
    """O9 D8 (owner, 2026-10-26): «Vincular» in «Servidores vinculados», the servers with no pack."""
    client, _, _, _, ptero = setup_all(OPERATOR)
    unlinked_servers(ptero)
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    section = text[text.index('id="servers-section"'):]
    form = section[section.index("<form"):section.index("</form>")]
    assert f'hx-post="/modpacks/{BUNDLE_ID}/servidores"' in form and '<option value="srv1">Survival</option>' in form
    assert "srv2" not in form and "Vincular" in visible(form)
    response = client.post(f"/modpacks/{BUNDLE_ID}/servidores", headers=HTMX, data={"server_id": "srv1"})
    assert response.status_code == 200 and response.headers["HX-Redirect"] == f"/modpacks/{BUNDLE_ID}#servers-section"
    assert ptero.linked == [("srv1", str(BUNDLE_ID), OPERATOR)]


def test_a_server_with_a_pack_is_not_moved_from_another_pack() -> None:
    client, _, _, _, ptero = setup_all(OPERATOR)
    unlinked_servers(ptero)
    response = client.post(f"/modpacks/{BUNDLE_ID}/servidores", headers=HTMX, data={"server_id": "srv2"})
    assert response.status_code == 409 and not getattr(ptero, "linked", [])


def test_an_editor_is_not_offered_linking() -> None:
    client, _, _, _, ptero = setup_all(EDITOR)
    unlinked_servers(ptero)
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert f'hx-post="/modpacks/{BUNDLE_ID}/servidores"' not in text


def test_the_filter_bar_has_the_version_filter() -> None:
    """O9 D9 (owner, 2026-10-26)."""
    client, _, _ = setup()
    form = bar(client.get(f"/modpacks/{BUNDLE_ID}").text)
    assert 'id="f-version"' in form and "versión del pack ofrecida" in form and "fuera de etiquetas" in form
    assert "#f-version" in form


def test_channels_save_when_changed_with_undo_and_the_mods_redrawn() -> None:
    """O9 D12 (owner, 2026-10-26): like a row's select, no «Guardar canales»; the notice undoes it."""
    client, modpack, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    form = text[text.index('<form id="channels"'):]
    form = form[:form.index("</form>")]
    assert 'hx-trigger="change"' in form and "Guardar canales" not in form
    response = client.post(f"/modpacks/{BUNDLE_ID}/channels", headers=HTMX, data={"channel": ["release", "beta"]})
    notice = response.text[response.text.index('id="notices"'):]
    undo = notice[notice.index("<form"):notice.index("</form>")]
    assert f'hx-post="/modpacks/{BUNDLE_ID}/channels"' in undo and 'name="channel" value="release"' in undo
    assert 'value="beta"' not in undo and "Deshacer" in undo
    assert '<div id="mods" class="space-y-4" hx-swap-oob="true"' in response.text


def test_a_mod_that_came_with_the_pack_says_how_to_take_it_out() -> None:
    """O9 D12: «Quitar del modpack» is only for what the admin added; one from the pack said nothing."""
    client, _, _ = setup()
    page = client.get(f"/modpacks/{BUNDLE_ID}").text
    text = page[page.index('id="mod-row-P1"'):]
    text = text[:text.index("</li>")]
    assert "Vino con el pack: desactívalo o crea un reemplazo vacío" in " ".join(visible(text).split())
    assert 'href="/replacements"' in text


def test_build_as_test_queues_a_test_job() -> None:
    """O8 B1 (d-7985ee-a99db7): «Construir como prueba» sends `prueba=1`."""
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", data={"prueba": "1"}, headers=HTMX)
    assert response.status_code == 200 and response.headers["HX-Trigger"] == "job-started"
    assert call(modpack, "build_request")[1:] == (str(BUNDLE_ID), EDITOR, "test")


def test_a_test_row_offers_approve_and_approve_and_recommend_never_recommend() -> None:
    """O8 B1, H2 (d-7985ee-3bb53b): a test is never recommended without being approved."""
    client, modpack, _ = setup()
    modpack.builds = [build(3, test=True), build(2), build(1)]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    row = text[text.index('data-build="3"'):]
    row = row[:row.index("</tr>")]
    assert "Prueba" in row and f'hx-post="/modpacks/{BUNDLE_ID}/builds/3/aprobar"' in row
    assert "Aprobar y recomendar" in row and "/builds/3/recommend" not in row


def test_approving_answers_the_builds_with_a_notice() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds/3/aprobar", data={"recomendar": "1"}, headers=HTMX)
    assert response.status_code == 200 and 'id="builds"' in response.text and "Build 3 aprobado." in response.text
    assert call(modpack, "build_approve")[1:] == (str(BUNDLE_ID), 3, EDITOR, True)


def test_approving_an_old_test_says_which_build_stays_in_force() -> None:
    from src.services.modpack import ApproveResult
    client, modpack, _ = setup()
    modpack.approved = ApproveResult(bundle=modpack.bundle, approved=True, in_force=4, newer=[4])
    text = client.post(f"/modpacks/{BUNDLE_ID}/builds/3/aprobar", headers=HTMX).text
    assert "Build 3 aprobado." in text and "El build 4 sigue vigente." in text and "Hay builds posteriores: 4." in text


def test_approving_an_approved_build_says_so() -> None:
    from src.services.modpack import ApproveResult
    client, modpack, _ = setup()
    modpack.approved = ApproveResult(bundle=modpack.bundle, approved=False, in_force=3, newer=[])
    assert "El build 3 ya estaba aprobado." in client.post(f"/modpacks/{BUNDLE_ID}/builds/3/aprobar", headers=HTMX).text


def test_naming_and_starring_a_build_answer_the_builds() -> None:
    """O8 B2, B3: the star is sent as a state, never toggled (user-walk W14)."""
    client, modpack, _ = setup()
    named = client.post(f"/modpacks/{BUNDLE_ID}/builds/2/nombre", data={"nombre": "con Iris"}, headers=HTMX)
    starred = client.post(f"/modpacks/{BUNDLE_ID}/builds/2/destacar", data={"estado": "si"}, headers=HTMX)
    unstarred = client.post(f"/modpacks/{BUNDLE_ID}/builds/2/destacar", data={"estado": "no"}, headers=HTMX)
    assert all(r.status_code == 200 and 'id="builds"' in r.text for r in (named, starred, unstarred))
    assert call(modpack, "build_name")[1:] == (str(BUNDLE_ID), 2, "con Iris", EDITOR)
    assert [c[3] for c in modpack.calls if c[0] == "build_star"] == [True, False]


def test_a_build_shows_its_name_its_star_and_the_form_for_each() -> None:
    client, modpack, _ = setup()
    modpack.builds = [build(2, name="con Iris", starred=True), build(1)]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    row = text[text.index('data-build="2"'):]
    row = row[:row.index("</tr>")]
    assert "con Iris" in row and 'name="nombre"' in row and 'maxlength="60"' in row
    assert f'hx-post="/modpacks/{BUNDLE_ID}/builds/2/destacar"' in row and "hx-vals='{\"estado\": \"no\"}'" in row


def test_a_name_with_html_is_escaped_everywhere() -> None:
    client, modpack, _ = setup()
    modpack.builds = [build(2, name="<b>x</b>"), build(1)]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "<b>x</b>" not in text and "&lt;b&gt;x&lt;/b&gt;" in text


def test_builds_offer_build_as_test() -> None:
    """O8 B1: «Construir como prueba» next to «Crear build»."""
    client, _, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    builds = text[text.index('id="builds"'):]
    assert "Construir como prueba" in builds and "{&#34;prueba&#34;: &#34;1&#34;}" in builds


def test_compare_with_previous_links_the_existing_previous() -> None:
    client, modpack, _ = setup()
    modpack.builds = [build(4), build(2), build(1)]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert f'href="/modpacks/{BUNDLE_ID}/builds/compare?de=2&amp;a=4"' in text
    assert f'/builds/compare?de=0' not in text  # the first has none


def test_old_builds_fold_and_only_starred_is_a_filter() -> None:
    client, modpack, _ = setup()
    modpack.builds = [build(n, starred=n == 2) for n in range(8, 0, -1)]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "3 builds anteriores" in text  # shown: 2 (starred), 8, 7, 6 and 1 (the recommended)
    only = client.get(f"/modpacks/{BUNDLE_ID}/builds", params={"destacados": "1"}, headers=HTMX).text
    assert 'id="builds"' in only and "7 builds anteriores" in only
