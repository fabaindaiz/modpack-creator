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
from src.database.pterodactyl import PterodactylDatabase
from src.exceptions import HTTP_404_NOT_FOUND
from src.library.changes import Change, ChangeSet, diff_entries
from src.library.scan import ScanFile
from src.library.resolution import Lock, LockEntry, ResolveRequest, Target
from src.model.modpack import ModpackBuild, ModpackBundle
from src.model.modrinth import ModrinthVersion
from src.model.pterodactyl import PterodactylServer, ServerScan
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
    """Modrinth: `GET /versions?ids=` con el changelog que se le diga (M6: en lote, con changelog
    por defecto); un id que no conoce no viene en la respuesta; uno en `failing` hace fallar el
    lote. `calls` guarda cada lote pedido."""

    def __init__(self, changelogs: dict[str, str | None], failing: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.changelogs = changelogs
        self.failing = failing
        self.batches: list[list[str]] = []

    @property
    def calls(self) -> list[str]:
        return [i for batch in self.batches for i in batch]

    async def version_get_many(self, ids: list[str]) -> list[ModrinthVersion]:
        self.batches.append(list(ids))
        await asyncio.sleep(0.01)
        if self.failing & set(ids):
            raise RuntimeError("Modrinth down")
        return [version_doc(i, "P-" + i, self.changelogs[i]) for i in ids if i in self.changelogs]


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
    assert sorted(calls_first) == ["v2", "v3"] and stored == "Arregla un crash"  # one request (M6)
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
    # One request for all: when it fails, every version it asked for is marked.
    assert preview.failed == {"v2", "v3", "ghost"} and preview.timed_out == set() and preview.changelogs == {}


@pytest.mark.network
def test_a_version_modrinth_does_not_return_is_marked_and_the_rest_arrive(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        return await service(FakeAPI({"v2": "bien"})).change_preview(lock(), lock(entry("P", "v2"), entry("Z", "ghost")))

    preview = mongo(scenario)
    assert preview.failed == {"ghost"} and preview.changelogs == {"v2": "bien"}


@pytest.mark.network
def test_changelogs_come_a_hundred_versions_a_request(mongo: MongoRunner) -> None:
    # M6: 150 changed mods after months without updating were 150 requests; now 2.
    async def scenario() -> list[int]:
        ids = [f"v{i:03}" for i in range(150)]
        api = FakeAPI({i: "x" for i in ids})
        await service(api).change_preview(lock(), lock(*[entry("P" + i, i) for i in ids]))
        return sorted(len(b) for b in api.batches)

    assert mongo(scenario) == [50, 100]


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


# La vista previa de un servidor: lo que tiene contra lo que pondría un build (N4).

def scan_file(path: str, project_id: str | None, version_id: str | None, status: str = "identified") -> ScanFile:
    return ScanFile(path=path, size=1, modified_at=T0, status=status,  # type: ignore[arg-type]
                    sha1="s", sha512="h-" + path, project_id=project_id, version_id=version_id)


async def seed_server(files: list[ScanFile] | None, entries: list[LockEntry]) -> str:
    """A server linked to a bundle with build 1 (with a scan, unless `files` is None)."""
    created = await ModpackBundle(name="pack", description="", version="26.3", loader=MinecraftLoaders.fabric).insert()
    request = ResolveRequest(target=Target(game_version="26.3", loader="fabric", channels=frozenset({"release"})),
                             projects=[])
    await ModpackDatabase().build_insert(ModpackBuild(
        bundle_id=created.id, number=1, created_at=T0, loader_version="0.16",  # type: ignore[arg-type]
        request=request, lock=lock(*entries)))
    server = await PterodactylDatabase().server_insert(PterodactylServer(
        identifier="1a2b3c4d", internal_id=1, uuid="1a2b3c4d-0000", name="Survival", node="n1",
        description="", status=None, is_suspended=False, is_installing=False, is_transferring=False,
        bundle_id=str(created.id)))
    if files is not None:
        await PterodactylDatabase().scan_insert(ServerScan(server_id=server.id, directory="/mods",  # type: ignore[arg-type]
                                                           scanned_at=T0, full=False, files=files))
    return str(server.id)


@pytest.mark.network
def test_a_server_preview_compares_the_scan_with_what_the_build_puts_on_the_server(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        files = [scan_file("/mods/p.jar", "P", "v1"), scan_file("/mods/q.jar", "Q", "q1"),
                 scan_file("/mods/gone.jar", "G", "g1"), scan_file("/mods/odd.jar", None, None, "unknown")]
        server_id = await seed_server(files, [entry("P", "v2"), entry("Q", "q1"), entry("N", "n1"),
                                                         entry("C", "c1", "exclude")])
        return await service(FakeAPI({"v2": "nuevo", "n1": None})).server_change_preview(server_id, 1)

    preview = mongo(scenario)
    assert [(c.project_id, c.from_version, c.to_version) for c in preview.changes.changed] == [("P", "v1", "v2")]
    assert [c.project_id for c in preview.changes.added] == ["N"]  # lo del cliente no va al servidor
    assert [c.project_id for c in preview.changes.removed] == ["G"]  # lo no identificado no cuenta
    assert preview.changes.unchanged == 1 and preview.changelogs == {"v2": "nuevo", "n1": None}


@pytest.mark.network
def test_a_server_preview_without_a_scan_is_a_conflict(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        server_id = await seed_server(None, [entry("P", "v2")])
        with pytest.raises(Exception) as error:
            await service(FakeAPI({})).server_change_preview(server_id, 1)
        return error.value

    error = mongo(scenario)
    assert getattr(error, "status_code", None) == 409 and "Escanea el servidor antes de comparar" in str(error.detail)


@pytest.mark.network
def test_a_server_preview_counts_the_mods_that_go_on_the_server_through_a_bridge(mongo: MongoRunner) -> None:
    """Batch C (C6): the bridged mods are listed apart, by bridge; a client-only one does not go there."""
    async def scenario() -> Any:
        bridged = [entry("P", "v2").model_copy(update={"via": "connector"}),
                   entry("Q", "q1").model_copy(update={"via": "connector"}),
                   entry("C", "c1", "exclude").model_copy(update={"via": "connector"})]
        server_id = await seed_server([scan_file("/mods/p.jar", "P", "v1")], [*bridged, entry("N", "n1")])
        return await service(FakeAPI({"v2": None, "q1": None, "n1": None})).server_change_preview(server_id, 1)

    assert mongo(scenario).bridged == {"Connector": 2}


class SlowAPI(FakeAPI):
    """Modrinth limitando: un lote con versiones lentas tarda mucho más que el plazo."""

    async def version_get_many(self, ids: list[str]) -> list[ModrinthVersion]:
        if any(i.startswith("slow") for i in ids):
            self.batches.append(list(ids))
            await asyncio.sleep(30)
        return await super().version_get_many(ids)


@pytest.mark.network
def test_the_preview_returns_by_its_deadline_and_marks_what_it_did_not_fetch(mongo: MongoRunner) -> None:
    import time

    async def scenario() -> Any:
        api = SlowAPI({"v2": "rápido", "slow1": "x", "slow2": "y"})
        svc = ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(), modrinthAPI=cast(Any, api),
                             loaderAPI=cast(Any, FakeLoaders()), audit=RecordingAudit(), changelog_deadline=0.3)
        started = time.monotonic()
        await ModrinthDatabase().version_upsert_many([version_doc("v2", "P", "rápido")])
        preview = await svc.change_preview(lock(), lock(entry("P", "v2"), entry("S1", "slow1"), entry("S2", "slow2")))
        return preview, time.monotonic() - started

    preview, took = mongo(scenario)
    assert took < 3
    assert preview.changelogs == {"v2": "rápido"} and preview.failed == set()
    assert preview.timed_out == {"slow1", "slow2"}  # not a failure: asking again may work


@pytest.mark.network
def test_the_preview_of_two_locks_carries_the_version_numbers_of_their_entries(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        first = entry("P", "id-1").model_copy(update={"version_number": "1.0.0"})
        second = entry("P", "id-2").model_copy(update={"version_number": "1.1.0"})
        kept = entry("K", "k-1").model_copy(update={"version_number": "9"})
        return await service(FakeAPI({"id-2": None})).change_preview(lock(first, kept), lock(second, kept))

    assert mongo(scenario).numbers == {"id-1": "1.0.0", "id-2": "1.1.0"}


@pytest.mark.network
def test_the_scan_side_takes_its_version_numbers_from_the_catalog_and_falls_back_to_the_id(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        await ModrinthDatabase().version_upsert_many([version_doc("v1", "P", "x").model_copy(update={"version_number": "0.9"})])
        files = [scan_file("/mods/p.jar", "P", "v1"), scan_file("/mods/g.jar", "G", "unknown-id")]
        server_id = await seed_server(files, [entry("P", "v2").model_copy(update={"version_number": "1.0"})])
        api = FakeAPI({"v2": None})
        preview = await service(api).server_change_preview(server_id, 1)
        return preview, [c for c in api.calls if c != "v2"]

    preview, extra_calls = mongo(scenario)
    assert preview.numbers == {"v1": "0.9", "v2": "1.0"}  # "unknown-id" has no number: the page shows the id
    assert extra_calls == []  # the numbers never go to Modrinth


def test_the_page_prints_version_numbers_and_the_timeout_text() -> None:
    from src.services.modpack import ChangePreview
    from src.web import templates

    env = templates.env  # the panel's: its tests and globals (ui.mod_link) are there
    changes = ChangeSet(added=[Change("A", None, "va")], removed=[Change("R", "vr", None)],
                        changed=[Change("C", "v1", "v2")], unchanged=0)
    preview = ChangePreview(changes=changes, changelogs={}, failed=set(), timed_out={"v2", "va"},
                            numbers={"v1": "1.0", "v2": "1.1"})
    text = env.get_template("modpacks/_changes.html").render(preview=preview, titles={})
    assert "1.0 → 1.1" in text and "— → va" in text and "vr → —" in text  # the id when there is no number
    assert text.count("tardó demasiado; vuelve a intentar") == 2 and "changelog no disponible" not in text


@pytest.mark.network
def test_update_preview_maps_a_check_to_changed_versions_with_numbers_and_skips_old_checks(mongo: MongoRunner) -> None:
    from src.model.modpack import AvailableUpdate

    async def scenario() -> Any:
        api = FakeAPI({"a2": "Nuevo"})  # b2 does not come back
        updates = [AvailableUpdate(project_id="A", from_version="1.0", to_version="2.0", from_version_id="a1", to_version_id="a2"),
                   AvailableUpdate(project_id="B", from_version="1.0", to_version="2.0", from_version_id="b1", to_version_id="b2"),
                   AvailableUpdate(project_id="OLD", from_version="1", to_version="2")]  # sin ids: antes de guardarlos
        return await service(api).update_preview(updates)

    preview = mongo(scenario)
    assert [(c.project_id, c.from_version, c.to_version) for c in preview.changes.changed] == [("A", "a1", "a2"), ("B", "b1", "b2")]
    assert preview.changes.added == [] and preview.changes.removed == []
    assert preview.numbers["a2"] == "2.0" and preview.numbers["a1"] == "1.0"
    assert preview.changelogs == {"a2": "Nuevo"} and preview.failed == {"b2"}


# Bajas de versión (F27, d-7985ee-322302): por fecha de publicación, como el piso del resolvedor.

def facts(day: int, game_versions: tuple[str, ...] = ("26.3",), loaders: tuple[str, ...] = ("fabric",)) -> Any:
    from src.library.changes import VersionFacts
    return VersionFacts(published=datetime(2026, 9, day, tzinfo=timezone.utc), game_versions=game_versions, loaders=loaders)


def test_direction_is_by_publication_date_and_a_branch_change_is_not_a_downgrade() -> None:
    from src.library.changes import direction

    assert direction(facts(10), facts(12)) == "up"
    assert direction(facts(12), facts(10)) == "down"
    assert direction(facts(10), facts(10)) == "up"  # same date: not a step back
    assert direction(facts(12), facts(10, game_versions=("1.21.1",))) == "branch"
    assert direction(facts(12), facts(10, loaders=("neoforge",))) == "branch"
    assert direction(facts(12), facts(10, loaders=("fabric", "quilt"))) == "down"
    assert direction(None, facts(10)) == "unknown" and direction(facts(10), None) == "unknown"
    # A date MongoDB gave back without a zone is UTC.
    from src.library.changes import VersionFacts
    naive = VersionFacts(published=datetime(2026, 9, 11), game_versions=("26.3",), loaders=("fabric",))
    assert direction(facts(12), naive) == "down"


def dated(version_id: str, project_id: str, day: int, game_versions: tuple[str, ...] = ("26.3",)) -> ModrinthVersion:
    return ModrinthVersion.parse(
        id=version_id, project_id=project_id, author_id="a", name=version_id, version_number="n-" + version_id,
        version_type="release", status="listed", downloads=1, game_versions=list(game_versions), loaders=["fabric"],
        featured=False, date_published=f"2026-09-{day:02d}T00:00:00Z", dependencies=[], files=[], changelog="")


@pytest.mark.network
def test_a_server_preview_marks_what_would_go_back_and_a_branch_change(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        await ModrinthDatabase().version_upsert_many([
            dated("j-new", "J", 20), dated("j-old", "J", 10), dated("s1", "S", 10), dated("s2", "S", 12),
            dated("m-a", "M", 20), dated("m-b", "M", 5, game_versions=("1.21.1",))])
        files = [scan_file("/mods/j.jar", "J", "j-new"), scan_file("/mods/s.jar", "S", "s1"),
                 scan_file("/mods/m.jar", "M", "m-a"), scan_file("/mods/u.jar", "U", "u1")]
        server_id = await seed_server(files, [entry("J", "j-old"), entry("S", "s2"), entry("M", "m-b"), entry("U", "u2")])
        return await service(FakeAPI({})).server_change_preview(server_id, 1)

    preview = mongo(scenario)
    assert preview.directions == {"J": "down", "M": "branch"}  # S sube; U: sin datos en el catálogo
    assert preview.downgrades == ["J"]


@pytest.mark.network
def test_a_server_preview_says_which_jars_would_stop_the_update_and_from_which_scan(mongo: MongoRunner) -> None:
    # F28 (d-7985ee-83e764): what the real update's preflight would refuse, read from the last scan.
    async def scenario() -> Any:
        files = [scan_file("/mods/p.jar", "P", "v1"), scan_file("/mods/odd.jar", None, None, "unknown"),
                 scan_file("/mods/x.jar", "X", "x1")]
        server_id = await seed_server(files, [entry("P", "v2")])
        clean = await seed_server([scan_file("/mods/p.jar", "P", "v1")], [entry("P", "v2")])
        api = service(FakeAPI({}))
        return await api.server_change_preview(server_id, 1), await api.server_change_preview(clean, 1)

    blocked, clean = mongo(scenario)
    assert blocked.unexplained is not None and blocked.unexplained.foreign == ["/mods/odd.jar", "/mods/x.jar"]
    assert blocked.scanned_at is not None and blocked.scanned_at.replace(tzinfo=timezone.utc) == T0
    assert clean.unexplained is None


def test_the_page_marks_each_downgrade_and_counts_them() -> None:
    from src.services.modpack import ChangePreview
    from src.web import templates

    changes = ChangeSet(added=[], removed=[], changed=[Change("J", "j1", "j0"), Change("M", "m1", "m0"), Change("S", "s1", "s2")],
                        unchanged=0)
    preview = ChangePreview(changes=changes, changelogs={}, failed=set(), numbers={"j1": "19.57", "j0": "19.51"},
                            directions={"J": "down", "M": "branch"}, pinned={"J"})
    text = templates.env.get_template("modpacks/_changes.html").render(preview=preview, titles={"J": "JEI"})
    assert "1 baja de versión" in text and "Bajar un mod puede romper mundos y configs." in text
    assert "baja, por un pin" in text and "cambio de rama" in text
    item = text[text.index('data-change="S"'):]
    assert "baja" not in item[:item.index("</li>")]
    none = ChangePreview(changes=changes, changelogs={}, failed=set())
    assert "baja de versión" not in templates.env.get_template("modpacks/_changes.html").render(preview=none, titles={})


# La comparación explicada de dos builds (O8 B4, d-7985ee-a99db7; user-walk W17, W18).


def _request(**fields: Any) -> ResolveRequest:
    target = fields.pop("target", Target(game_version="1.21.1", loader="fabric"))
    return ResolveRequest(target=target, projects=[], **fields)


def test_request_changes_skip_floors_and_missing_fields() -> None:
    from src.library.changes import request_changes
    before = _request(floors={"A": ["v1"]}, free={"A"})
    after = _request(floors={"A": ["v2"]}, unchecked={"A"})
    assert request_changes(before, after, {}) == []
    # A build stored before a field existed is not compared on it.
    old = ResolveRequest.model_validate({"target": {"game_version": "1.21.1", "loader": "fabric"}, "projects": []})
    assert request_changes(old, _request(native_only={"A"}), {}) == []


def test_request_changes_name_pins_and_channels() -> None:
    from src.library.changes import RequestChange, request_changes
    before = _request(target=Target(game_version="1.21.1", loader="fabric", channels=frozenset({"release"})), pins={},
                      server_decisions={})
    after = _request(target=Target(game_version="1.21.1", loader="fabric", channels=frozenset({"release", "beta"})),
                     pins={"A": "v2"}, server_decisions={"B": False})
    assert request_changes(before, after, {"v2": "0.6.1"}) == [
        RequestChange(field="channels", label="Canales", project_id=None, before="release", after="beta, release"),
        RequestChange(field="pins", label="Fijado", project_id="A", before="—", after="0.6.1"),
        RequestChange(field="server_decisions", label="En el servidor", project_id="B", before="—", after="no"),
    ]


def test_change_reason_prefers_the_pin_then_the_origin() -> None:
    from src.library.changes import change_reason

    def at(origin: str | None, requested_by: list[str]) -> LockEntry:
        return LockEntry(project_id="A", origin=origin, version_id="v", version_number="1", file=None,
                         environment=None, server="include", reason="elegida", requested_by=requested_by)  # type: ignore[arg-type]

    assert change_reason(at("pack", []), {"A": "v"}).kind == "pin"
    assert change_reason(at("admin", []), {}).kind == "admin" and change_reason(at("pack", []), {}).kind == "pack"
    dependency = change_reason(at(None, ["X", "Y"]), {})
    assert dependency.kind == "dependency" and dependency.requested_by == ("X", "Y")


def test_the_summary_counts_each_way_a_mod_moved() -> None:
    from src.library.changes import ChangeSummary, summary_of
    changes = ChangeSet(added=[Change("N", None, "n1")], removed=[],
                        changed=[Change("U", "u1", "u2"), Change("D", "d2", "d1"), Change("B", "b1", "b2")], unchanged=4)
    summary = summary_of(changes, {"U": "up", "D": "down", "B": "branch"})
    assert summary == ChangeSummary(up=1, down=1, branch=1, added=1, removed=0, other=0)
    assert summary.text() == "4 cambios: 1 sube, 1 baja, 1 cambia de rama, 1 nuevo"
    # A direction the catalog cannot tell is not said to go up (final review).
    unknown = summary_of(ChangeSet(added=[], removed=[], changed=[Change("U", "u1", "u2")], unchanged=0), {})
    assert unknown.text() == "1 cambio: 1 cambia de versión"


@pytest.mark.network
def test_compare_marks_pins_from_the_build_not_the_pack(mongo: MongoRunner) -> None:
    """W17: «baja, por un pin» was read from the pack's pins of today."""
    async def scenario() -> Any:
        svc = service(FakeAPI({"v2": "nuevo"}))
        created = await ModpackBundle(name="pack", description="", version="26.3", loader=MinecraftLoaders.fabric,
                                      pins={"Q": "q9"}).insert()
        for number, pins, entries in ((1, {}, [entry("P", "v1")]), (2, {"P": "v2"}, [entry("P", "v2")])):
            await ModpackDatabase().build_insert(ModpackBuild(
                bundle_id=created.id, number=number, created_at=T0, loader_version="0.16",  # type: ignore[arg-type]
                request=_request(pins=pins), lock=lock(*entries)))
        return await svc.compare_builds(str(created.id), 1, 2)

    preview = mongo(scenario)
    assert preview.pinned == {"P"} and preview.reasons["P"].kind == "pin"
    assert [(c.label, c.project_id, c.after) for c in preview.request] == [("Fijado", "P", "v2")]
    assert preview.summary is not None and preview.summary.text() == "1 cambio: 1 cambia de versión"


# What the knowledge base says where one updates (O8 tanda 3, K-Q3 of d-7985ee-57d185).


async def known_pair(a: str, b: str) -> None:
    """A confirmed entry: `a` and `b` fail together, any version; and the versions in the catalog, on 26.3."""
    from src.model.modpack.known_issue import IssueSide, KnownIssue
    from tests._actors import EDITOR
    from tests.test_update_check import version_doc
    # With their changelog read (empty): the preview asks Modrinth for nothing, and the fake keeps the catalog.
    await ModrinthDatabase().version_upsert_many([version_doc(v, p, v, changelog="") for p, v in
                                                  (("P", "v1"), ("P", "v2"), ("Q", "q1"), ("N", "n1"))])
    await KnownIssue.insert(KnownIssue(a=IssueSide(project_id=a, title=a), b=IssueSide(project_id=b, title=b),
                                       status="confirmed", summary="negro", author=EDITOR.ref(), at=T0, updated_at=T0,
                                       key="k"))


@pytest.mark.network
def test_a_server_preview_warns_of_a_known_pair(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        server_id = await seed_server([scan_file("/mods/p.jar", "P", "v1"), scan_file("/mods/q.jar", "Q", "q1")],
                                      [entry("P", "v2"), entry("Q", "q1")])
        await known_pair("P", "Q")
        return await service(FakeAPI({"v2": None})).server_change_preview(server_id, 1)

    assert sorted(mongo(scenario).known) == ["P", "Q"]


@pytest.mark.network
def test_the_updates_preview_warns_of_a_known_pair_in_the_whole_build(mongo: MongoRunner) -> None:
    """Q does not change, and still is half of the pair the update makes."""
    from src.model.modpack import AvailableUpdate

    async def scenario() -> Any:
        server_id = await seed_server(None, [entry("P", "v1"), entry("Q", "q1")])
        bundle_id = (await PterodactylDatabase().server_get(server_id)).bundle_id
        await known_pair("P", "Q")
        update = AvailableUpdate(project_id="P", from_version="v1", to_version="v2", from_version_id="v1", to_version_id="v2")
        return await service(FakeAPI({"v2": None})).update_preview([update], bundle_id=bundle_id, build_number=1)

    assert sorted(mongo(scenario).known) == ["P", "Q"]
