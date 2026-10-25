"""Importar fija la versión del pack, con sus orígenes (O2; docs/research/2026-10-21-observaciones-ronda-3.md).

Un pack importado que trae versiones exactas fija cada mod a la versión del pack, con origen
«versión del pack» y el resultado de validarla; una que Modrinth no marca para el pack se usa igual,
marcada. Nunca pisa una elección ni baja un mod: ahí solo se ofrece en la tarjeta. La tarjeta pone en
uso otro origen, con la misma confirmación de baja de hoy. Contra MongoDB real; Modrinth es el doble
de `tests/test_modpack_import.py`.
"""
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from src.database.modrinth import ModrinthDatabase
from src.library.resolution import Lock, LockEntry, ResolveRequest, Target
from src.model.modpack import ModpackBuild, ModpackBundle, PinOrigin, PinRecord, PinSource
from src.model.modrinth import ModrinthVersion
from tests._actors import EDITOR
from tests.conftest import MongoRunner
from tests.test_modpack_import import LIB, LITHIUM, SODIUM, Scenario, mrpack, version

pytestmark = pytest.mark.network

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def off_tags(s: Scenario) -> None:
    """Lithium's version in the pack does not say 1.20.1 on Modrinth (MixinTrace Reborn's case)."""
    for v in s.api.versions:
        if v["id"] == "lit-1":
            v["game_versions"] = ["1.20"]


async def built_with(s: Scenario, bundle_id: str, project_id: str, version_id: str) -> None:
    """Build 1 of the pack runs `version_id` of `project_id`: the mod's floor."""
    bundle = await s.modpackDB.bundle_get(bundle_id)
    entry = LockEntry(project_id=project_id, origin="pack", version_id=version_id, version_number=version_id,
                      file=None, environment=None, server="include", reason="r", requested_by=[])
    lock = Lock(entries=[entry], problems=[], suggestions=[], needs_projects=set(), needs_types=set(), needs_versions=set())
    request = ResolveRequest(target=Target(game_version="1.20.1", loader="fabric"), projects=[])
    await s.modpackDB.build_insert(ModpackBuild(bundle_id=bundle.id, number=1, created_at=NOW,  # type: ignore[arg-type]
                                                request=request, lock=lock))
    await s.modpackDB.bundle_latest_build(bundle_id, 1)


async def imported(s: Scenario) -> str:
    """A pack imported with Sodium pinned to its version «sod-1»: a pack the card can choose on."""
    return (await s.service.run_mrpack(mrpack(), EDITOR)).bundle_id


def test_importing_pins_each_mod_to_the_packs_version_and_marks_one_outside_the_tags(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ModpackBundle]:
        s = Scenario()
        off_tags(s)
        report = await s.service.run_mrpack(mrpack(), EDITOR)
        return report, await s.modpackDB.bundle_get(report.bundle_id)

    report, bundle = mongo(scenario)
    assert bundle.pins == {SODIUM[0]: "sod-1", LITHIUM[0]: "lit-1", LIB[0]: "lib-b"}
    assert {p: r.selected for p, r in bundle.pin_origins.items()} == {p: PinSource.pack for p in bundle.pins}
    lithium = bundle.pin_origins[LITHIUM[0]].origins
    assert [(o.source, o.version_id, o.valid, o.problem, o.label) for o in lithium] == [
        (PinSource.pack, "lit-1", False, "Modrinth no la marca para 1.20.1", "Aventura 2.0")]
    # A beta of the pack passes: importing gave it its channel exception first.
    assert bundle.pin_origins[LIB[0]].origins[0].valid is True
    assert sorted(report.pinned) == sorted(bundle.pins) and report.outside == {LITHIUM[0]: "Modrinth no la marca para 1.20.1"}


def test_importing_into_a_pack_keeps_the_admins_pin_and_offers_the_packs_version(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ModpackBundle]:
        s = Scenario()
        bundle_id = await s.bundle()
        await s.modpackDB.bundle_pin_set(bundle_id, SODIUM[0], "sod-0")
        report = await s.service.run_mrpack(mrpack(), EDITOR, into=bundle_id)
        return report, await s.modpackDB.bundle_get(bundle_id)

    report, bundle = mongo(scenario)
    assert bundle.pins[SODIUM[0]] == "sod-0"
    record = bundle.pin_origins[SODIUM[0]]
    assert record.selected == PinSource.admin
    assert [(o.source, o.version_id) for o in record.origins] == [(PinSource.admin, "sod-0"), (PinSource.pack, "sod-1")]
    assert SODIUM[0] in report.offered and SODIUM[0] not in report.pinned


