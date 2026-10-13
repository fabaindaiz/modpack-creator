"""La lista de mods de un modpack y las versiones que se pueden fijar (spec §1, Ficha de mod): una
fila por proyecto del pack y por dependencia resuelta, con los datos de la ficha. El catálogo y los
bundles son de MongoDB real; Modrinth es un doble que anota sus llamadas."""
import asyncio
from datetime import datetime, timezone
from typing import Any, Optional, Sequence, cast

import pytest

from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.library.resolution import Lock, LockEntry, ResolveRequest, Target
from src.model.modpack import (AvailableUpdate, ModpackBuild, ModpackBundle, ModpackResolution, PackProject,
                               ProjectOrigin, UpdateCheck)
from src.model.modrinth import ModrinthProject, ModrinthVersion, ModrinthVersionList
from src.model.utils import MinecraftLoaders
from src.services.modpack import ModpackService
from tests._actors import EDITOR, RecordingAudit
from tests.conftest import MongoRunner
from tests.test_builds import FakeLoaders

pytestmark = pytest.mark.network

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def project(project_id: str, title: str, icon: Optional[str] = "https://cdn.modrinth.com/x.png", loaders: Sequence[str] = ("fabric",),
            game_versions: Sequence[str] = ("1.21.1",), project_type: str = "mod") -> ModrinthProject:
    return ModrinthProject.parse(
        id=project_id, slug=title.lower(), team="t", title=title, description=f"About {title}", body="",
        project_type=project_type, status="approved", downloads=7, followers=1, categories=["optimization"],
        additional_categories=[], versions=[], game_versions=list(game_versions), loaders=list(loaders),
        issues_url="https://example.org/issues", source_url=None, wiki_url=None, icon_url=icon,
        published="2020-01-01T00:00:00Z", updated="2020-01-01T00:00:00Z")


def version(version_id: str, project_id: str, number: str, day: int, kind: str = "release",
            loaders: Sequence[str] = ("fabric",), game_versions: Sequence[str] = ("1.21.1",)) -> ModrinthVersion:
    return ModrinthVersion.parse(
        id=version_id, project_id=project_id, author_id="a", name=number, version_number=number,
        version_type=kind, status="listed", downloads=1, game_versions=list(game_versions), loaders=list(loaders),
        featured=False, date_published=f"2026-09-{day:02d}T00:00:00Z", dependencies=[], files=[], changelog=None)


class FakeModrinth:
    """Anota cada llamada; `fail` la hace lanzar, `delay` la hace tardar."""

    def __init__(self, projects: Sequence[ModrinthProject] = (), versions: Sequence[ModrinthVersion] = (),
                 fail: bool = False, delay: float = 0.0) -> None:
        self.projects = {p.project_id: p for p in projects}
        self.versions = list(versions)
        self.fail = fail
        self.delay = delay
        self.calls: list[tuple[str, Any]] = []

    async def project_get_many(self, slugs: list[str]) -> list[ModrinthProject]:
        self.calls.append(("project_get_many", list(slugs)))
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("Modrinth is down")
        return [self.projects[s] for s in slugs if s in self.projects]

    async def project_versions(self, slug: str, loaders: Sequence[str] = (), game_versions: Sequence[str] = (),
                               featured: Optional[bool] = None) -> list[ModrinthVersion]:
        self.calls.append(("project_versions", (slug, tuple(loaders), tuple(game_versions))))
        return [v for v in self.versions if v.project_id == slug and set(loaders) & set(v.loaders)
                and set(game_versions) & set(v.game_versions)]


def entry(project_id: str, number: str, origin: Optional[str] = "pack", requested_by: Sequence[str] = (),
          server: str = "include", project_type: str = "mod") -> LockEntry:
    return LockEntry(project_id=project_id, origin=origin, project_type=project_type, version_id=f"v-{project_id}-{number}",
                     version_number=number, file=None, environment=None, server=cast(Any, server), reason="r",
                     requested_by=list(requested_by))


