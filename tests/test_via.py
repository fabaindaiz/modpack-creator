"""A mod's way in the pack (batch C, task 7; d-7985ee-5e70f7, d-7985ee-dea282, d-7985ee-8e5f9e,
d-7985ee-7e602f, d-7985ee-98c52a): the row says it runs through a bridge and whether a native version
exists, and moving it between its native and bridged versions always asks first. Against MongoDB."""
from typing import Any

import pytest
from fastapi import HTTPException

from src.model.modpack import ModpackBundle, PackProject, ProjectOrigin
from src.model.utils import MinecraftLoaders
from tests._actors import EDITOR
from tests.conftest import MongoRunner
from tests.test_mod_list import NOW, FakeModrinth, Scenario, entry, lock, project, version
from src.model.modpack import ModpackResolution
from src.model.modrinth import ModrinthVersionList

pytestmark = pytest.mark.network


async def bridged_pack(s: Scenario, native: str = "available") -> str:
    created = await s.modpackDB.bundle_insert(ModpackBundle(
        name="pack", description="", version="1.21.1", loader=MinecraftLoaders.neoforge,
        projects=[PackProject(project_id="F", origin=ProjectOrigin.admin)], secondary_loaders=["connector"]))
    await s.modrinthDB.project_upsert_many([project("F", "FabricMod", loaders=("fabric",))])
    saved = lock(entry("F", "1.0", origin="admin").model_copy(update={"via": "connector"}))
    if native == "available":
        saved.native_available = {"F": "f-native"}
    elif native == "behind":
        saved.native_behind = {"F": "f-old"}
    await s.modpackDB.resolution_save(ModpackResolution(
        bundle_id=created.id, resolved_at=NOW, request_digest="d", lock=saved))  # type: ignore[arg-type]
    return str(created.id)


