"""La ficha local de un mod (spec §2, "Ficha local de cada mod"): su identidad, dónde se usa (packs,
resoluciones guardadas, builds recomendado y último, servidores escaneados) y sus versiones del
catálogo. El catálogo, los bundles y los servidores son de MongoDB real; Modrinth es un doble que
anota sus llamadas: una ficha llama a Modrinth a lo más una vez."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence, cast

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.exceptions import UpstreamError
from src.library.resolution import ResolveRequest, Target
from src.library.scan import ScanFile
from src.model.modpack import ModpackBuild, ModpackBundle, ModpackResolution, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.model.modrinth.version import ModrinthVersionDependency
from src.model.pterodactyl import PterodactylServer, ServerScan
from src.model.utils import MinecraftLoaders
from src.services.modpack import ModpackService
from src.services.pterodactyl import SCAN_DIRECTORY
from tests._actors import RecordingAudit
from tests.conftest import MongoRunner
from tests.test_builds import FakeLoaders
from tests.test_mod_list import NOW, entry, lock, project, version

pytestmark = pytest.mark.network


def requiring(base: ModrinthVersion, *project_ids: str, kind: str = "required") -> ModrinthVersion:
    base.dependencies = [ModrinthVersionDependency(version_id=None, project_id=p, file_name=None,
                                                   dependency_type=cast(Any, kind)) for p in project_ids]
    return base


class FakeModrinth:
    """Anota cada llamada. `error` hace lanzar a todas; `delay` las hace tardar."""

    def __init__(self, projects: Sequence[ModrinthProject] = (), versions: Sequence[ModrinthVersion] = (),
                 error: Optional[Exception] = None, delay: float = 0.0) -> None:
        self.projects = {p.project_id: p for p in projects}
        self.versions = list(versions)
        self.error = error
        self.delay = delay
        self.calls: list[tuple[Any, ...]] = []

    async def _wait(self) -> None:
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error

    async def project_get_many(self, slugs: list[str]) -> list[ModrinthProject]:
        self.calls.append(("project_get_many", list(slugs)))
        await self._wait()
        return [p for p in self.projects.values() if p.project_id in slugs or p.slug in slugs]

    async def project_versions(self, slug: str, loaders: Sequence[str] = (), game_versions: Sequence[str] = (),
                               featured: Optional[bool] = None) -> list[ModrinthVersion]:
        self.calls.append(("project_versions", slug, tuple(loaders), tuple(game_versions)))
        await self._wait()
        return [v for v in self.versions if v.project_id == slug
                and (not loaders or set(loaders) & set(v.loaders))
                and (not game_versions or set(game_versions) & set(v.game_versions))]


class Scenario:
    def __init__(self, api: FakeModrinth, deadline: float = 5.0) -> None:
        self.api = api
        self.modpackDB = ModpackDatabase()
        self.modrinthDB = ModrinthDatabase()
        self.pterodactylDB = PterodactylDatabase()
        self.service = ModpackService(modpackDB=self.modpackDB, modrinthDB=self.modrinthDB, modrinthAPI=cast(Any, api),
                                      loaderAPI=cast(Any, FakeLoaders()), audit=RecordingAudit(),
                                      pterodactylDB=self.pterodactylDB, icon_deadline=deadline)

    async def bundle(self, name: str, projects: Sequence[tuple[str, ProjectOrigin]] = ()) -> str:
        created = await self.modpackDB.bundle_insert(ModpackBundle(
            name=name, description="", version="1.21.1", loader=MinecraftLoaders.fabric,
            projects=[PackProject(project_id=i, origin=o) for i, o in projects]))
        return str(created.id)

    async def resolved(self, bundle_id: str, *entries: Any) -> None:
        bundle = await self.modpackDB.bundle_get(bundle_id)
        await self.modpackDB.resolution_save(ModpackResolution(
            bundle_id=bundle.id, resolved_at=NOW, request_digest="d", lock=lock(*entries)))  # type: ignore[arg-type]

    async def built(self, bundle_id: str, number: int, *entries: Any) -> None:
        bundle = await self.modpackDB.bundle_get(bundle_id)
        request = ResolveRequest(target=Target(game_version="1.21.1", loader="fabric", channels=frozenset({"release"})),
                                 projects=[])
        await self.modpackDB.build_insert(ModpackBuild(bundle_id=bundle.id, number=number, created_at=NOW,  # type: ignore[arg-type]
                                                       request=request, lock=lock(*entries)))
        await self.modpackDB.bundle_latest_build(bundle_id, number)

    async def server(self, name: str, *files: ScanFile, uuid: str = "u-1") -> str:
        server = await self.pterodactylDB.server_insert(PterodactylServer(
            identifier=uuid[:8], internal_id=1, uuid=uuid, name=name, node="n1", description="", status=None,
            is_suspended=False, is_installing=False, is_transferring=False))
        if files:
            await self.pterodactylDB.scan_insert(ServerScan(server_id=server.id, directory=SCAN_DIRECTORY,  # type: ignore[arg-type]
                                                            scanned_at=NOW, full=False, files=list(files)))
        return str(server.id)


def jar(path: str, project_id: Optional[str], version_id: Optional[str], status: str = "identified") -> ScanFile:
    return ScanFile(path=path, size=1, modified_at=NOW, status=cast(Any, status), sha1="s1", sha512="s5" + path,
                    project_id=project_id, version_id=version_id)


def test_page_lists_packs_dependency_builds_and_servers(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P", "P title"), project("X", "X title")])
        await s.modrinthDB.version_upsert_many([version("v-P-0.8.13", "P", "0.8.13", 1)])
        a = await s.bundle("A", [("P", ProjectOrigin.admin)])
        await s.built(a, 1, entry("P", "0.8.13", origin="admin"))
        await s.built(a, 2, entry("P", "0.8.14", origin="admin"))
        await s.modpackDB.bundle_recommend_build(a, 1)
        b = await s.bundle("B", [("X", ProjectOrigin.pack)])
        await s.resolved(b, entry("X", "1"), entry("P", "0.8.13", origin=None, requested_by=["X"]))
        await s.bundle("C", [("X", ProjectOrigin.pack)])  # does not use P
        await s.server("S", jar("/mods/p.jar", "P", "v-P-0.8.13"), jar("/mods/x.jar", "X", "v-X"))
        await s.server("T", jar("/mods/x.jar", "X", "v-X"), uuid="u-2")  # does not have P
        await s.server("U", uuid="u-3")  # never scanned
        page = await s.service.mod_page("P")
        return page, a, b, s.api.calls

    page, a, b, calls = mongo(scenario)
    assert page.project.title == "P title"
    assert [(u.bundle_name, u.origin, u.dependency_of) for u in page.uses] == [("A", "admin", []), ("B", None, ["X title"])]
    assert [u.bundle_id for u in page.uses] == [a, b]
    assert page.uses[0].recommended == (1, "0.8.13") and page.uses[0].latest == (2, "0.8.14")
    assert page.uses[1].recommended is None and page.uses[1].latest is None  # B has no build
    assert [(s.server_name, s.version, s.scanned_at.replace(tzinfo=timezone.utc)) for s in page.servers] == [
        ("S", "0.8.13", NOW)]
    assert calls == []  # everything shown was in the catalog, with an icon


def test_only_each_servers_latest_scan_counts_and_one_query_finds_them(mongo: MongoRunner) -> None:
    # F4 of the final review: the page read every server's latest scan, one query each.
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P", "P title")])
        await s.modrinthDB.version_upsert_many([version("v-old", "P", "1.0", 1), version("v-new", "P", "2.0", 2)])
        later = NOW + timedelta(hours=1)
        moved = await s.server("Moved", jar("/mods/p.jar", "P", "v-old"))
        await s.pterodactylDB.scan_insert(ServerScan(server_id=PydanticObjectId(moved), directory=SCAN_DIRECTORY,
                                                     scanned_at=later, full=False, files=[jar("/mods/p.jar", "P", "v-new")]))
        gone = await s.server("Gone", jar("/mods/p.jar", "P", "v-old"), uuid="u-2")
        await s.pterodactylDB.scan_insert(ServerScan(server_id=PydanticObjectId(gone), directory=SCAN_DIRECTORY,
                                                     scanned_at=later, full=False, files=[jar("/mods/x.jar", "X", "v-X")]))
        await s.pterodactylDB.scan_insert(ServerScan(server_id=PydanticObjectId(gone), directory="/plugins",
                                                     scanned_at=later + timedelta(hours=1), full=False,
                                                     files=[jar("/plugins/p.jar", "P", "v-new")]))
        await s.server("Unknown", jar("/mods/p.jar", None, None, status="unknown"), uuid="u-3")

        async def per_server(*args: Any) -> Any:
            raise AssertionError("one query per server")

        s.pterodactylDB.scan_latest = per_server  # type: ignore[method-assign,assignment]
        return await s.service.mod_page("P")

    page = mongo(scenario)
    assert [(s.server_name, s.version, s.scanned_at.replace(tzinfo=timezone.utc)) for s in page.servers] == [
        ("Moved", "2.0", NOW + timedelta(hours=1))]


def test_a_build_without_the_mod_is_not_named(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P", "P title"), project("Q", "Q title")])
        a = await s.bundle("A", [("P", ProjectOrigin.admin)])
        await s.built(a, 1, entry("P", "1.0", origin="admin"))
        await s.built(a, 2, entry("Q", "2.0"))  # P was added after the latest build... or removed from it
        await s.modpackDB.bundle_recommend_build(a, 1)
        return (await s.service.mod_page("P")).uses

    [use] = mongo(scenario)
    assert use.recommended == (1, "1.0") and use.latest is None


def test_a_project_missing_from_the_catalog_is_fetched_with_its_missing_relatives(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        api = FakeModrinth(projects=[project("P", "P title"), project("D1", "D1 title"), project("D2", "D2 title")])
        s = Scenario(api)
        # The catalog has P's versions (a resolution fetched them) but not P; D2 is there, D1 is not.
        await s.modrinthDB.project_upsert_many([project("D2", "D2 title")])
        await s.modrinthDB.version_upsert_many([requiring(version("v1", "P", "1.0", 1), "D1", "D2"),
                                                requiring(version("v2", "P", "2.0", 2), "O1", kind="optional")])
        page = await s.service.mod_page("P")
        stored = await s.modrinthDB.project_find_by_modrinth_id(["P", "D1"])
        return page, api.calls, sorted(p.project_id for p in stored)

    page, calls, stored = mongo(scenario)
    assert calls == [("project_get_many", ["P", "D1"])]  # one call; the optional one is not a relative
    assert page.project.title == "P title" and stored == ["D1", "P"]
    assert page.dependencies == ["D1", "D2"] and page.titles["D1"] == "D1 title" and page.titles["D2"] == "D2 title"


def test_a_stored_project_fills_what_it_shows_in_one_call(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        api = FakeModrinth(projects=[project("D1", "D1 title"), project("X", "X title"), project("Y", "Y title")])
        s = Scenario(api)
        await s.modrinthDB.project_upsert_many([project("P", "P title")])
        await s.modrinthDB.version_upsert_many([requiring(version("v1", "P", "1.0", 1), "D1")])
        b = await s.bundle("B", [("X", ProjectOrigin.pack)])
        await s.resolved(b, entry("X", "1"), entry("P", "1.0", origin=None, requested_by=["X"]))
        page = await s.service.mod_page("P", named=["Y"])
        return page, api.calls

    page, calls = mongo(scenario)
    assert calls == [("project_get_many", ["D1", "X", "Y"])]
    assert page.uses[0].dependency_of == ["X title"] and page.titles["Y"] == "Y title"


def test_a_stored_project_survives_modrinth_down(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth(error=UpstreamError("Modrinth", 502, raw="down")))
        await s.modrinthDB.project_upsert_many([project("P", "P title")])
        await s.modrinthDB.version_upsert_many([requiring(version("v1", "P", "1.0", 1), "D1")])
        return await s.service.mod_page("P")

    page = mongo(scenario)
    assert page.project.title == "P title" and page.titles["D1"] == "D1"  # shown by id, never an error


def test_a_slug_finds_the_project_and_what_is_stored_under_its_id(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        api = FakeModrinth(projects=[project("P", "Sodium")])
        s = Scenario(api)
        await s.bundle("A", [("P", ProjectOrigin.admin)])
        fetched = await s.service.mod_page("sodium")  # not in the catalog yet: Modrinth answers by slug
        stored = await s.service.mod_page("sodium")  # now the catalog has it, by slug too
        return fetched, stored, api.calls

    fetched, stored, calls = mongo(scenario)
    for page in (fetched, stored):
        assert page.project.project_id == "P" and [u.bundle_name for u in page.uses] == ["A"]
    assert calls == [("project_get_many", ["sodium"])]


def test_a_slug_fetches_the_relatives_stored_under_its_id(mongo: MongoRunner) -> None:
    # By slug, the relatives are only known once Modrinth says the project's id: they are filled
    # after it, best effort, so they show by title and never as ids.
    async def scenario() -> Any:
        api = FakeModrinth(projects=[project("P", "Sodium"), project("D1", "D1 title"), project("X", "X title")])
        s = Scenario(api)
        await s.modrinthDB.version_upsert_many([requiring(version("v1", "P", "1.0", 1), "D1")])
        b = await s.bundle("B", [("X", ProjectOrigin.pack)])
        await s.resolved(b, entry("X", "1"), entry("P", "1.0", origin=None, requested_by=["X"]))
        page = await s.service.mod_page("sodium")
        return page, api.calls

    page, calls = mongo(scenario)
    assert page.project.project_id == "P" and page.dependencies == ["D1"]
    assert page.titles["D1"] == "D1 title" and page.uses[0].dependency_of == ["X title"]
    assert page.uses[0].requested_by == ["X"]
    assert calls == [("project_get_many", ["sodium"]), ("project_get_many", ["D1", "X"])]


def test_a_slug_with_modrinth_down_for_the_relatives_still_shows_the_project(mongo: MongoRunner) -> None:
    class FirstOnly(FakeModrinth):
        async def project_get_many(self, slugs: list[str]) -> list[ModrinthProject]:
            if self.calls:
                self.calls.append(("project_get_many", list(slugs)))
                raise UpstreamError("Modrinth", 502, raw="down")
            return await super().project_get_many(slugs)

    async def scenario() -> Any:
        s = Scenario(FirstOnly(projects=[project("P", "Sodium")]))
        await s.modrinthDB.version_upsert_many([requiring(version("v1", "P", "1.0", 1), "D1")])
        return await s.service.mod_page("sodium")

    page = mongo(scenario)
    assert page.project.title == "Sodium" and page.titles["D1"] == "D1"  # by id, never an error


def test_from_a_pack_the_page_marks_its_version_and_loader(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P", "P title", loaders=("fabric",),
                                                        game_versions=("1.20.1", "1.21.1"))])
        a = await s.bundle("A")  # 1.21.1, fabric; it does not need to have the mod
        other = await s.modpackDB.bundle_insert(ModpackBundle(
            name="Viejo", description="", version="1.12.2", loader=MinecraftLoaders.forge, projects=[]))
        marked = await s.service.mod_page("P", pack=a)
        unsupported = await s.service.mod_page("P", pack=str(other.id))
        unknown = await s.service.mod_page("P", pack="0123456789abcdef01234567")
        invalid = await s.service.mod_page("P", pack="not-an-id")
        return a, marked, unsupported, unknown, invalid

    a, marked, unsupported, unknown, invalid = mongo(scenario)
    assert marked.pack is not None and marked.pack.bundle_id == a and marked.pack.name == "A"
    assert (marked.pack.game_version, marked.pack.loader) == ("1.21.1", "fabric")
    assert marked.pack.supports_version is True and marked.pack.supports_loader is True
    assert unsupported.pack is not None and unsupported.pack.game_version == "1.12.2"
    assert unsupported.pack.supports_version is False and unsupported.pack.supports_loader is False
    assert unknown.pack is None and invalid.pack is None  # ignored, never an error


def test_from_a_pack_with_only_a_beta_for_its_version_the_mark_says_so(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P", "P title", game_versions=("1.20.1", "1.21.1"))])
        await s.modrinthDB.version_upsert_many([version("b1", "P", "2.0-beta", 1, kind="beta"),
                                                version("r0", "P", "1.0", 2, game_versions=["1.20.1"])])
        a = await s.bundle("A")  # 1.21.1, fabric, release only
        return (await s.service.mod_page("P", pack=a)).pack

    mark = mongo(scenario)
    assert mark is not None and mark.version_channel == "beta"


def test_unknown_to_modrinth_is_a_404(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        with pytest.raises(HTTPException) as raised:
            await s.service.mod_page("NOPE")
        return raised.value

    error = mongo(scenario)
    assert error.status_code == 404 and not isinstance(error, UpstreamError)
    assert "Modrinth no conoce" in error.detail


def test_a_missing_project_with_modrinth_down_raises_the_upstream_error(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth(error=UpstreamError("Modrinth", 502, raw="down")))
        with pytest.raises(UpstreamError):
            await s.service.mod_page("P")
        slow = Scenario(FakeModrinth(projects=[project("P", "P title")], delay=1.0), deadline=0.05)
        with pytest.raises(TimeoutError):
            await slow.service.mod_page("P")

    mongo(scenario)


def test_catalog_versions_are_newest_first_capped_and_partial(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P", "P title")])
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        many = [version(f"v{i}", "P", f"1.{i}", 1) for i in range(55)]
        for i, v in enumerate(many):
            v.date_published = base + timedelta(days=i)
        await s.modrinthDB.version_upsert_many([*many, version("other", "Q", "9", 1)])
        return await s.service.mod_page("P")

    page = mongo(scenario)
    assert len(page.versions) == 50 and page.versions_partial is True
    assert page.versions[0].version_number == "1.54" and page.versions[-1].version_number == "1.5"
    row = page.versions[0]
    assert (row.version_id, row.channel, row.game_versions, row.loaders) == ("v54", "release", ["1.21.1"], ["fabric"])


def test_all_versions_are_fetched_without_filters_and_stored(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        api = FakeModrinth(versions=[version("a", "P", "1.0", 1, loaders=("forge",), game_versions=("1.12.2",)),
                                     version("b", "P", "2.0", 3, kind="beta"), version("c", "Q", "9", 2)])
        s = Scenario(api)
        rows = await s.service.mod_versions_all("P")
        stored = await s.modrinthDB.version_find_by_version_ids(["a", "b", "c"])
        return rows, api.calls, sorted(v.version_id for v in stored)

    rows, calls, stored = mongo(scenario)
    assert calls[-1] == ("project_versions", "P", (), ())
    assert [(r.version_number, r.channel) for r in rows] == [("2.0", "beta"), ("1.0", "release")]
    assert stored == ["a", "b"]


def test_all_versions_let_modrinth_failing_through(mongo: MongoRunner) -> None:
    async def scenario() -> None:
        s = Scenario(FakeModrinth(error=UpstreamError("Modrinth", 503, raw="down")))
        with pytest.raises(UpstreamError):
            await s.service.mod_versions_all("P")
        slow = Scenario(FakeModrinth(delay=1.0), deadline=0.05)
        with pytest.raises(TimeoutError):
            await slow.service.mod_versions_all("P")

    mongo(scenario)