def lock(*entries: LockEntry) -> Lock:
    return Lock(entries=list(entries), problems=[], suggestions=[], needs_projects=set(), needs_types=set(),
                needs_versions=set())


class Scenario:
    def __init__(self, api: FakeModrinth, icon_deadline: float = 5.0) -> None:
        self.api = api
        self.modpackDB = ModpackDatabase()
        self.modrinthDB = ModrinthDatabase()
        self.service = ModpackService(modpackDB=self.modpackDB, modrinthDB=self.modrinthDB, modrinthAPI=cast(Any, api),
                                      loaderAPI=cast(Any, FakeLoaders()), audit=RecordingAudit(), icon_deadline=icon_deadline)

    async def bundle(self, projects: Sequence[tuple[str, ProjectOrigin]], pins: Optional[dict[str, str]] = None,
                     channels: Sequence[str] = ("release",)) -> str:
        created = await self.modpackDB.bundle_insert(ModpackBundle(
            name="pack", description="", version="1.21.1", loader=MinecraftLoaders.fabric, channels=list(channels),
            pins=pins or {}, projects=[PackProject(project_id=i, origin=o) for i, o in projects]))
        return str(created.id)

    async def resolved(self, bundle_id: str, *entries: LockEntry) -> None:
        bundle = await self.modpackDB.bundle_get(bundle_id)
        await self.modpackDB.resolution_save(ModpackResolution(
            bundle_id=bundle.id, resolved_at=NOW, request_digest="d", lock=lock(*entries)))  # type: ignore[arg-type]

    async def built(self, bundle_id: str, *entries: LockEntry, number: int = 1) -> None:
        bundle = await self.modpackDB.bundle_get(bundle_id)
        request = ResolveRequest(target=Target(game_version="1.21.1", loader="fabric", channels=frozenset({"release"})),
                                 projects=[])
        await self.modpackDB.build_insert(ModpackBuild(bundle_id=bundle.id, number=number, created_at=NOW,  # type: ignore[arg-type]
                                                       request=request, lock=lock(*entries)))
        await self.modpackDB.bundle_latest_build(bundle_id, number)


def test_rows_join_pack_projects_and_resolved_dependencies(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium"), project("P9", "Lithium")])
        bundle_id = await s.bundle([("P1", ProjectOrigin.admin)])
        await s.resolved(bundle_id, entry("P1", "1", origin="admin"), entry("P9", "2", origin=None, requested_by=["P1"]))
        listed = await s.service.mod_list(bundle_id, check=None)
        return [(r.project_id, r.origin, r.required_by, r.side) for r in listed.rows], listed.stale, listed.resolution is not None

    rows, stale, has_resolution = mongo(scenario)
    assert rows == [("P1", "admin", [], "include"), ("P9", None, ["Sodium"], "include")]  # titles, never ids
    assert stale is True and has_resolution  # the saved digest is not the bundle's


def test_without_a_resolution_the_rows_are_the_pack_projects(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium"), project("P2", "Iris")])
        bundle_id = await s.bundle([("P2", ProjectOrigin.pack), ("P1", ProjectOrigin.admin)])
        await s.built(bundle_id, entry("P1", "0.8.13"))
        listed = await s.service.mod_list(bundle_id, check=None)
        return [(r.project_id, r.side, r.build_version, r.title) for r in listed.rows], listed.resolution

    rows, resolution = mongo(scenario)
    assert rows == [("P2", None, None, "Iris"), ("P1", None, "0.8.13", "Sodium")]  # the pack's order
    assert resolution is None


def test_dependencies_come_after_the_pack_by_title(mongo: MongoRunner) -> None:
    async def scenario() -> list[str]:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium"), project("D1", "Zeta"), project("D2", "Alpha")])
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack)])
        await s.resolved(bundle_id, entry("D1", "1", origin=None), entry("P1", "1"), entry("D2", "1", origin=None))
        return [r.project_id for r in (await s.service.mod_list(bundle_id, check=None)).rows]

    assert mongo(scenario) == ["P1", "D2", "D1"]


