"""Un pin que baja un mod pide confirmación (F27; d-7985ee-c62c61, d-7985ee-0b9ebb, d-7985ee-322302).

El piso es el de d-7985ee-6dc34c: la versión del build vigente y la del último escaneo de cada
servidor vinculado, la más nueva que sirve para la versión de Minecraft del pack. Un pin anterior
al piso (por fecha de publicación) es un 409 que describe la baja y no escribe nada, salvo con
`confirm_downgrade`; un pin para otra versión de Minecraft es un cambio de rama, no una baja. El
chequeo vive en el servicio: vale también para el PATCH de `/v1`. Contra MongoDB real.
"""
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from fastapi import HTTPException

from src.database.pterodactyl import PterodactylDatabase
from src.library.scan import ScanFile
from src.model.modpack import ModpackBundlePatch, ProjectOrigin
from src.model.modrinth import ModrinthVersion
from src.model.modrinth.version import ModrinthVersionStatus
from src.model.pterodactyl import PterodactylServer, ServerScan
from tests._actors import EDITOR
from tests.conftest import MongoRunner
from tests.test_mod_list import FakeModrinth, Scenario, entry, project, version

pytestmark = pytest.mark.network

# JEI: 19.57 (día 20) en el build; 19.51 (día 10) es anterior; 19.60 (día 25) es posterior; 18.0
# (día 1) es para otra versión de Minecraft.
J57, J51, J60, J18 = "jei-1957", "jei-1951", "jei-1960", "jei-1800"


class VersionsModrinth(FakeModrinth):
    """Also answers `GET /versions?ids=` for what it was given, counting the calls."""

    def __init__(self, versions: list[ModrinthVersion]) -> None:
        super().__init__([project("J", "JEI")], versions)
        self.many: list[list[str]] = []

    async def version_get_many(self, ids: list[str]) -> list[ModrinthVersion]:
        self.many.append(list(ids))
        return [v for v in self.versions if v.version_id in ids]


def jei_versions() -> list[ModrinthVersion]:
    return [version(J57, "J", "19.57", 20), version(J51, "J", "19.51", 10), version(J60, "J", "19.60", 25),
            version(J18, "J", "18.0", 1, game_versions=("1.20.1",))]


async def pack_with_build(stored: bool = True) -> tuple[Scenario, str]:
    """JEI in the pack and in build 1 at 19.57; the catalog has JEI's versions unless not `stored`."""
    s = Scenario(VersionsModrinth(jei_versions()))
    await s.modrinthDB.project_upsert_many([project("J", "JEI")])
    await s.modrinthDB.version_upsert_many([v for v in jei_versions() if stored or v.version_id != J57])
    bundle_id = await s.bundle([("J", ProjectOrigin.admin)])
    await s.built(bundle_id, entry("J", "19.57").model_copy(update={"version_id": J57}))
    return s, bundle_id


async def refused(s: Scenario, bundle_id: str, version_id: str) -> tuple[Optional[HTTPException], dict[str, str], list[Any]]:
    caught: Optional[HTTPException] = None
    try:
        await s.service.pin_set(bundle_id, "J", version_id, EDITOR)
    except HTTPException as error:
        caught = error
    audit: Any = s.service.audit
    return caught, (await s.modpackDB.bundle_get(bundle_id)).pins, list(audit.events)


