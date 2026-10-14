"""A pack's bridges (batch C; d-7985ee-a8d395, d-7985ee-4fd88e): turned on through the pack's patch,
validated against the bridges of its loader, and the guest's versions asked of Modrinth only for the
mods with no native version. Against MongoDB."""
from typing import Any

import pytest
from fastapi import HTTPException

from src.model.modpack import ModpackBundle, ModpackBundlePatch, PackProject, ProjectOrigin
from src.model.utils import MinecraftLoaders
from tests._actors import EDITOR
from tests.conftest import MongoRunner
from tests.test_mod_list import FakeModrinth, Scenario, project, version

pytestmark = pytest.mark.network


async def neoforge_pack(s: Scenario, game: str = "1.21.1", bridges: tuple[str, ...] = ()) -> str:
    created = await s.modpackDB.bundle_insert(ModpackBundle(
        name="pack", description="", version=game, loader=MinecraftLoaders.neoforge,
        projects=[PackProject(project_id=i, origin=ProjectOrigin.admin) for i in ("N", "F")],
        secondary_loaders=list(bridges)))
    return str(created.id)


def api(game: str = "1.21.1") -> FakeModrinth:
    return FakeModrinth([project("N", "Native", loaders=("neoforge",), game_versions=(game,)),
                         project("F", "FabricOnly", loaders=("fabric",), game_versions=(game,))],
                        [version("n1", "N", "1.0", 1, loaders=("neoforge",), game_versions=(game,)),
                         version("f1", "F", "1.0", 1, loaders=("fabric",), game_versions=(game,))])


def test_a_pack_turns_a_bridge_on_through_its_patch_and_only_one_of_its_loader(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(api())
        bundle_id = await neoforge_pack(s)
        await s.service.bundle_update(bundle_id, ModpackBundlePatch(secondary_loaders=["connector"]), EDITOR)
        saved = (await s.modpackDB.bundle_get(bundle_id)).secondary_loaders
        caught = None
        try:
            await s.service.bundle_update(bundle_id, ModpackBundlePatch(secondary_loaders=["kilt"]), EDITOR)
        except HTTPException as error:
            caught = error.status_code
        audit: Any = s.service.audit
        return saved, caught, (await s.modpackDB.bundle_get(bundle_id)).secondary_loaders, audit.events[-1][0]

    saved, caught, after, action = mongo(scenario)
    assert saved == ["connector"] and caught == 422 and after == ["connector"] and action == "bundle.update"


def test_resolving_asks_for_fabric_versions_only_for_the_mod_without_a_native_one(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        fake = api()
        s = Scenario(fake)
        bundle_id = await neoforge_pack(s, bridges=("connector",))
        lock = await s.service.resolve_fetching(bundle_id)
        asked = [args for name, args in fake.calls if name == "project_versions"]
        return {e.project_id: (e.version_id, e.via) for e in lock.entries}, asked

    entries, asked = mongo(scenario)
    assert entries == {"N": ("n1", None), "F": ("f1", "connector")}
    assert ("N", ("fabric",), ("1.21.1",)) not in asked and ("F", ("fabric",), ("1.21.1",)) in asked


def test_a_bridge_saved_for_another_minecraft_version_has_no_effect(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(api("1.20.1"))
        bundle_id = await neoforge_pack(s, game="1.20.1", bridges=("connector",))
        lock = await s.service.resolve_fetching(bundle_id)
        return [e.project_id for e in lock.entries], [p.kind for p in lock.problems]

    entries, problems = mongo(scenario)
    assert entries == ["N"] and "unresolvable" in problems  # F: no native version, and no bridge for 1.20.1 here


# Task 6: what enters with a bridged mod (C4; d-7985ee-56a529: shown, never added alone).


def companions_api(game: str = "1.21.1") -> FakeModrinth:
    from src.library.bridges import CONNECTOR, FORGIFIED_FABRIC_API

    fake = api(game)
    fake.projects |= {p.project_id: p for p in (
        project("M", "FabricMiniMap", loaders=("fabric",), game_versions=(game,)),
        project(CONNECTOR, "Connector", loaders=("neoforge",), game_versions=(game,)),
        project(FORGIFIED_FABRIC_API, "Forgified Fabric API", loaders=("neoforge",), game_versions=(game,)))}
    fake.versions += [version("m1", "M", "1.0", 1, loaders=("fabric",), game_versions=(game,))]
    return fake


def test_a_mod_that_only_runs_through_the_bridge_names_the_companions_the_pack_lacks(mongo: MongoRunner) -> None:
    from src.library.bridges import CONNECTOR, FORGIFIED_FABRIC_API

    async def scenario() -> Any:
        s = Scenario(companions_api())
        bundle_id = await neoforge_pack(s, bridges=("connector",))
        first = await s.service.bridge_companions_missing(bundle_id, "M")
        await s.modpackDB.bundle_projects_add(bundle_id, [PackProject(project_id=CONNECTOR, origin=ProjectOrigin.admin)])
        second = await s.service.bridge_companions_missing(bundle_id, "M")
        return first, second, (await s.modpackDB.bundle_get(bundle_id)).projects

    first, second, projects = mongo(scenario)
    assert first == [(CONNECTOR, "Connector"), (FORGIFIED_FABRIC_API, "Forgified Fabric API")]
    assert second == [(FORGIFIED_FABRIC_API, "Forgified Fabric API")]
    assert "M" not in [p.project_id for p in projects]  # a read: nothing is added


def test_a_native_mod_or_a_pack_without_a_bridge_needs_nothing(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        fake = companions_api()
        s = Scenario(fake)
        native = await s.service.bridge_companions_missing(await neoforge_pack(s, bridges=("connector",)), "N")
        fake.calls.clear()
        off = await s.service.bridge_companions_missing(await neoforge_pack(s), "M")
        return native, off, fake.calls

    native, off, calls = mongo(scenario)
    assert native == [] and off == [] and calls == []  # without a bridge, not even Modrinth is asked