def test_build_version_and_newer_version_from_the_check(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium"), project("P2", "Iris")])
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack), ("P2", ProjectOrigin.pack)])
        await s.built(bundle_id, entry("P1", "0.8.13"), entry("P2", "1.0"))
        check = UpdateCheck(bundle_id=(await s.modpackDB.bundle_get(bundle_id)).id, build_number=1, checked_at=NOW,  # type: ignore[arg-type]
                            updates=[AvailableUpdate(project_id="P1", from_version="0.8.13", to_version="0.8.14")])
        rows = {r.project_id: r for r in (await s.service.mod_list(bundle_id, check=check)).rows}
        return [(r.build_version, r.newer_version, r.pinned) for r in rows.values()]

    assert mongo(scenario) == [("0.8.13", "0.8.14", False), ("1.0", None, False)]


def test_newer_versions_only_from_a_check_of_the_latest_build(mongo: MongoRunner) -> None:
    """The version shown is the latest build's; a check of another build (the recommended one, older)
    compares against other versions, so its "→ newer" is not shown next to it."""
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium")])
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack)])
        await s.built(bundle_id, entry("P1", "0.8.13"))
        await s.built(bundle_id, entry("P1", "0.8.14"), number=2)
        check = UpdateCheck(bundle_id=(await s.modpackDB.bundle_get(bundle_id)).id, build_number=1, checked_at=NOW,  # type: ignore[arg-type]
                            updates=[AvailableUpdate(project_id="P1", from_version="0.8.13", to_version="0.8.14")])
        return [(r.build_version, r.newer_version) for r in (await s.service.mod_list(bundle_id, check=check)).rows]

    assert mongo(scenario) == [("0.8.14", None)]


def test_pinned_rows_never_show_a_newer_version(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium"), project("P2", "Iris")])
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack), ("P2", ProjectOrigin.pack)], pins={"P1": "v-P1-0.8.13"})
        await s.built(bundle_id, entry("P1", "0.8.13"), entry("P2", "1.0"))
        check = UpdateCheck(bundle_id=(await s.modpackDB.bundle_get(bundle_id)).id, build_number=1, checked_at=NOW,  # type: ignore[arg-type]
                            updates=[AvailableUpdate(project_id="P1", from_version="0.8.13", to_version="0.8.14"),
                                     AvailableUpdate(project_id="P2", from_version="1.0", to_version="1.1", pinned=True)])
        return [(r.project_id, r.pinned, r.newer_version) for r in (await s.service.mod_list(bundle_id, check=check)).rows]

    assert mongo(scenario) == [("P1", True, None), ("P2", False, None)]


def test_a_removed_project_leaves_the_list(mongo: MongoRunner) -> None:
    """Removing a project does not resolve again: its entry stays in the saved lock, but only the
    resolution's real dependencies (no origin) are rows besides the pack's projects."""
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium"), project("P2", "Iris"), project("D1", "Lib"),
                                                project("P3", "Old")])
        bundle_id = await s.bundle([("P1", ProjectOrigin.admin), ("P2", ProjectOrigin.admin), ("P3", ProjectOrigin.pack)])
        await s.resolved(bundle_id, entry("P1", "1", origin="admin"), entry("P2", "1", origin="admin"),
                         entry("P3", "1", origin="pack"), entry("D1", "1", origin=None, requested_by=["P1"]))
        await s.service.project_remove(bundle_id, "P2", EDITOR)
        await s.service.pack_replace(bundle_id, [], EDITOR)
        return [r.project_id for r in (await s.service.mod_list(bundle_id, check=None)).rows]

    assert mongo(scenario) == ["P1", "D1"]


