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
from src.library.resolution import Lock, LockEntry, Problem
from src.model.modpack import ModpackBuild, ModpackBundle, ModpackReplacement, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthProject
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from src.services.modpack import ModpackService
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
        server_decisions=decisions if decisions is not None else {"OLD": True}, latest_build=2, recommended_build=1)


def build(number: int) -> ModpackBuild:
    return ModpackBuild.model_construct(id=PydanticObjectId(), bundle_id=BUNDLE_ID, number=number, created_at=T0,
                                        lock=lock(), loader_version="0.16")


def entry(project_id: str, server: str = "include") -> LockEntry:
    return LockEntry(project_id=project_id, origin="pack", version_id="v-" + project_id, version_number="1.0",
                     file=None, environment="client_and_server", server=server,  # type: ignore[arg-type]
                     reason="en el pack", requested_by=[])


def lock(*entries: LockEntry, problems: Optional[list[Problem]] = None) -> Lock:
    return Lock(entries=list(entries), problems=problems or [], suggestions=[], needs_projects=set(),
                needs_types=set(), needs_versions=set())


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

    async def project_add(self, bundle_id: str, refs: list[str], origin: ProjectOrigin, actor: Actor) -> list[PackProject]:
        self._record("project_add", bundle_id, refs, origin, actor)
        return self.bundle.projects

    async def project_remove(self, bundle_id: str, project_id: str, actor: Actor) -> list[PackProject]:
        self._record("project_remove", bundle_id, project_id, actor)
        return self.bundle.projects[:1]

    async def resolve(self, bundle_id: str) -> Lock:
        self._record("resolve", bundle_id)
        return self.lock

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

    async def search(self, query: str, game_version: Optional[str], loader: Optional[str], limit: int = 10) -> list[dict[str, Any]]:
        self.searches.append((query, game_version, loader, limit))
        if self.error is not None:
            raise self.error
        return self.hits

    async def projects_by_id(self, project_ids: list[str]) -> dict[str, ModrinthProject]:
        return {i: project(i, self.titles[i]) for i in project_ids if i in self.titles}


@pytest.fixture(autouse=True)
def reset_overrides() -> Any:
    yield
    import main

    for provider in (main.container.modpackService, main.container.modrinthService):
        provider.reset_override()


def setup(actor: Optional[Actor] = EDITOR) -> tuple[TestClient, FakeModpack, FakeModrinth]:
    import main

    modpack, modrinth = FakeModpack(), FakeModrinth()
    main.container.modpackService.override(providers.Object(modpack))
    main.container.modrinthService.override(providers.Object(modrinth))
    # Sin `with`: el lifespan abriría MongoDB y el proveedor OIDC.
    client = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
    if actor is not None:
        client.cookies.set("session", session_cookie(session_for(actor)))
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

def test_detail_groups_projects_by_origin_with_their_titles() -> None:
    client, _, _ = setup()
    response = client.get(f"/modpacks/{BUNDLE_ID}")
    assert response.status_code == 200
    text = response.text
    assert "Sodium" in text and "Lithium" in text
    assert text.index("Sodium") < text.index("Lithium")  # pack antes que admin
    assert 'id="projects"' in text and 'id="lock"' in text and 'id="builds"' in text
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
    client, _, modrinth = setup()
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    modrinth.titles["P1"] = evil
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
    modrinth.titles = {i: "Mod " + i + " " + "x" * 120 for i in ids}
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
    (["singleplayer_only"], "cliente"),
    (["server_only"], "servidor"),
    (["server_only_client_optional"], "servidor"),
    (["dedicated_server_only"], "servidor"),
    (["client_or_server"], "cliente y servidor"),
    (["client_or_server_prefers_both"], "cliente y servidor"),
    (["client_only", "server_only"], "cliente y servidor"),
    (["client_only", "unknown"], "cliente"),
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
    response = client.post(f"/modpacks/{BUNDLE_ID}/projects", headers=HTMX, data={"ref": "P3"})
    assert response.status_code == 200 and 'id="projects"' in response.text
    assert call(modpack, "project_add")[1:] == (str(BUNDLE_ID), ["P3"], ProjectOrigin.admin, EDITOR)


def test_add_unknown_project_shows_the_reason_as_a_notice() -> None:
    client, modpack, _ = setup()
    modpack.error = HTTP_404_NOT_FOUND("Modrinth has no project nope")
    response = client.post(f"/modpacks/{BUNDLE_ID}/projects", headers=HTMX, data={"ref": "nope"})
    assert response.status_code == 404 and "Modrinth has no project nope" in response.text


def test_remove_project_with_the_actor() -> None:
    client, modpack, _ = setup()
    response = client.delete(f"/modpacks/{BUNDLE_ID}/projects/P2", headers=HTMX)
    assert response.status_code == 200 and 'id="projects"' in response.text
    assert call(modpack, "project_remove")[1:] == (str(BUNDLE_ID), "P2", EDITOR)
    assert "Lithium" not in response.text  # el doble devuelve solo P1


# Lock y decisiones.

def test_resolve_shows_the_lock_with_undecided_mods() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/resolve", headers=HTMX)
    assert response.status_code == 200 and 'id="lock"' in response.text
    assert call(modpack, "resolve")[1] == str(BUNDLE_ID)
    assert "Sodium" in response.text and "Lithium" in response.text
    assert f'hx-post="/modpacks/{BUNDLE_ID}/decisions"' in response.text
    assert 'name="project_id" value="P2"' in response.text and "¿Va en el servidor?" in response.text


def test_resolve_shows_problems() -> None:
    client, modpack, _ = setup()
    modpack.lock = lock(entry("P1"), problems=[Problem(kind="incompatible", project_id="P1", detail="no corre en 1.21.1")])
    response = client.post(f"/modpacks/{BUNDLE_ID}/resolve", headers=HTMX)
    assert "no corre en 1.21.1" in response.text


def test_decision_is_one_key_with_the_actor() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/decisions", headers=HTMX, data={"project_id": "P2", "server": "no"})
    assert response.status_code == 200 and 'id="lock"' in response.text
    assert call(modpack, "decision_set")[1:] == (str(BUNDLE_ID), "P2", False, EDITOR)


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
