"""Qué cambia entre dos locks (N3): la diferencia pura y la vista previa con los changelogs.

`diff_entries` no toca nada. `change_preview` lee los changelogs del catálogo y, si no están, de
Modrinth (`GET /version/{id}`); un fallo de Modrinth marca la versión, nunca lanza.
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.exceptions import HTTP_404_NOT_FOUND
from src.library.changes import Change, ChangeSet, diff_entries
from src.library.resolution import Lock, LockEntry, ResolveRequest, Target
from src.model.modpack import ModpackBuild, ModpackBundle
from src.model.modrinth import ModrinthVersion
from src.model.utils import MinecraftLoaders
from src.services.modpack import ModpackService
from tests._actors import RecordingAudit
from tests.conftest import MongoRunner
from tests.test_builds import FakeLoaders

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_diff_splits_added_removed_changed_and_unchanged_sorted_by_project() -> None:
    result = diff_entries({"b": "1", "a": "1", "gone2": "9", "gone1": "9", "same": "3"},
                          {"z": "1", "y": "2", "a": "2", "b": "1", "same": "3"})
    assert result == ChangeSet(
        added=[Change("y", None, "2"), Change("z", None, "1")],
        removed=[Change("gone1", "9", None), Change("gone2", "9", None)],
        changed=[Change("a", "1", "2")],
        unchanged=2)


def test_diff_of_equal_locks_changes_nothing() -> None:
    result = diff_entries({"a": "1"}, {"a": "1"})
    assert result == ChangeSet(added=[], removed=[], changed=[], unchanged=1)


def test_diff_of_nothing_is_empty() -> None:
    assert diff_entries({}, {}) == ChangeSet(added=[], removed=[], changed=[], unchanged=0)


# La vista previa y la comparación de builds, contra MongoDB real.

def version_doc(version_id: str, project_id: str, changelog: str | None = None) -> ModrinthVersion:
    return ModrinthVersion.parse(
        id=version_id, project_id=project_id, author_id="a", name=version_id, version_number=version_id,
        version_type="release", status="listed", downloads=1, game_versions=["26.3"], loaders=["fabric"],
        featured=False, date_published="2020-01-01T00:00:00Z", dependencies=[], files=[], changelog=changelog)


class FakeAPI:
    """Modrinth: `GET /version/{id}` con el changelog que se le diga; cuenta las llamadas."""

    def __init__(self, changelogs: dict[str, str | None], failing: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.changelogs = changelogs
        self.failing = failing
        self.calls: list[str] = []
        self.running = 0
        self.peak = 0

    async def version_get(self, id: str) -> ModrinthVersion:
        self.calls.append(id)
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(0.01)
            if id in self.failing:
                raise RuntimeError("Modrinth down")
            if id not in self.changelogs:
                raise HTTP_404_NOT_FOUND(f"No version {id}")
            return version_doc(id, "P-" + id, self.changelogs[id])
        finally:
            self.running -= 1


def service(api: FakeAPI) -> ModpackService:
    return ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(), modrinthAPI=cast(Any, api),
                          loaderAPI=cast(Any, FakeLoaders()), audit=RecordingAudit())


def entry(project_id: str, version_id: str, server: str = "include") -> LockEntry:
    return LockEntry(project_id=project_id, origin="pack", version_id=version_id, version_number=version_id,
                     file=None, environment="client_and_server", server=server,  # type: ignore[arg-type]
                     reason="en el pack", requested_by=[])


def lock(*entries: LockEntry) -> Lock:
    return Lock(entries=list(entries), problems=[], suggestions=[], needs_projects=set(), needs_types=set(),
                needs_versions=set())


@pytest.mark.network
def test_the_preview_reads_changelogs_from_modrinth_then_from_the_catalog(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, list[str], list[str], str | None]:
        api = FakeAPI({"v2": "Arregla un crash", "v3": None})
        svc = service(api)
        before, after = lock(entry("P", "v1"), entry("Q", "q1")), lock(entry("P", "v2"), entry("Q", "q1"), entry("R", "v3"))
        first = await svc.change_preview(before, after)
        calls_first = list(api.calls)
        second = await svc.change_preview(before, after)
        stored = await ModrinthDatabase().version_find_by_version_id("v2")
        assert first.changelogs == second.changelogs
        return first, calls_first, api.calls[len(calls_first):], stored.changelog if stored else None

    first, calls_first, calls_second, stored = mongo(scenario)
    assert [c.project_id for c in first.changes.changed] == ["P"] and first.changes.unchanged == 1
    assert [c.project_id for c in first.changes.added] == ["R"]
    assert first.changelogs == {"v2": "Arregla un crash", "v3": None} and first.failed == set()
    assert sorted(calls_first) == ["v2", "v3"] and stored == "Arregla un crash"
    # Lo guardado no se vuelve a pedir; una versión sin changelog tampoco (se guarda vacía).
    assert calls_second == []


@pytest.mark.network
def test_a_changelog_the_catalog_has_is_not_asked_again(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[dict[str, str | None], list[str]]:
        await ModrinthDatabase().version_upsert_many([version_doc("v2", "P", "del catálogo")])
        api = FakeAPI({})
        preview = await service(api).change_preview(lock(entry("P", "v1")), lock(entry("P", "v2")))
        return preview.changelogs, api.calls

    assert mongo(scenario) == ({"v2": "del catálogo"}, [])


@pytest.mark.network
def test_a_modrinth_failure_marks_the_version_and_never_raises(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        api = FakeAPI({"v2": "bien"}, failing={"v3"})
        return await service(api).change_preview(
            lock(entry("P", "v1"), entry("Q", "q1")), lock(entry("P", "v2"), entry("Q", "v3"), entry("Z", "ghost")))

    preview = mongo(scenario)
    assert preview.failed == {"v3", "ghost"}
    assert preview.changelogs == {"v2": "bien"}


@pytest.mark.network
def test_modrinth_is_asked_at_most_eight_at_a_time(mongo: MongoRunner) -> None:
    async def scenario() -> int:
        ids = [f"v{i}" for i in range(30)]
        api = FakeAPI({i: "x" for i in ids})
        await service(api).change_preview(lock(), lock(*[entry("P" + i, i) for i in ids]))
        return api.peak

    assert 1 < mongo(scenario) <= 8


@pytest.mark.network
def test_a_refresh_from_a_listing_does_not_erase_a_stored_changelog(mongo: MongoRunner) -> None:
    async def scenario() -> str | None:
        db = ModrinthDatabase()
        await db.version_upsert_many([version_doc("v2", "P", "guardado")])
        # Modrinth lista versiones sin changelog (include_changelog=false): llega None.
        await db.version_upsert_many([version_doc("v2", "P", None)])
        stored = await db.version_find_by_version_id("v2")
        return stored.changelog if stored else "missing"

    assert mongo(scenario) == "guardado"


@pytest.mark.network
def test_documents_stored_before_changelogs_still_load(mongo: MongoRunner) -> None:
    async def scenario() -> str | None:
        raw = version_doc("old", "P").model_dump(exclude={"id", "revision_id", "changelog"})
        await ModrinthVersion.get_pymongo_collection().insert_one(raw)
        stored = await ModrinthDatabase().version_find_by_version_id("old")
        assert stored is not None
        return stored.changelog

    assert mongo(scenario) is None


@pytest.mark.network
def test_compare_builds_uses_the_locks_of_both_builds(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        svc = service(FakeAPI({"v2": "nuevo"}))
        created = await ModpackBundle(name="pack", description="", version="26.3",
                                      loader=MinecraftLoaders.fabric).insert()
        request = ResolveRequest(target=Target(game_version="26.3", loader="fabric", channels=frozenset({"release"})),
                                 projects=[])
        for number, entries in ((1, [entry("P", "v1"), entry("S", "s1", "exclude")]), (2, [entry("P", "v2")])):
            await ModpackDatabase().build_insert(ModpackBuild(
                bundle_id=created.id, number=number, created_at=T0, loader_version="0.16",  # type: ignore[arg-type]
                request=request, lock=lock(*entries)))
        preview = await svc.compare_builds(str(created.id), 1, 2)
        with pytest.raises(Exception) as missing:
            await svc.compare_builds(str(created.id), 1, 9)
        same = await svc.compare_builds(str(created.id), 2, 2)
        return preview, missing.value, same

    preview, missing, same = mongo(scenario)
    assert [(c.project_id, c.from_version, c.to_version) for c in preview.changes.changed] == [("P", "v1", "v2")]
    assert [c.project_id for c in preview.changes.removed] == ["S"]
    assert preview.changelogs == {"v2": "nuevo"}
    assert getattr(missing, "status_code", None) == 404
    assert (same.changes.changed, same.changes.added, same.changes.removed, same.changes.unchanged) == ([], [], [], 1)
