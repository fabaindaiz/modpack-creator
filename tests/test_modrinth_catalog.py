"""El catálogo sigue el modelo actual de Modrinth (i-7985ee-f4ba63), probado con respuestas reales.

Las respuestas están grabadas en tests/fixtures/modrinth/ por scripts/record_modrinth_fixtures.py
(la fecha, en RECORDED). Construir un ModrinthProject o un ModrinthVersion exige Beanie
inicializado, y eso un servidor: por eso casi todo corre dentro del fixture `mongo`.
"""
import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from src.database.modrinth import ModrinthDatabase
from src.model.modrinth import ModrinthGameVersion, ModrinthProject, ModrinthVersion, newest_first
from src.model.modrinth.project import ModrinthEnvironment
from src.services.modrinth import ModrinthService
from tests.conftest import MongoRunner

FIXTURES = Path(__file__).parent / "fixtures" / "modrinth"
PROJECTS = ["sodium", "fabric-api", "lithium", "iris", "complementary-reimagined"]


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def all_versions() -> list[dict[str, Any]]:
    return [v for f in sorted(FIXTURES.glob("versions-*.json")) for v in load(f.name)]


# Versiones de juego: Minecraft numera por año desde 2026 (26.x). Sin base.


def tags() -> list[ModrinthGameVersion]:
    return [ModrinthGameVersion(**t) for t in load("tag-game_version.json")]


def test_game_versions_are_ordered_by_modrinth_not_by_parsing_them() -> None:
    # Como texto, "1.21.9" > "1.21.10" y "26.3" < "1.21"; como 1.x.y, 26.3 no se puede leer.
    shuffled = ["1.21.9", "26.3", "1.21.1", "1.21.10", "1.21.11"]
    assert newest_first(shuffled, tags()) == ["26.3", "1.21.11", "1.21.10", "1.21.9", "1.21.1"]


def test_a_game_version_modrinth_does_not_list_is_an_error_not_a_guess() -> None:
    with pytest.raises(ValueError, match="27.1"):
        newest_first(["26.3", "27.1"], tags())


# Proyectos y versiones grabados.


@pytest.mark.network
def test_every_recorded_project_parses_and_keeps_its_environment(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, tuple[list[str], list[str]]]:
        parsed = {slug: ModrinthProject.parse(**load(f"project-{slug}.json")) for slug in PROJECTS}
        return {slug: ([e.value for e in p.environment], p.loaders) for slug, p in parsed.items()}

    parsed = mongo(scenario)
    assert parsed["sodium"][0] == ["client_only"]
    assert parsed["fabric-api"][0] == ["client_or_server_prefers_both"]
    # Un shader: sin environment, y loaders que no son loaders de mods.
    assert parsed["complementary-reimagined"] == ([], ["iris", "optifine"])


@pytest.mark.network
def test_every_recorded_version_parses_and_keeps_its_environment(mongo: MongoRunner) -> None:
    async def scenario() -> set[str | None]:
        return {ModrinthVersion.parse(**v).environment for v in all_versions()}

    environments = mongo(scenario)
    assert environments and environments <= {e for e in ModrinthEnvironment}


@pytest.mark.network
def test_a_project_without_the_deprecated_sides_still_parses(mongo: MongoRunner) -> None:
    """Modrinth deprecó client_side y server_side: el día que los quite, el catálogo sigue."""
    async def scenario() -> ModrinthProject:
        data = load("project-sodium.json")
        del data["client_side"], data["server_side"]
        return ModrinthProject.parse(**data)

    project = mongo(scenario)
    assert (project.client_side, project.server_side) == (None, None)


@pytest.mark.network
def test_documents_stored_before_environment_existed_still_load(mongo: MongoRunner) -> None:
    """El campo nuevo y el viejo conviven (tarjeta no-simultaneous-deploy): lo guardado antes carga."""
    async def scenario() -> tuple[list[ModrinthEnvironment], ModrinthEnvironment | None]:
        project = load("project-sodium.json")
        version = load("versions-sodium.json")[0]
        del project["environment"], version["environment"]
        await ModrinthDatabase().project_upsert_many([ModrinthProject.parse(**project)])
        await ModrinthDatabase().version_upsert_many([ModrinthVersion.parse(**version)])
        stored_project = await ModrinthProject.find_one(ModrinthProject.slug == "sodium")
        stored_version = await ModrinthVersion.find_one(ModrinthVersion.version_id == version["id"])
        assert stored_project and stored_version
        return stored_project.environment, stored_version.environment

    assert mongo(scenario) == ([], None)