def test_pins_outside_every_row_are_listed_with_their_titles(mongo: MongoRunner) -> None:
    """A pin on a project that left the pack (and is not a dependency) has no row: the list names
    it, titled from the same single fill, so it can still be removed."""
    async def scenario() -> Any:
        api = FakeModrinth([project("GONE", "Viejo")])
        s = Scenario(api)
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium"), project("D1", "Lib")])
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack)], pins={"P1": "v1", "D1": "v2", "GONE": "v3"})
        await s.resolved(bundle_id, entry("P1", "1"), entry("D1", "1", origin=None, requested_by=["P1"]))
        listed = await s.service.mod_list(bundle_id, check=None)
        return listed.orphan_pins, listed.titles.get("GONE"), api.calls

    orphans, title, calls = mongo(scenario)
    assert orphans == ["GONE"] and title == "Viejo"
    assert calls == [("project_get_many", ["GONE"])]


def test_channel_exceptions_outside_every_row_have_their_titles(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        api = FakeModrinth([project("GONE", "Viejo")])
        s = Scenario(api)
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium")])
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack)])
        await s.modpackDB.bundle_channel_exception_set(bundle_id, "GONE", "beta")
        return (await s.service.mod_list(bundle_id, check=None)).titles.get("GONE")

    assert mongo(scenario) == "Viejo"


def test_missing_icons_are_filled_in_one_batch_call(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        api = FakeModrinth([project("P1", "Sodium"), project("P2", "Iris")])
        s = Scenario(api)
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium", icon=None), project("P3", "Lithium")])
        # Stored before projects carried when they were fetched: worth asking again for its icon.
        await ModrinthProject.get_pymongo_collection().update_one({"project_id": "P1"}, {"$unset": {"fetched_at": ""}})
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack), ("P2", ProjectOrigin.pack), ("P3", ProjectOrigin.pack)])
        listed = await s.service.mod_list(bundle_id, check=None)
        return api.calls, [(r.project_id, r.icon_url) for r in listed.rows], listed.icons_missing

    calls, icons, missing = mongo(scenario)
    assert calls == [("project_get_many", ["P1", "P2"])]
    assert icons == [("P1", "https://cdn.modrinth.com/x.png"), ("P2", "https://cdn.modrinth.com/x.png"),
                     ("P3", "https://cdn.modrinth.com/x.png")]
    assert missing == 0


@pytest.mark.parametrize("api", [FakeModrinth(fail=True), FakeModrinth(delay=30.0)], ids=["fails", "too slow"])
def test_modrinth_down_still_lists_without_icons(mongo: MongoRunner, api: FakeModrinth) -> None:
    async def scenario() -> Any:
        s = Scenario(api, icon_deadline=0.05)
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium", icon=None)])
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack), ("P2", ProjectOrigin.pack)])
        started = asyncio.get_running_loop().time()
        listed = await s.service.mod_list(bundle_id, check=None)
        took = asyncio.get_running_loop().time() - started
        return took, [(r.project_id, r.title, r.icon_url) for r in listed.rows], listed.icons_missing

    took, rows, missing = mongo(scenario)
    assert took < 1.0
    assert rows == [("P1", "Sodium", None), ("P2", "P2", None)]  # P2 is not in the catalog: shown by its id
    assert missing == 2


def test_suggestion_and_problem_titles_come_from_the_same_single_fill(mongo: MongoRunner) -> None:
    from src.library.resolution import Problem, Suggestion

    async def scenario(api: FakeModrinth, deadline: float) -> Any:
        s = Scenario(api, icon_deadline=deadline)
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium")])
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack)])
        bundle = await s.modpackDB.bundle_get(bundle_id)
        saved = lock(entry("P1", "1"))
        saved.suggestions = [Suggestion(project_id="S1", requested_by=["P1"])]
        saved.problems = [Problem(kind="unresolvable", project_id="X1", detail="sin versión")]
        await s.modpackDB.resolution_save(ModpackResolution(
            bundle_id=bundle.id, resolved_at=NOW, request_digest="d", lock=saved))  # type: ignore[arg-type]
        started = asyncio.get_running_loop().time()
        listed = await s.service.mod_list(bundle_id, check=None)
        return asyncio.get_running_loop().time() - started, listed.titles, api.calls

    def working() -> Any:
        return scenario(FakeModrinth([project("S1", "Mod Menu"), project("X1", "Roto")]), 5.0)

    def hanging() -> Any:
        return scenario(FakeModrinth(delay=30.0), 0.2)

    _, titles, calls = mongo(working)
    assert calls == [("project_get_many", ["S1", "X1"])]  # one batch call for icons and titles together
    assert titles == {"P1": "Sodium", "S1": "Mod Menu", "X1": "Roto"}
    took, titles, calls = mongo(hanging)
    assert took < 0.2 * 2 and len(calls) == 1  # one deadline, never two in a row
    assert titles == {"P1": "Sodium", "S1": "S1", "X1": "X1"}