def test_a_pin_before_the_build_in_force_is_refused_and_described(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s, bundle_id = await pack_with_build()
        return await refused(s, bundle_id, J51)

    error, pins, events = mongo(scenario)
    assert error is not None and error.status_code == 409
    assert "JEI 19.57 → 19.51" in str(error.detail) and "El build 1 usa 19.57" in str(error.detail)
    assert getattr(error, "downgrade").floor_number == "19.57"
    assert pins == {} and events == []


def test_confirming_saves_the_pin_and_the_event_says_it_goes_back(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s, bundle_id = await pack_with_build()
        await s.service.pin_set(bundle_id, "J", J51, EDITOR, confirm_downgrade=True)
        audit: Any = s.service.audit
        return (await s.modpackDB.bundle_get(bundle_id)).pins, audit.events[-1][2]

    pins, details = mongo(scenario)
    assert pins == {"J": J51}
    assert details == {"project_id": "J", "version_id": J51, "from_version_id": None, "downgrade": {"from": "19.57", "to": "19.51"}}


def test_a_pin_at_or_after_the_floor_or_on_another_branch_needs_nothing(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s, bundle_id = await pack_with_build()
        saved = []
        for pinned in (J57, J60, J18):
            await s.service.pin_set(bundle_id, "J", pinned, EDITOR)
            saved.append((await s.modpackDB.bundle_get(bundle_id)).pins["J"])
        return saved

    assert mongo(scenario) == [J57, J60, J18]


def test_without_a_build_or_a_linked_server_there_is_no_floor(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(VersionsModrinth(jei_versions()))
        await s.modrinthDB.version_upsert_many(jei_versions())
        bundle_id = await s.bundle([("J", ProjectOrigin.admin)])
        await s.service.pin_set(bundle_id, "J", J51, EDITOR)
        return (await s.modpackDB.bundle_get(bundle_id)).pins

    assert mongo(scenario) == {"J": J51}


def test_what_a_linked_server_runs_is_a_floor_and_named(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario(VersionsModrinth(jei_versions()))
        await s.modrinthDB.project_upsert_many([project("J", "JEI")])
        await s.modrinthDB.version_upsert_many(jei_versions())
        bundle_id = await s.bundle([("J", ProjectOrigin.admin)])
        server = await PterodactylDatabase().server_insert(PterodactylServer(
            identifier="1a2b3c4d", internal_id=1, uuid="1a2b3c4d-0000", name="Survival", node="n1", description="",
            status=None, is_suspended=False, is_installing=False, is_transferring=False, bundle_id=bundle_id))
        await PterodactylDatabase().scan_insert(ServerScan(
            server_id=server.id, directory="/mods", scanned_at=datetime(2026, 10, 1, tzinfo=timezone.utc),  # type: ignore[arg-type]
            full=False, files=[ScanFile(path="/mods/jei.jar", size=1, modified_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                                        status="identified", project_id="J", version_id=J60)]))
        return await refused(s, bundle_id, J57)

    error, pins, _ = mongo(scenario)
    assert error is not None and error.status_code == 409
    assert "JEI 19.60 → 19.57" in str(error.detail) and "El servidor «Survival» usa 19.60" in str(error.detail)
    assert pins == {}


def test_the_floor_is_chosen_as_the_resolver_does_listed_and_for_the_pack_s_loader(mongo: MongoRunner) -> None:
    # Review (2026-10-12): a newer version for another loader, or unlisted, is no floor; the one
    # that fits (19.57, the build's) still is, and the pin before it goes back.
    async def scenario() -> Any:
        s, bundle_id = await pack_with_build()
        other_loader = version("jei-forge", "J", "19.70", 28, loaders=("forge",))
        unlisted = version("jei-hidden", "J", "19.80", 29).model_copy(update={"status": ModrinthVersionStatus.unlisted})
        await s.modrinthDB.version_upsert_many([other_loader, unlisted])
        server = await PterodactylDatabase().server_insert(PterodactylServer(
            identifier="1a2b3c4d", internal_id=1, uuid="1a2b3c4d-0000", name="Survival", node="n1", description="",
            status=None, is_suspended=False, is_installing=False, is_transferring=False, bundle_id=bundle_id))
        await PterodactylDatabase().scan_insert(ServerScan(
            server_id=server.id, directory="/mods", scanned_at=datetime(2026, 10, 1, tzinfo=timezone.utc),  # type: ignore[arg-type]
            full=False, files=[ScanFile(path=f"/mods/{v}.jar", size=1, modified_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                                        status="identified", project_id="J", version_id=v)
                               for v in ("jei-forge", "jei-hidden")]))
        return await refused(s, bundle_id, J51)

    error, pins, _ = mongo(scenario)
    assert error is not None and "JEI 19.57 → 19.51" in str(error.detail) and pins == {}


def test_a_floor_the_catalog_lacks_is_read_from_modrinth_once(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s, bundle_id = await pack_with_build(stored=False)
        error, _, _ = await refused(s, bundle_id, J51)
        api: Any = s.api
        return error, api.many

    error, many = mongo(scenario)
    assert error is not None and error.status_code == 409 and many == [[J57]]


def test_the_mod_list_marks_a_saved_pin_below_its_floor_without_asking_modrinth(mongo: MongoRunner) -> None:
    """d-7985ee-f43772: a pin can become a step back after it was set (the floor rose); the list
    says so next to the pin, from the catalog only."""
    async def scenario() -> Any:
        s, bundle_id = await pack_with_build()
        await s.service.pin_set(bundle_id, "J", J51, EDITOR, confirm_downgrade=True)
        api: Any = s.api
        api.many.clear()
        rows = (await s.service.mod_list(bundle_id, check=None)).rows
        return [(r.project_id, r.pinned, r.pin_floor) for r in rows], api.many

    rows, many = mongo(scenario)
    assert rows == [("J", True, "19.57")] and many == []


def test_the_v1_patch_asks_for_the_same_confirmation(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s, bundle_id = await pack_with_build()
        caught = None
        try:
            await s.service.bundle_update(bundle_id, ModpackBundlePatch(pins={"J": J51}), EDITOR)
        except HTTPException as error:
            caught = error.status_code
        before = (await s.modpackDB.bundle_get(bundle_id)).pins
        await s.service.bundle_update(bundle_id, ModpackBundlePatch(pins={"J": J51}), EDITOR, confirm_downgrade=True)
        await s.service.bundle_update(bundle_id, ModpackBundlePatch(pins={"J": None}), EDITOR)  # removing needs nothing
        return caught, before, (await s.modpackDB.bundle_get(bundle_id)).pins

    assert mongo(scenario) == (409, {}, {})
