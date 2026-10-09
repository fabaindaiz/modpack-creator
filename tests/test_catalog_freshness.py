"""Resolver desde la base (spec 2026-10-09-economia-de-requests, E1–E2, F2).

Por proyecto y objetivo se guarda qué versiones listaba Modrinth; dentro de la ventana de 5 minutos
un pack se resuelve sin ninguna request. Pasada la ventana, revisar cambios son dos requests
agrupadas (los proyectos y solo las versiones nuevas), nunca una por mod. Un build revisa siempre.
Contra MongoDB real; Modrinth es un doble que anota cada llamada."""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence, cast

import pytest

from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.model.modpack import ModpackBundle, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.model.utils import MinecraftLoaders
from src.services.modpack import CATALOG_WINDOW, ModpackService
from tests._actors import EDITOR, RecordingAudit
from tests.conftest import MongoRunner
from tests.test_builds import FakeLoaders

pytestmark = pytest.mark.network

T0 = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def project(project_id: str, versions: Sequence[str]) -> dict[str, Any]:
    return dict(id=project_id, slug=project_id.lower(), team="t", title=project_id, description="", body="",
                project_type="mod", status="approved", downloads=1, followers=1, categories=[],
                additional_categories=[], versions=list(versions), game_versions=["1.20.1"], loaders=["fabric"],
                issues_url=None, source_url=None, wiki_url=None, icon_url=None,
                published="2020-01-01T00:00:00Z", updated="2020-01-01T00:00:00Z")


def version(version_id: str, project_id: str, day: int, deps: Sequence[str] = (), game: str = "1.20.1") -> dict[str, Any]:
    return dict(id=version_id, project_id=project_id, author_id="a", name=version_id, version_number=version_id,
                version_type="release", status="listed", downloads=1, game_versions=[game], loaders=["fabric"],
                featured=False, date_published=f"2026-09-{day:02d}T00:00:00Z", changelog=None,
                dependencies=[{"project_id": d, "version_id": None, "file_name": None, "dependency_type": "required"}
                              for d in deps],
                files=[{"hashes": {"sha1": version_id, "sha512": version_id * 2}, "url": f"https://cdn.modrinth.com/{version_id}.jar",
                        "filename": f"{version_id}.jar", "primary": True, "size": 1}])


class Modrinth:
    """Two mods; A needs LIB. Each call is noted."""

    def __init__(self) -> None:
        self.versions = {v["id"]: v for v in (version("a1", "AAAA", 1, deps=["LIBB"]), version("b1", "BBBB", 1),
                                              version("l1", "LIBB", 1), version("a0", "AAAA", 0, game="1.19.2"))}
        self.calls: list[tuple[str, Any]] = []

    def listed(self, project_id: str) -> list[str]:
        return [i for i, v in self.versions.items() if v["project_id"] == project_id]

    async def project_get_many(self, slugs: list[str]) -> list[ModrinthProject]:
        self.calls.append(("project_get_many", tuple(sorted(slugs))))
        return [ModrinthProject.parse(**project(p, self.listed(p))) for p in slugs if self.listed(p)]

    async def project_versions(self, slug: str, loaders: Sequence[str] = (), game_versions: Sequence[str] = (),
                               featured: Optional[bool] = None) -> list[ModrinthVersion]:
        self.calls.append(("project_versions", slug))
        return [ModrinthVersion.parse(**v) for v in self.versions.values()
                if v["project_id"] == slug and set(game_versions) & set(v["game_versions"])]

    async def version_get_many(self, ids: list[str]) -> list[ModrinthVersion]:
        self.calls.append(("version_get_many", tuple(sorted(ids))))
        return [ModrinthVersion.parse(**self.versions[i]) for i in ids if i in self.versions]


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


async def setup() -> tuple[ModpackService, Modrinth, Clock, str]:
    api, clock = Modrinth(), Clock()
    service = ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(), modrinthAPI=cast(Any, api),
                             loaderAPI=cast(Any, FakeLoaders()), audit=RecordingAudit(), clock=clock)
    bundle = await ModpackDatabase().bundle_insert(ModpackBundle(
        name="p", description="", version="1.20.1", loader=MinecraftLoaders.fabric,
        projects=[PackProject(project_id=p, origin=ProjectOrigin.pack) for p in ("AAAA", "BBBB")]))
    return service, api, clock, str(bundle.id)


def chosen(lock: Any) -> dict[str, str]:
    return {e.project_id: e.version_id for e in lock.entries}