def test_problems_name_who_asked_and_the_other_side_and_have_a_card(mongo: MongoRunner) -> None:
    # "Con problemas" shows each problem with its mod's card and its text with titles (spec §1, §3):
    # who asked for it and the other side of an incompatibility are named from the same single fill,
    # and a mod a problem names that no row shows (an unresolvable dependency) gets its card too.
    from src.library.resolution import Problem

    async def scenario() -> Any:
        api = FakeModrinth([project("X1", "Roto"), project("Q1", "Quilted")])
        s = Scenario(api)
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium"), project("P2", "Lithium"),
                                                project("R1", "Requester")])
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack), ("P2", ProjectOrigin.admin)])
        bundle = await s.modpackDB.bundle_get(bundle_id)
        saved = lock(entry("P1", "1"), entry("P2", "1"))
        saved.problems = [Problem(kind="unresolvable", project_id="X1", requested_by=["R1"], detail="sin versión"),
                          Problem(kind="incompatible", project_id="P2", conflict_with="Q1", detail="incompatible")]
        await s.modpackDB.resolution_save(ModpackResolution(
            bundle_id=bundle.id, resolved_at=NOW, request_digest="d", lock=saved))  # type: ignore[arg-type]
        listed = await s.service.mod_list(bundle_id, check=None)
        cards = {i: (c.title, c.description, c.icon_url, c.origin) for i, c in listed.problem_cards.items()}
        return listed.titles, cards, api.calls

    titles, cards, calls = mongo(scenario)
    assert [(c, sorted(ids)) for c, ids in calls] == [("project_get_many", ["Q1", "X1"])]  # one fill, names included
    assert titles["X1"] == "Roto" and titles["R1"] == "Requester" and titles["Q1"] == "Quilted"
    assert cards == {"X1": ("Roto", "About Roto", "https://cdn.modrinth.com/x.png", None)}  # P2 has its row


def test_support_flags_follow_the_pack(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([
            project("P1", "Quilty", loaders=["quilt"], game_versions=["1.21.1"]),
            project("P2", "Fabricy", loaders=["fabric"], game_versions=["1.20.1"]),
            project("P3", "Shady", loaders=["iris"], game_versions=["1.21.1"], project_type="shader")])
        bundle_id = await s.bundle([(i, ProjectOrigin.pack) for i in ("P1", "P2", "P3")])
        rows = (await s.service.mod_list(bundle_id, check=None)).rows
        return [(r.supports_loader, r.supports_version) for r in rows]

    assert mongo(scenario) == [(False, True), (True, False), (True, True)]


def test_a_version_only_outside_the_pack_channels_is_marked_with_its_channel(mongo: MongoRunner) -> None:
    # Tester feedback (2026-10-08): the card said "1.21.1 ✓" for a mod with only a beta for 1.21.1,
    # while the resolution marked it as a problem. The project's game versions span every channel.
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        await s.modrinthDB.project_upsert_many([project(i, i) for i in ("P1", "P2", "P3", "P4")])
        await s.modrinthDB.version_upsert_many([
            version("b1", "P1", "1.0-beta", 1, kind="beta"),
            version("r1", "P1", "0.9", 2, game_versions=["1.20.1"]),
            version("a2", "P2", "1.0-alpha", 1, kind="alpha"), version("b2", "P2", "1.0-beta", 2, kind="beta"),
            version("r3", "P3", "1.0", 1), version("b3", "P3", "1.1-beta", 2, kind="beta"),
            version("q4", "P4", "1.0-beta", 1, kind="beta", loaders=["forge"])])
        bundle_id = await s.bundle([(i, ProjectOrigin.pack) for i in ("P1", "P2", "P3", "P4", "P5")])
        rows = (await s.service.mod_list(bundle_id, check=None)).rows
        return [(r.project_id, r.version_channel) for r in rows]

    # P4's beta is for another loader and P5 has no versions in the catalog: nothing to say.
    assert mongo(scenario) == [("P1", "beta"), ("P2", "beta"), ("P3", None), ("P4", None), ("P5", None)]


def test_support_flags_are_unknown_without_the_project(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth(fail=True))
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack)])
        row = (await s.service.mod_list(bundle_id, check=None)).rows[0]
        return row.supports_loader, row.supports_version

    assert mongo(scenario) == (None, None)


