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
from tests.test_mod_list import NOW, FakeModrinth, Scenario, entry, lock, project
from src.model.modpack import ModpackResolution

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
