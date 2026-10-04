"""Las páginas de modpacks del panel (etapa 2): lista, detalle, búsqueda, lock, builds y reemplazos.

Los servicios se sustituyen con dobles en el contenedor, como en `tests/test_web_base.py`. Cada
doble anota qué recibió, para afirmar que toda mutación lleva el `actor` de la sesión.
"""
import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from aiohttp import ClientConnectionError
from beanie import PydanticObjectId
from dependency_injector import providers
from fastapi.testclient import TestClient

from src.exceptions import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT
from src.library.resolution import Lock, LockEntry, Problem, Suggestion
from src.library.scan import LocalFile
from src.model.modpack import (AvailableUpdate, ModpackBuild, ModpackBundle, ModpackReplacement, ModpackResolution,
                               PackProject, ProjectOrigin, UpdateCheck)
from src.model.modrinth import ModrinthProject
from src.model.pterodactyl import PterodactylServer
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from src.services.modpack import ModList, ModpackService, ModRow, VersionOption
from src.services.modrinth import ModrinthService
from tests._actors import EDITOR, OPERATOR, session_for
from tests._session import session_cookie
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
        channels=["release"], pins={"P1": "ver-p1"},
        local_files=[LocalFile(filename="custom.jar", sha512="ab12cd34ef56" + "0" * 100, sha1="x", size=1536)])


def build(number: int) -> ModpackBuild:
    return ModpackBuild.model_construct(id=PydanticObjectId(), bundle_id=BUNDLE_ID, number=number, created_at=T0,
                                        lock=lock(), loader_version="0.16")


def entry(project_id: str, server: str = "include") -> LockEntry:
    return LockEntry(project_id=project_id, origin="pack", version_id="v-" + project_id, version_number="1.0",
                     file=None, environment="client_and_server", server=server,  # type: ignore[arg-type]
                     reason="en el pack", requested_by=[])


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
    return ModrinthProject.model_construct(project_id=project_id, slug=project_id.lower(), title=title)