def test_importing_never_puts_an_older_version_in_use(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ModpackBundle]:
        s = Scenario()
        newer = version("sod-2", SODIUM[0], "sodium jar 2", day=5)
        s.api.versions.append(newer)
        await ModrinthDatabase().version_upsert_many([ModrinthVersion.parse(**newer)])
        bundle_id = await s.bundle()
        await built_with(s, bundle_id, SODIUM[0], "sod-2")
        report = await s.service.run_mrpack(mrpack(), EDITOR, into=bundle_id)
        return report, await s.modpackDB.bundle_get(bundle_id)

    report, bundle = mongo(scenario)
    assert SODIUM[0] not in bundle.pins
    record = bundle.pin_origins[SODIUM[0]]
    assert record.selected == PinSource.resolution and record.origins[0].version_id == "sod-1"
    assert SODIUM[0] in report.offered


def test_the_card_puts_another_origin_in_use_and_going_back_asks_first(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        s = Scenario()
        newer = version("sod-2", SODIUM[0], "sodium jar 2", day=5)
        s.api.versions.append(newer)
        await ModrinthDatabase().version_upsert_many([ModrinthVersion.parse(**newer)])
        bundle_id = await s.bundle()
        await built_with(s, bundle_id, SODIUM[0], "sod-2")
        await s.service.run_mrpack(mrpack(), EDITOR, into=bundle_id)
        service = s.service.modpackService
        with pytest.raises(HTTPException) as refused:
            await service.pin_origin_select(bundle_id, SODIUM[0], PinSource.pack, EDITOR)
        unchanged = (await s.modpackDB.bundle_get(bundle_id)).pins.get(SODIUM[0])
        await service.pin_origin_select(bundle_id, SODIUM[0], PinSource.pack, EDITOR, confirm_downgrade=True)
        pinned = await s.modpackDB.bundle_get(bundle_id)
        await service.pin_origin_select(bundle_id, SODIUM[0], PinSource.resolution, EDITOR)
        freed = await s.modpackDB.bundle_get(bundle_id)
        events = [e for e in s.audit.events if e[0] == "bundle.pin"]
        return refused.value.status_code, unchanged, pinned, freed, events

    status, unchanged, pinned, freed, events = mongo(scenario)
    assert status == 409 and unchanged is None
    assert pinned.pins[SODIUM[0]] == "sod-1" and pinned.pin_origins[SODIUM[0]].selected == PinSource.pack
    assert SODIUM[0] not in freed.pins and freed.pin_origins[SODIUM[0]].selected == PinSource.resolution
    assert [o.source for o in freed.pin_origins[SODIUM[0]].origins] == [PinSource.pack]
    assert events[0][2]["downgrade"] == {"from": "sod-2", "to": "sod-1"} and events[0][2]["origin"] == "pack"


def test_the_card_puts_one_servers_origin_in_use_by_its_server(mongo: MongoRunner) -> None:
    """One «servidor» origin per linked server (owner, 2026-10-22): choosing names the server."""
    async def scenario() -> tuple[Any, ...]:
        s = Scenario()
        bundle_id = await imported(s)
        read = (await s.modpackDB.bundle_get(bundle_id)).pin_origins[SODIUM[0]]
        record = read.model_copy(update={"origins": [
            *read.origins, PinOrigin(source=PinSource.server, version_id="sod-1", label="Survival", server_id="s1"),
            PinOrigin(source=PinSource.server, version_id="sod-1", label="Creativo", server_id="s2")]})
        assert await s.modpackDB.bundle_pin_record_set(bundle_id, SODIUM[0], record, "sod-1")
        service = s.service.modpackService
        await service.pin_origin_select(bundle_id, SODIUM[0], PinSource.server, EDITOR, server_id="s2")
        chosen = await s.modpackDB.bundle_get(bundle_id)
        with pytest.raises(HTTPException) as unknown:
            await service.pin_origin_select(bundle_id, SODIUM[0], PinSource.server, EDITOR, server_id="s9")
        return chosen, unknown.value.status_code

    chosen, status = mongo(scenario)
    record = chosen.pin_origins[SODIUM[0]]
    assert (record.selected, record.selected_key) == (PinSource.server, "server:s2")
    assert [o.server_id for o in record.origins if o.source == PinSource.server] == ["s1", "s2"]
    assert status == 422


def test_a_pack_version_outside_the_tags_resolves_marked_instead_of_a_problem(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        s = Scenario()
        off_tags(s)
        report = await s.service.run_mrpack(mrpack(), EDITOR)
        return await s.service.modpackService.resolve_fetching(report.bundle_id)

    lock = mongo(scenario)
    assert not [p for p in lock.problems if p.project_id == LITHIUM[0]]
    [lithium] = [e for e in lock.entries if e.project_id == LITHIUM[0]]
    assert (lithium.version_id, lithium.off_tags) == ("lit-1", True)


def test_a_bundle_stored_before_origins_loads_with_its_pins_as_they_were(mongo: MongoRunner) -> None:
    async def scenario() -> ModpackBundle:
        s = Scenario()
        bundle_id = await s.bundle()
        # As stored before O2: no `pin_origins` key at all.
        await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": (await s.modpackDB.bundle_get(bundle_id)).id},
            {"$set": {"pins": {SODIUM[0]: "sod-1"}}, "$unset": {"pin_origins": ""}})
        return await s.modpackDB.bundle_get(bundle_id)

    bundle = mongo(scenario)
    assert bundle.pins == {SODIUM[0]: "sod-1"} and bundle.pin_origins == {}


# Dos escrituras desde la misma lectura (menor de la revisión, 2026-10-22): la segunda no pisa a la primera.

def server_origin(server_id: str, name: str) -> PinOrigin:
    return PinOrigin(source=PinSource.server, version_id="sod-2", label=name, server_id=server_id)


def test_a_record_written_from_a_stale_read_is_refused(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        s = Scenario()
        bundle_id = await s.bundle()
        await s.modpackDB.bundle_pin_set(bundle_id, SODIUM[0], "sod-1")
        base = PinRecord(origins=[PinOrigin(source=PinSource.admin, version_id="sod-1")], selected=PinSource.admin)
        assert await s.modpackDB.bundle_pin_record_set(bundle_id, SODIUM[0], base, "sod-1")
        read = (await s.modpackDB.bundle_get(bundle_id)).pin_origins[SODIUM[0]]
        first = read.model_copy(update={"origins": [*read.origins, server_origin("s1", "Survival")]})
        second = read.model_copy(update={"origins": [*read.origins, server_origin("s2", "Creativo")]})
        written = (await s.modpackDB.bundle_pin_record_set(bundle_id, SODIUM[0], first, "sod-1"),
                   await s.modpackDB.bundle_pin_record_set(bundle_id, SODIUM[0], second, "sod-1"))
        return written, await s.modpackDB.bundle_get(bundle_id)

    written, bundle = mongo(scenario)
    assert written == (True, False)
    record = bundle.pin_origins[SODIUM[0]]
    assert [o.server_id for o in record.origins if o.source == PinSource.server] == ["s1"]
    assert record.rev == 2


def test_a_record_stored_before_revisions_is_written_as_revision_zero(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[bool, ModpackBundle]:
        s = Scenario()
        bundle_id = await s.bundle()
        # As stored before the revision: no `rev` key in the record.
        await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": (await s.modpackDB.bundle_get(bundle_id)).id},
            {"$set": {"pins": {SODIUM[0]: "sod-1"},
                      f"pin_origins.{SODIUM[0]}": {"origins": [{"source": "admin", "version_id": "sod-1"}],
                                                    "selected": "admin"}}})
        read = (await s.modpackDB.bundle_get(bundle_id)).pin_origins[SODIUM[0]]
        changed = read.model_copy(update={"origins": [*read.origins, server_origin("s1", "Survival")]})
        written = await s.modpackDB.bundle_pin_record_set(bundle_id, SODIUM[0], changed, "sod-1")
        return written, await s.modpackDB.bundle_get(bundle_id)

    written, bundle = mongo(scenario)
    assert written and bundle.pin_origins[SODIUM[0]].rev == 1


def test_choosing_an_origin_while_a_scan_adds_another_keeps_both(mongo: MongoRunner) -> None:
    async def scenario() -> ModpackBundle:
        s = Scenario()
        bundle_id = await imported(s)
        read = (await s.modpackDB.bundle_get(bundle_id)).pin_origins[SODIUM[0]]
        record = read.model_copy(update={"origins": [
            *read.origins, PinOrigin(source=PinSource.server, version_id="sod-1", label="Survival", server_id="s1")]})
        assert await s.modpackDB.bundle_pin_record_set(bundle_id, SODIUM[0], record, "sod-1")
        stale = await s.modpackDB.bundle_get(bundle_id)
        # A scan of another server writes its origin after the card read the pack.
        fresh = stale.pin_origins[SODIUM[0]]
        await s.modpackDB.bundle_pin_record_set(
            bundle_id, SODIUM[0], fresh.model_copy(update={"origins": [*fresh.origins, server_origin("s2", "Creativo")]}),
            "sod-1")
        reads = [stale]
        real_get = s.modpackDB.bundle_get

        async def get(bundle_id: str) -> ModpackBundle:
            return reads.pop() if reads else await real_get(bundle_id)

        s.modpackDB.bundle_get = get  # type: ignore[method-assign]
        await s.service.modpackService.pin_origin_select(bundle_id, SODIUM[0], PinSource.server, EDITOR, server_id="s1")
        return await real_get(bundle_id)

    bundle = mongo(scenario)
    record = bundle.pin_origins[SODIUM[0]]
    assert (record.selected, record.selected_key, bundle.pins[SODIUM[0]]) == (PinSource.server, "server:s1", "sod-1")
    assert [o.server_id for o in record.origins if o.source == PinSource.server] == ["s1", "s2"]