def test_versions_for_the_pack_newest_first(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        api = FakeModrinth(versions=[
            version("a", "P1", "0.8.13", 1), version("b", "P1", "0.8.14", 5), version("c", "P1", "0.9.0-beta", 9, kind="beta"),
            version("d", "P1", "0.7.0", 1, loaders=["quilt"]), version("e", "P1", "0.6.0", 1, game_versions=["1.20.1"]),
            version("f", "P2", "9", 1)])
        s = Scenario(api)
        await s.modrinthDB.project_upsert_many([project("P1", "Sodium")])
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack)])
        only_release = [(o.version_number, o.channel) for o in await s.service.project_versions(bundle_id, "P1")]
        stored = {v.version_id async for v in ModrinthVersion.find()}
        bundle_beta = await s.bundle([("P1", ProjectOrigin.pack)], channels=("release", "beta"))
        with_beta = [o.version_number for o in await s.service.project_versions(bundle_beta, "P1")]
        return only_release, stored, with_beta, api.calls[0]

    only_release, stored, with_beta, call = mongo(scenario)
    assert only_release == [("0.8.14", "release"), ("0.8.13", "release")]  # fabric and 1.21.1 only, by date desc
    assert with_beta == ["0.9.0-beta", "0.8.14", "0.8.13"]  # every channel the pack allows
    assert {"a", "b", "c"} <= stored  # what was fetched stays in the catalog
    assert call == ("project_versions", ("P1", ("fabric",), ("1.21.1",)))


def test_versions_of_a_shader_use_its_own_loaders(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        api = FakeModrinth(versions=[version("s", "S1", "1.0", 1, loaders=["iris"])])
        s = Scenario(api)
        await s.modrinthDB.project_upsert_many([project("S1", "Shady", loaders=["iris"], project_type="shader")])
        bundle_id = await s.bundle([("S1", ProjectOrigin.pack)])
        return [o.version_number for o in await s.service.project_versions(bundle_id, "S1")], api.calls[0][1][1]

    numbers, asked = mongo(scenario)
    assert numbers == ["1.0"] and asked == ("canvas", "iris", "optifine", "vanilla")


def test_projects_filled_returns_the_catalog_and_fills_what_is_missing(mongo: MongoRunner) -> None:
    from src.services.modrinth import ModrinthService

    async def scenario() -> Any:
        api = FakeModrinth([project("P2", "Iris")])
        db = ModrinthDatabase()
        await db.project_upsert_many([project("P1", "Sodium")])
        filled = await ModrinthService(modrinthAPI=cast(Any, api), modrinthDB=db).projects_filled(["P1", "P2", "P3"])
        return sorted(filled), api.calls

    assert mongo(scenario) == (["P1", "P2"], [("project_get_many", ["P2", "P3"])])


def test_the_version_picker_asks_modrinth_once_and_then_reads_the_catalog(mongo: MongoRunner) -> None:
    # Spec 2026-10-09-economia-de-requests (F6): one request per card opened became none once the
    # catalog accounts for the project's versions (the resolution's checks keep them current).
    async def scenario() -> Any:
        api = FakeModrinth(versions=[version("a", "P1", "0.8.13", 1), version("b", "P1", "0.8.14", 5)])
        s = Scenario(api)
        await s.modrinthDB.project_upsert_many([ModrinthProject.parse(**{**project("P1", "Sodium").model_dump(mode="json"),
                                                                         "id": "P1", "versions": ["a", "b"]})])
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack)])
        first = [o.version_number for o in await s.service.project_versions(bundle_id, "P1")]
        second = [o.version_number for o in await s.service.project_versions(bundle_id, "P1")]
        return first, second, [c[0] for c in api.calls]

    first, second, calls = mongo(scenario)
    assert first == second == ["0.8.14", "0.8.13"] and calls == ["project_versions"]