class FakeModpack:
    def __init__(self) -> None:
        self.bundle = bundle()
        self.builds = [build(2), build(1)]
        self.rules = [ModpackReplacement.model_construct(project_id="OLDMOD", replacement="NEWMOD"),
                      ModpackReplacement.model_construct(project_id="DROPPED", replacement=None)]
        self.lock = lock(entry("P1"), entry("P2", "admin"))
        self.calls: list[tuple[Any, ...]] = []
        self.error: Optional[Exception] = None
        # What mod_list answers: `rows` as given, or one row per project of the bundle (with the
        # side and pin it has) and per entry of the saved resolution that is not in it.
        self.titles: dict[str, str] = {"P1": "Sodium", "P2": "Lithium"}
        self.rows: Optional[list[ModRow]] = None
        self.saved: Optional[ModpackResolution] = None
        self.stale = False
        self.checks_seen: list[Optional[UpdateCheck]] = []
        self.versions = [VersionOption("ver-new", "0.8.14", "release", datetime(2026, 10, 1, tzinfo=timezone.utc)),
                         VersionOption("ver-beta", "0.9.0-beta.1", "beta", datetime(2026, 10, 2, tzinfo=timezone.utc))]
        self.versions_error: Optional[Exception] = None

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

    async def bundle_update(self, bundle_id: str, patch: Any, actor: Actor) -> ModpackBundle:
        self._record("bundle_update", bundle_id, patch, actor)
        return self.bundle

    async def pin_set(self, bundle_id: str, project_id: str, version_id: str, actor: Actor) -> ModpackBundle:
        self._record("pin_set", bundle_id, project_id, version_id, actor)
        self.bundle.pins = {**self.bundle.pins, project_id: version_id}
        return self.bundle

    async def pin_unset(self, bundle_id: str, project_id: str, actor: Actor) -> ModpackBundle:
        self._record("pin_unset", bundle_id, project_id, actor)
        self.bundle.pins = {k: v for k, v in self.bundle.pins.items() if k != project_id}
        return self.bundle

    async def project_add(self, bundle_id: str, refs: list[str], origin: ProjectOrigin, actor: Actor) -> list[PackProject]:
        self._record("project_add", bundle_id, refs, origin, actor)
        owned = {p.project_id for p in self.bundle.projects}
        self.bundle.projects = self.bundle.projects + [PackProject(project_id=r, origin=origin) for r in refs if r not in owned]
        return self.bundle.projects

    async def project_remove(self, bundle_id: str, project_id: str, actor: Actor) -> list[PackProject]:
        self._record("project_remove", bundle_id, project_id, actor)
        self.bundle.projects = [p for p in self.bundle.projects if p.project_id != project_id]
        return self.bundle.projects

    async def resolve(self, bundle_id: str) -> Lock:
        self._record("resolve", bundle_id)
        self.saved = resolution(self.lock)
        return self.lock

    async def mod_list(self, bundle_id: str, check: Optional[UpdateCheck]) -> ModList:
        self.checks_seen.append(check)
        rows = self.rows
        if rows is None:
            entries = {e.project_id: e for e in self.saved.lock.entries} if self.saved else {}
            pack = {p.project_id: p.origin.value for p in self.bundle.projects}
            rows = [row(i, self.titles.get(i, i), origin, side=entries[i].server if i in entries else None,
                        pinned=i in self.bundle.pins) for i, origin in pack.items()]
            rows += [row(i, self.titles.get(i, i), None, side=e.server) for i, e in entries.items() if i not in pack]
        return ModList(rows=rows, resolution=self.saved, stale=self.stale, icons_missing=0)

    async def project_versions(self, bundle_id: str, project_id: str) -> list[VersionOption]:
        self.calls.append(("project_versions", bundle_id, project_id))
        if self.versions_error is not None:
            raise self.versions_error
        return self.versions

    async def build_create(self, bundle_id: str, actor: Actor) -> ModpackBuild:
        self._record("build_create", bundle_id, actor)
        return self.builds[0]

    async def build_find(self, bundle_id: str) -> list[ModpackBuild]:
        return self.builds

    async def build_recommend(self, bundle_id: str, number: int, actor: Actor) -> ModpackBundle:
        self._record("build_recommend", bundle_id, number, actor)
        return self.bundle

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

    async def search(self, query: str, game_version: Optional[str], loader: Optional[str], limit: int = 10) -> list[dict[str, Any]]:
        self.searches.append((query, game_version, loader, limit))
        if self.error is not None:
            raise self.error
        return self.hits

    async def projects_by_id(self, project_ids: list[str]) -> dict[str, ModrinthProject]:
        return {i: project(i, self.titles[i]) for i in project_ids if i in self.titles}

    async def projects_filled(self, project_ids: list[str]) -> dict[str, ModrinthProject]:
        self.filled.append(list(project_ids))
        return {i: project(i, self.titles[i]) for i in project_ids if i in self.titles}


class FakeChecks:
    def __init__(self) -> None:
        self.check: Optional[UpdateCheck] = None

    async def latest(self, bundle_id: str) -> Optional[UpdateCheck]:
        return self.check


class FakePtero:
    def __init__(self) -> None:
        self.servers: list[PterodactylServer] = []

    async def server_find(self) -> list[PterodactylServer]:
        return self.servers


@pytest.fixture(autouse=True)
def reset_overrides() -> Any:
    yield
    import main

    for provider in (main.container.modpackService, main.container.modrinthService,
                     main.container.updateCheckService, main.container.pterodactylService):
        provider.reset_override()


def setup_all(actor: Optional[Actor] = EDITOR) -> tuple[TestClient, FakeModpack, FakeModrinth, FakeChecks, FakePtero]:
    import main

    modpack, modrinth, checks, ptero = FakeModpack(), FakeModrinth(), FakeChecks(), FakePtero()
    main.container.modpackService.override(providers.Object(modpack))
    main.container.modrinthService.override(providers.Object(modrinth))
    main.container.updateCheckService.override(providers.Object(checks))
    main.container.pterodactylService.override(providers.Object(ptero))
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