def test_a_bridged_row_says_its_way_whether_a_native_one_caught_up_and_that_the_server_should_test_it(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        rows = []
        for state in ("available", "behind", "none"):
            bundle_id = await bridged_pack(s, state)
            [row] = (await s.service.mod_list(bundle_id, check=None)).rows
            rows.append((row.via, row.native, row.server_risky))
        return rows

    assert mongo(scenario) == [("connector", "alcanza", True), ("connector", "detrás", True), ("connector", None, True)]


def test_moving_a_mod_to_its_native_versions_asks_first_then_is_kept_and_audited(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        bundle_id = await bridged_pack(s)
        refused = None
        try:
            await s.service.via_set(bundle_id, "F", native=True, actor=EDITOR)
        except HTTPException as error:
            refused = (error.status_code, str(error.detail))
        before = (await s.modpackDB.bundle_get(bundle_id)).native_only
        await s.service.via_set(bundle_id, "F", native=True, actor=EDITOR, confirm=True)
        moved = (await s.modpackDB.bundle_get(bundle_id)).native_only
        await s.service.via_set(bundle_id, "F", native=False, actor=EDITOR, confirm=True)
        audit: Any = s.service.audit
        return refused, before, moved, (await s.modpackDB.bundle_get(bundle_id)).native_only, audit.events[-1]

    refused, before, moved, back, event = mongo(scenario)
    assert refused is not None and refused[0] == 409 and "FabricMod" in refused[1]
    assert before == [] and moved == ["F"] and back == []
    assert event[0] == "bundle.via" and event[2] == {"project_id": "F", "to": "puente"}


def test_after_the_last_bridged_mod_the_bridge_is_named_to_remove(mongo: MongoRunner) -> None:
    """d-7985ee-80ee9a: removing the last bridged mod suggests removing the bridge, never does it."""
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        bundle_id = await bridged_pack(s)
        await s.modpackDB.bundle_projects_add(bundle_id, [PackProject(project_id="u58R1TMW", origin=ProjectOrigin.admin)])
        await s.modrinthDB.project_upsert_many([project("u58R1TMW", "Connector", loaders=("neoforge",))])
        busy = await s.service.bridge_idle(bundle_id)
        await s.service.project_remove(bundle_id, "F", EDITOR)
        return busy, await s.service.bridge_idle(bundle_id), [p.project_id for p in (await s.modpackDB.bundle_get(bundle_id)).projects]

    busy, idle, kept = mongo(scenario)
    assert busy == [] and idle == ["Connector"] and kept == ["u58R1TMW"]


# The final review (2026-10-13).


def test_a_bridged_row_offers_its_bridged_versions_to_pin(mongo: MongoRunner) -> None:
    """Important 4: the pin select of a mod that runs through a bridge lists its own way's versions,
    never the native ones (picking one would change its way without asking)."""
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        bundle_id = await bridged_pack(s, "none")
        fabric = [version("f-1", "F", "1.0", 1), version("f-2", "F", "1.1", 2)]
        native = [version("n-1", "F", "0.9", 3, loaders=("neoforge",))]
        await s.modrinthDB.version_upsert_many([*fabric, *native])
        for loader, found in (("fabric", fabric), ("neoforge", native)):
            await s.modrinthDB.version_list_save(ModrinthVersionList(
                project_id="F", game_version="1.21.1", loaders=[loader], version_ids=[v.version_id for v in found],
                fetched_at=NOW, target=f"1.21.1|{loader}"))
        [row] = (await s.service.mod_list(bundle_id, check=None)).rows
        return [o.version_id for o in row.pin_options or []]

    assert mongo(scenario) == ["f-2", "f-1"]


def test_a_pin_that_changes_a_mod_s_way_is_refused_and_points_to_the_way_change(mongo: MongoRunner) -> None:
    """Important 4 (d-7985ee-7e602f): a pin never moves a mod between its native and bridged versions;
    that is «Pasar a nativa» or «Volver al puente», which ask first. A pin of the same way is kept."""
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        bundle_id = await bridged_pack(s)
        await s.modrinthDB.version_upsert_many([version("n-1", "F", "0.9", 3, loaders=("neoforge",)),
                                                version("f-2", "F", "1.1", 2)])
        refused = None
        try:
            await s.service.pin_set(bundle_id, "F", "n-1", EDITOR)
        except HTTPException as error:
            refused = (error.status_code, str(error.detail))
        before = dict((await s.modpackDB.bundle_get(bundle_id)).pins)
        await s.service.pin_set(bundle_id, "F", "f-2", EDITOR)
        return refused, before, dict((await s.modpackDB.bundle_get(bundle_id)).pins)

    refused, before, after = mongo(scenario)
    assert refused is not None and refused[0] == 422 and "Pasar a nativa" in refused[1]
    assert before == {} and after == {"F": "f-2"}


def test_the_bridge_is_named_to_remove_even_when_the_removed_mod_brought_bridged_dependencies(mongo: MongoRunner) -> None:
    """Important 6: the saved resolution is from before the removal; a bridged dependency only the
    removed mod asked for does not keep the bridge busy."""
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        bundle_id = await bridged_pack(s)
        await s.modpackDB.bundle_projects_add(bundle_id, [PackProject(project_id="u58R1TMW", origin=ProjectOrigin.admin)])
        await s.modrinthDB.project_upsert_many([project("u58R1TMW", "Connector", loaders=("neoforge",))])
        bundle = await s.modpackDB.bundle_get(bundle_id)
        saved = lock(entry("F", "1.0", origin="admin").model_copy(update={"via": "connector"}),
                     entry("L", "2.0", origin=None, requested_by=["F"]).model_copy(update={"via": "connector"}))
        await s.modpackDB.resolution_save(ModpackResolution(
            bundle_id=bundle.id, resolved_at=NOW, request_digest="d", lock=saved))  # type: ignore[arg-type]
        busy = await s.service.bridge_idle(bundle_id)
        await s.service.project_remove(bundle_id, "F", EDITOR)
        return busy, await s.service.bridge_idle(bundle_id)

    assert mongo(scenario) == ([], ["Connector"])


def test_a_mod_already_installed_native_cannot_go_back_to_the_bridge(mongo: MongoRunner) -> None:
    """Review of batch C (minor 1): «Volver al puente» is offered only while what is installed still
    runs through the bridge; once the build in force has its native version, going back would be a
    change of way the resolution never makes, so it is refused and the mark stays."""
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        bundle_id = await bridged_pack(s, "none")
        await s.modpackDB.bundle_native_only_set(bundle_id, "F", True)
        before = (await s.service.mod_list(bundle_id, check=None)).via_back
        await s.modrinthDB.version_upsert_many([version("v-F-2.0", "F", "2.0", 5, loaders=("neoforge",))])
        await s.built(bundle_id, entry("F", "2.0", origin="admin"))
        after = (await s.service.mod_list(bundle_id, check=None)).via_back
        refused = None
        try:
            await s.service.via_set(bundle_id, "F", native=False, actor=EDITOR, confirm=True)
        except HTTPException as error:
            refused = (error.status_code, str(error.detail))
        return before, after, refused, (await s.modpackDB.bundle_get(bundle_id)).native_only

    before, after, refused, kept = mongo(scenario)
    assert before == {"F"} and after == set()
    assert refused is not None and refused[0] == 422 and "nativa" in refused[1] and kept == ["F"]


def test_an_installed_version_the_catalog_does_not_know_does_not_offer_going_back(mongo: MongoRunner) -> None:
    """Review of the minors (minor 4): a version nobody can say is bridged may be native, so going back
    is neither offered nor done (it fails closed)."""
    async def scenario() -> Any:
        s = Scenario(FakeModrinth())
        bundle_id = await bridged_pack(s, "none")
        await s.modpackDB.bundle_native_only_set(bundle_id, "F", True)
        await s.built(bundle_id, entry("F", "9.9", origin="admin"))
        return (await s.service.mod_list(bundle_id, check=None)).via_back

    assert mongo(scenario) == set()