def test_the_first_resolve_fetches_what_is_missing_and_the_next_inside_the_window_none(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, api, clock, bundle_id = await setup()
        first = chosen(await service.resolve_fetching(bundle_id))
        cold = list(api.calls)
        api.calls.clear()
        clock.now += CATALOG_WINDOW - timedelta(seconds=1)
        second = chosen(await service.resolve_fetching(bundle_id))
        return first, second, cold, api.calls

    first, second, cold, warm = mongo(scenario)
    assert first == second == {"AAAA": "a1", "BBBB": "b1", "LIBB": "l1"}
    assert [c[0] for c in cold].count("project_versions") == 3  # once per project, the first time only
    assert warm == []


def test_past_the_window_a_check_is_two_batched_calls_and_brings_a_new_version(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, api, clock, bundle_id = await setup()
        await service.resolve_fetching(bundle_id)
        api.calls.clear()
        api.versions["b2"] = version("b2", "BBBB", 5)
        clock.now += CATALOG_WINDOW + timedelta(seconds=1)
        lock = await service.resolve_fetching(bundle_id)
        return chosen(lock), api.calls

    picked, calls = mongo(scenario)
    assert picked["BBBB"] == "b2"
    assert calls == [("project_get_many", ("AAAA", "BBBB", "LIBB")), ("version_get_many", ("b2",))]


def test_a_check_without_changes_is_one_call(mongo: MongoRunner) -> None:
    async def scenario() -> list[tuple[str, Any]]:
        service, api, clock, bundle_id = await setup()
        await service.resolve_fetching(bundle_id)
        api.calls.clear()
        clock.now += CATALOG_WINDOW + timedelta(seconds=1)
        await service.resolve_fetching(bundle_id)
        return api.calls

    assert mongo(scenario) == [("project_get_many", ("AAAA", "BBBB", "LIBB"))]


def test_a_version_modrinth_no_longer_lists_is_not_used(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, str]:
        service, api, clock, bundle_id = await setup()
        api.versions["b2"] = version("b2", "BBBB", 5)
        await service.resolve_fetching(bundle_id)
        del api.versions["b2"]
        clock.now += CATALOG_WINDOW + timedelta(seconds=1)
        return chosen(await service.resolve_fetching(bundle_id))

    assert mongo(scenario)["BBBB"] == "b1"


def test_a_project_refreshed_elsewhere_does_not_hide_its_new_versions(mongo: MongoRunner) -> None:
    # The mod page refreshes the project document without fetching its versions: the check compares
    # against what the version list accounted for, not against that document.
    async def scenario() -> dict[str, str]:
        service, api, clock, bundle_id = await setup()
        await service.resolve_fetching(bundle_id)
        api.versions["b2"] = version("b2", "BBBB", 5)
        await ModrinthDatabase().project_upsert_many([ModrinthProject.parse(**project("BBBB", api.listed("BBBB")))])
        clock.now += CATALOG_WINDOW + timedelta(seconds=1)
        return chosen(await service.resolve_fetching(bundle_id))

    assert mongo(scenario)["BBBB"] == "b2"


def test_a_build_checks_even_inside_the_window(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, list[tuple[str, Any]]]:
        service, api, clock, bundle_id = await setup()
        await service.resolve_fetching(bundle_id)
        await service.decisions_set(bundle_id, {"AAAA": True, "BBBB": True, "LIBB": True}, EDITOR)
        api.calls.clear()
        api.versions["b2"] = version("b2", "BBBB", 5)
        build = await service.build_create(bundle_id, EDITOR)
        return {e.project_id: e.version_id for e in build.lock.entries}["BBBB"], api.calls

    picked, calls = mongo(scenario)
    assert picked == "b2" and [c[0] for c in calls] == ["project_get_many", "version_get_many"]


def test_what_was_fetched_stays_even_if_a_later_call_fails(mongo: MongoRunner) -> None:
    async def scenario() -> list[str]:
        service, api, clock, bundle_id = await setup()
        original = api.project_versions

        async def failing(slug: str, *args: Any, **kwargs: Any) -> list[ModrinthVersion]:
            if slug == "BBBB":
                raise RuntimeError("Modrinth down")
            return await original(slug, *args, **kwargs)

        api.project_versions = failing  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            await service.resolve_fetching(bundle_id)
        return sorted({v.project_id async for v in ModrinthVersion.find()})

    assert "AAAA" in mongo(scenario)