def test_resolve_answers_the_mods_with_the_saved_resolution_on_top() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/resolve", headers=HTMX)
    assert response.status_code == 200 and response.text.lstrip().startswith('<div id="mods"')
    assert call(modpack, "resolve")[1] == str(BUNDLE_ID)
    assert "Sodium" in response.text and "Lithium" in response.text
    assert f'hx-post="/modpacks/{BUNDLE_ID}/decisions/bulk"' in response.text
    assert response.text.index("Resuelto") < response.text.index("Decidir varios") < response.text.index('data-project="P1"')


def test_resolve_shows_problems() -> None:
    client, modpack, _ = setup()
    modpack.lock = lock(entry("P1"), problems=[Problem(kind="incompatible", project_id="P1", detail="no corre en 1.21.1")])
    response = client.post(f"/modpacks/{BUNDLE_ID}/resolve", headers=HTMX)
    assert "no corre en 1.21.1" in response.text


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
    modpack.lock = lock(entry("P1"), entry("P2", "admin"), entry("P3", "admin"))
    text = client.post(f"/modpacks/{BUNDLE_ID}/resolve", headers=HTMX).text
    assert f'hx-post="/modpacks/{BUNDLE_ID}/decisions/bulk"' in text
    assert 'name="decision.P2"' in text and 'name="decision.P3"' in text and 'name="decision.P1"' not in text
    assert "Guardar decisiones" in text
    form = text[text.index(f'hx-post="/modpacks/{BUNDLE_ID}/decisions/bulk"'):]
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


def test_create_build_returns_the_builds_with_the_actor() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX)
    assert response.status_code == 200 and 'id="builds"' in response.text
    assert call(modpack, "build_create")[1:] == (str(BUNDLE_ID), EDITOR)


def test_build_conflict_shows_reason_as_notice() -> None:
    client, modpack, _ = setup()
    modpack.error = HTTP_409_CONFLICT("Mods sin decidir: x")
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX)
    assert response.status_code == 409 and "Mods sin decidir: x" in response.text
    assert 'data-notice' in response.text


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


def test_service_search_builds_facets_from_the_target() -> None:
    api = FakeSearchApi()
    service = ModrinthService(api, None)  # type: ignore[arg-type]
    assert asyncio.run(service.search("sodium", "1.21.1", "quilt", 7)) == [{"project_id": "P"}]
    [(query, facets, limit)] = api.calls
    assert (query, limit) == ("sodium", 7)
    assert facets[0] == ["project_type:mod", "project_type:shader", "project_type:resourcepack", "project_type:datapack"]
    assert facets[1] == ["versions:1.21.1"]
    # Quilt carga mods de Fabric; los shaders y packs tienen loaders propios: no se excluyen.
    assert {"categories:quilt", "categories:fabric", "categories:iris", "categories:datapack"} <= set(facets[2])


def test_service_search_without_a_target_has_only_the_types() -> None:
    api = FakeSearchApi()
    asyncio.run(ModrinthService(api, None).search("x", None, None))  # type: ignore[arg-type]
    assert len(api.calls[0][1]) == 1