def test_a_page_does_not_ask_again_for_an_icon_modrinth_just_said_it_lacks_nor_for_an_unknown_id(mongo: MongoRunner) -> None:
    # Spec 2026-10-09-economia-de-requests (F6): each render asked again for every project without an
    # icon and every id Modrinth does not know. Now both wait 24 hours.
    async def scenario() -> Any:
        api = FakeModrinth([project("P1", "Sodium", icon=None)])
        s = Scenario(api)
        bundle_id = await s.bundle([("P1", ProjectOrigin.pack), ("GONE1", ProjectOrigin.pack)])
        await s.service.mod_list(bundle_id, check=None)
        first = list(api.calls)
        api.calls.clear()
        await s.service.mod_list(bundle_id, check=None)
        return first, api.calls

    first, second = mongo(scenario)
    assert first == [("project_get_many", ["P1", "GONE1"])] and second == []


# A3 (owner, 2026-10-13): the row's pin select offers the catalog's versions, never Modrinth's.

def jei_listed(days: Sequence[int], kind: str = "release") -> list[ModrinthVersion]:
    return [version(f"j-{d}", "J", f"1.{d}", d, kind=kind) for d in days]


async def jei_pack(s: Scenario, versions: list[ModrinthVersion], pins: Optional[dict[str, str]] = None,
                   listed: bool = True) -> str:
    await s.modrinthDB.project_upsert_many([project("J", "JEI")])
    await s.modrinthDB.version_upsert_many(versions)
    if listed:
        await s.modrinthDB.version_list_save(ModrinthVersionList(
            project_id="J", game_version="1.21.1", loaders=["fabric"], version_ids=sorted(v.version_id for v in versions),
            fetched_at=NOW, target="1.21.1|fabric"))
    return await s.bundle([("J", ProjectOrigin.admin)], pins=pins)


def test_the_row_offers_the_15_newest_versions_and_the_pinned_one_from_the_catalog(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        bundle_id = await jei_pack(s, jei_listed(range(1, 21)), pins={"J": "j-3"})
        [row] = (await s.service.mod_list(bundle_id, check=None)).rows
        return [o.version_id for o in row.pin_options or []], row.pinned_version, s.api.calls

    options, pinned, calls = mongo(scenario)
    assert options == [f"j-{d}" for d in range(20, 5, -1)] + ["j-3"]
    assert pinned == "j-3" and calls == []


def test_a_mod_with_a_beta_exception_also_offers_beta_versions(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        bundle_id = await jei_pack(s, jei_listed([1, 2]) + jei_listed([3], kind="beta"))
        plain = [o.version_id for o in (await s.service.mod_list(bundle_id, check=None)).rows[0].pin_options or []]
        await s.modpackDB.bundle_channel_exception_set(bundle_id, "J", "beta")
        beta = [o.version_id for o in (await s.service.mod_list(bundle_id, check=None)).rows[0].pin_options or []]
        return plain, beta

    plain, beta = mongo(scenario)
    assert plain == ["j-2", "j-1"] and beta == ["j-3", "j-2", "j-1"]


def test_without_the_catalog_s_list_the_row_has_no_options(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        bundle_id = await jei_pack(s, jei_listed([1, 2]), listed=False)
        [row] = (await s.service.mod_list(bundle_id, check=None)).rows
        return row.pin_options, s.api.calls

    options, calls = mongo(scenario)
    assert options is None and calls == []