@pytest.mark.network
def test_an_environment_modrinth_adds_tomorrow_reads_as_unknown(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[ModrinthEnvironment], ModrinthEnvironment | None]:
        project = load("project-sodium.json")
        version = load("versions-sodium.json")[0]
        project["environment"] = ["client_only", "on_the_moon"]
        version["environment"] = "on_the_moon"
        return ModrinthProject.parse(**project).environment, ModrinthVersion.parse(**version).environment

    environments, environment = mongo(scenario)
    assert environments == [ModrinthEnvironment.client_only, ModrinthEnvironment.unknown]
    assert environment == ModrinthEnvironment.unknown


# Dependencias por versión, no por proyecto.


class RecordedModrinthAPI:
    """Contesta con las respuestas grabadas; lo que no está grabado es un error del test."""

    def __init__(self) -> None:
        projects = [load(f"project-{slug}.json") for slug in PROJECTS]
        self.projects = {key: p for p in projects for key in (p["id"], p["slug"])}
        self.versions = {v["id"]: v for v in all_versions()}

    async def version_get_many(self, ids: list[str]) -> list[ModrinthVersion]:
        return [ModrinthVersion.parse(**copy.deepcopy(self.versions[i])) for i in ids]

    async def project_get_many(self, slugs: list[str]) -> list[ModrinthProject]:
        return [ModrinthProject.parse(**copy.deepcopy(self.projects[s])) for s in slugs]


def service() -> ModrinthService:
    return ModrinthService(modrinthAPI=cast(Any, RecordedModrinthAPI()), modrinthDB=ModrinthDatabase())


def iris_for(game_version: str) -> str:
    version_id: str = next(v["id"] for v in load("versions-iris.json") if game_version in v["game_versions"])
    return version_id


@pytest.mark.network
def test_the_dependencies_of_a_version_are_those_of_that_version(mongo: MongoRunner) -> None:
    async def scenario() -> list[tuple[str | None, str | None, str]]:
        dependencies = await service().version_dependencies(iris_for("26.3"))
        return [(d.project_id, d.version_id, d.dependency_type.value) for d in dependencies]

    assert mongo(scenario) == [("AANobbMI", "bAZQdGpg", "required")]


@pytest.mark.network
def test_following_a_version_stores_it_and_what_it_depends_on(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[set[str], dict[str, list[str]]]:
        await service().version_dependencies(iris_for("26.3"))
        projects = {p.slug async for p in ModrinthProject.find()}
        versions = {v.version_id: v.game_versions async for v in ModrinthVersion.find()}
        return projects, versions

    projects, versions = mongo(scenario)
    assert projects == {"iris", "sodium"}
    # La versión de Sodium que pide esta Iris, para 26.3: no la de 1.21.11 que piden otras.
    assert versions == {iris_for("26.3"): ["26.3"], "bAZQdGpg": ["26.3"]}


@pytest.mark.network
def test_icon_url_is_stored_and_absent_reads_as_none(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str | None, str | None]:
        with_icon = load("project-sodium.json")
        without = load("project-lithium.json")
        with_icon["icon_url"] = "https://cdn.modrinth.com/data/AANobbMI/icon.png"
        without.pop("icon_url", None)
        await ModrinthDatabase().project_upsert_many(
            [ModrinthProject.parse(**with_icon), ModrinthProject.parse(**without)])
        a = await ModrinthProject.find_one(ModrinthProject.slug == "sodium")
        b = await ModrinthProject.find_one(ModrinthProject.slug == "lithium")
        assert a and b
        return a.icon_url, b.icon_url

    assert mongo(scenario) == ("https://cdn.modrinth.com/data/AANobbMI/icon.png", None)


@pytest.mark.network
def test_the_same_search_within_ten_minutes_is_answered_from_mongodb(mongo: MongoRunner) -> None:
    # Spec 2026-10-09-economia-de-requests (F6): every pause in typing was a request to Modrinth.
    from src.services.modrinth import ModrinthService

    class Search:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def search(self, query: str, facets: list[list[str]], limit: int = 5) -> list[dict[str, Any]]:
            self.calls.append(query)
            return [{"project_id": "P1", "title": query}]

    async def scenario() -> tuple[Any, ...]:
        api = Search()
        service = ModrinthService(cast(Any, api), ModrinthDatabase())
        first = await service.search("Sodium ", "1.21.1", "fabric", 10)
        again = await service.search("sodium", "1.21.1", "fabric", 10)
        other = await service.search("sodium", "1.20.1", "fabric", 10)
        return first, again, other, api.calls

    first, again, other, calls = mongo(scenario)
    assert first == again == [{"project_id": "P1", "title": "sodium"}]
    assert calls == ["sodium", "sodium"]  # the second target is another search