@pytest.mark.network
def test_projects_by_id_finds_them_by_their_modrinth_id(mongo: MongoRunner) -> None:
    import json
    from pathlib import Path

    from src.database.modrinth import ModrinthDatabase

    data = json.loads((Path(__file__).parent / "fixtures" / "modrinth" / "project-sodium.json").read_text())

    async def scenario() -> dict[str, str]:
        database = ModrinthDatabase()
        await database.project_upsert_many([ModrinthProject.parse(**data)])
        found = await ModrinthService(None, database).projects_by_id([data["id"], "unknown"])  # type: ignore[arg-type]
        return {i: p.slug for i, p in found.items()}

    assert mongo(scenario) == {data["id"]: "sodium"}


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
    assert "fijado" in card(text, "P1") and f'hx-delete="/modpacks/{BUNDLE_ID}/pins/P1"' in card(text, "P1")
    assert "fijado" not in card(text, "P2") and "ver-p1" not in visible(text)


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
            downloads=1234567, side="exclude", build_version="0.8.13", newer_version="0.8.14", pinned=True),
        row("P9", "Fabric API", None, required_by=["Sodium"], side="include", build_version="0.100.0"),
        row("P8", "Mod Menu", "pack", side="admin")]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert modpack.checks_seen == [checks.check]  # la última revisión llega a la lista
    sodium = card(text, "AANobbMI")
    assert '<img src="https://cdn.modrinth.com/data/AANobbMI/icon.png"' in sodium
    for shown in ("Sodium", "agregado", "Solo cliente", "0.8.13 → 0.8.14", "1,2 M descargas", "fijado"):
        assert shown in sodium, shown
    assert 'title="0.8.13 → 0.8.14"' in sodium and "truncate" in sodium  # versión larga con elipsis
    dependency = card(text, "P9")
    assert "dependencia de Sodium" in dependency and "En el servidor" in dependency and "0.100.0" in dependency
    assert "del .mrpack" in card(text, "P8") and "Sin decidir" in card(text, "P8")
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
    assert 'name="version_id"' not in text


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
    modpack.versions_error = RuntimeError("down")
    response = client.get(f"/modpacks/{BUNDLE_ID}/projects/P1/versions", headers=HTMX)
    assert response.status_code == 200 and "Modrinth no respondió" in response.text
    assert f'hx-get="/modpacks/{BUNDLE_ID}/projects/P1/versions"' in response.text  # reintentar


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
    client, modpack, modrinth = setup()
    modrinth.titles["P7"] = "Mod Menu"
    modpack.saved = resolution(lock(entry("P1"), suggestions=[Suggestion(project_id="P7", requested_by=["P1"])],
                                    problems=[Problem(kind="unresolvable", project_id="P6", detail="sin versión")]))
    modrinth.titles["P6"] = "Roto"
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert modrinth.filled and {"P6", "P7"} <= set(modrinth.filled[0])
    suggestions = text[text.index("Sugerencias"):]
    assert "Mod Menu" in suggestions and "sugerida por Sodium" in suggestions
    assert f'hx-post="/modpacks/{BUNDLE_ID}/projects"' in suggestions and 'name="ref" value="P7"' in suggestions
    assert "Roto" in text and "sin versión" in text
    assert "P7" not in visible(text) and "P6" not in visible(text)


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


def test_undecided_card_decides_inline() -> None:
    client, modpack, _ = setup()
    modpack.saved = resolution(lock(entry("P1"), entry("P2", "admin")))
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    lithium = card(text, "P2")
    assert f'hx-post="/modpacks/{BUNDLE_ID}/decisions"' in lithium and 'hx-target="#mods"' in lithium
    assert '<input type="hidden" name="project_id" value="P2">' in lithium
    assert 'name="server" value="yes"' in lithium and 'name="server" value="no"' in lithium
    assert f'hx-post="/modpacks/{BUNDLE_ID}/decisions"' not in card(text, "P1")
    response = client.post(f"/modpacks/{BUNDLE_ID}/decisions", headers=HTMX, data={"project_id": "P2", "server": "yes"})
    assert response.status_code == 200 and response.text.lstrip().startswith('<div id="mods"')
    assert call(modpack, "decision_set")[1:] == (str(BUNDLE_ID), "P2", True, EDITOR)


def test_detail_layout_has_linked_servers_and_renamed_updates_link() -> None:
    client, _, _, _, ptero = setup_all()
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
    order = [text.index(marker) for marker in ('id="mods-section"', 'id="builds-section"', "Servidores vinculados",
                                               'id="channels-section"', "Archivos locales")]
    assert order == sorted(order)


def test_empty_sections_fold_to_one_line() -> None:
    client, modpack, _ = setup()
    modpack.bundle.local_files = []
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert "Archivos locales: ninguno" in text and "sha512" not in text.lower()
    assert "Servidores vinculados: ninguno" in text
