"""What a pin's origins replaced is kept (O8 D2, d-7985ee-a99db7; P5 of d-7985ee-a68b5c; user-walk N1–N3).

Every write of a mod's pin record goes through `ModpackDatabase.bundle_pin_record_set`: what the stored
record had and the new one does not is added to `pin_history` in the same update. Removing the mod keeps
it too, marked. `past_origins` is what the history shows of it: no duplicates, the 20 newest, and at least
the newest of each source.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.library.pins import past_origins
from src.model.modpack import PastOrigin, PinOrigin, PinRecord, PinSource
from tests._actors import EDITOR
from tests.conftest import MongoRunner
from src.database.modrinth import ModrinthDatabase
from src.model.modrinth import ModrinthVersion
from tests.test_modpack_import import SODIUM, Scenario, mrpack, version

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def past(source: PinSource, version: str, days: int, server: str | None = None) -> PastOrigin:
    return PastOrigin(origin=PinOrigin(source=source, version_id=version, server_id=server), until=T0 + timedelta(days=days))


def test_past_origins_dedupe_cap_and_keep_one_per_source() -> None:
    flapping = [past(PinSource.server, f"s-{i % 2}", i, "srv") for i in range(30)]
    history = [past(PinSource.pack, "p-1", 0), *flapping]
    shown = past_origins(history)
    keys = [(p.origin.source, p.origin.server_id, p.origin.version_id) for p in shown]
    assert len(keys) == len(set(keys))  # a server going back and forth is one entry per version
    assert (PinSource.pack, None, "p-1") in keys  # the pack's original version is never pushed out
    assert shown == sorted(shown, key=lambda p: p.until, reverse=True)
    many = [past(PinSource.admin, f"a-{i}", i) for i in range(30)]
    assert len(past_origins(many)) == 20


@pytest.mark.network
def test_a_record_write_keeps_what_it_replaced(mongo: MongoRunner) -> None:
    async def scenario() -> list[tuple[str, str]]:
        s = Scenario()
        bundle_id = await s.bundle()
        await s.modpackDB.bundle_pin_set(bundle_id, SODIUM[0], "sod-1")
        first = PinRecord(origins=[PinOrigin(source=PinSource.admin, version_id="sod-1")], selected=PinSource.admin)
        assert await s.modpackDB.bundle_pin_record_set(bundle_id, SODIUM[0], first, "sod-1")
        read = (await s.modpackDB.bundle_get(bundle_id)).pin_origins[SODIUM[0]]
        second = read.model_copy(update={"origins": [PinOrigin(source=PinSource.admin, version_id="sod-2")]})
        assert await s.modpackDB.bundle_pin_record_set(bundle_id, SODIUM[0], second, "sod-2")
        stale = read.model_copy(update={"origins": []})  # from the old read: refused, and nothing kept
        assert not await s.modpackDB.bundle_pin_record_set(bundle_id, SODIUM[0], stale, None)
        bundle = await s.modpackDB.bundle_get(bundle_id)
        return [(p.origin.source.value, p.origin.version_id) for p in bundle.pin_history[SODIUM[0]]]

    assert mongo(scenario) == [("admin", "sod-1")]


@pytest.mark.network
def test_pinning_by_hand_again_keeps_the_replaced_choice_and_the_others_keep_theirs(mongo: MongoRunner) -> None:
    """A second choice by hand replaces the first: that one is kept. Using another origin or unpinning
    replaces nothing (the origins stay on offer), so they add nothing."""
    async def scenario() -> list[tuple[str, str]]:
        s = Scenario()
        newer = version("sod-2", SODIUM[0], "sodium jar 2", day=5)
        s.api.versions.append(newer)
        await ModrinthDatabase().version_upsert_many([ModrinthVersion.parse(**newer)])
        bundle_id = (await s.service.run_mrpack(mrpack(), EDITOR)).bundle_id  # Sodium pinned to the pack's sod-1
        service = s.service.modpackService
        await service.pin_set(bundle_id, SODIUM[0], "sod-1", EDITOR)
        await service.pin_set(bundle_id, SODIUM[0], "sod-2", EDITOR)
        await service.pin_origin_select(bundle_id, SODIUM[0], PinSource.pack, EDITOR, confirm_downgrade=True)
        await service.pin_unset(bundle_id, SODIUM[0], EDITOR)
        bundle = await s.modpackDB.bundle_get(bundle_id)
        return [(p.origin.source.value, p.origin.version_id) for p in bundle.pin_history.get(SODIUM[0], [])]

    assert mongo(scenario) == [("admin", "sod-1")]


@pytest.mark.network
def test_forty_replacements_by_a_server_never_push_the_packs_origin_out_of_what_is_stored(mongo: MongoRunner) -> None:
    """Review of tanda 2: the history kept the last HISTORY_KEPT (40) replaced origins, so a server going back
    and forth pushed the pack's own past version out of storage, and `past_origins` could only keep what was
    stored. The newest of each source is kept besides the last 40."""
    from src.database.modpack import HISTORY_KEPT

    async def scenario() -> tuple[Any, ...]:
        s = Scenario()
        bundle_id = await s.bundle()
        mod = SODIUM[0]

        async def write(*origins: PinOrigin) -> None:
            read = (await s.modpackDB.bundle_get(bundle_id)).pin_origins.get(mod) or PinRecord(origins=[], selected=PinSource.pack)
            assert await s.modpackDB.bundle_pin_record_set(
                bundle_id, mod, read.model_copy(update={"origins": list(origins)}), origins[0].version_id)

        await write(PinOrigin(source=PinSource.pack, version_id="p-1"))
        await write(PinOrigin(source=PinSource.pack, version_id="p-2"))  # p-1 is now past
        for i in range(HISTORY_KEPT + 5):
            await write(PinOrigin(source=PinSource.pack, version_id="p-2"),
                        PinOrigin(source=PinSource.server, version_id=f"s-{i % 2}", server_id="srv"))
        stored = (await s.modpackDB.bundle_get(bundle_id)).pin_history[mod]
        return (len(stored), [p.origin.version_id for p in stored if p.origin.source == PinSource.pack],
                [p.origin.version_id for p in past_origins(stored) if p.origin.source == PinSource.pack])

    size, stored_pack, shown_pack = mongo(scenario)
    assert stored_pack == ["p-1"] and shown_pack == ["p-1"]
    assert size == 41  # the last 40, and the pack's newest besides


@pytest.mark.network
def test_removing_the_mod_keeps_its_history_and_marks_it(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        s = Scenario()
        bundle_id = (await s.service.run_mrpack(mrpack(), EDITOR)).bundle_id
        # The write project_remove makes when nothing else needs the mod (its keys cleared).
        await s.modpackDB.bundle_project_remove(bundle_id, SODIUM[0], clear_keys=True)
        bundle = await s.modpackDB.bundle_get(bundle_id)
        return SODIUM[0] in bundle.pin_origins, [(p.origin.source.value, p.removed_mod) for p in bundle.pin_history[SODIUM[0]]]

    still, history = mongo(scenario)
    assert still is False and ("pack", True) in history


@pytest.mark.network
def test_a_pin_change_audits_from_which_version(mongo: MongoRunner) -> None:
    """User-walk N4: «bundle.pin» said only where a pin went; the history needs where it came from."""
    async def scenario() -> list[tuple[Any, Any]]:
        s = Scenario()
        newer = version("sod-2", SODIUM[0], "sodium jar 2", day=5)
        s.api.versions.append(newer)
        await ModrinthDatabase().version_upsert_many([ModrinthVersion.parse(**newer)])
        bundle_id = (await s.service.run_mrpack(mrpack(), EDITOR)).bundle_id  # pinned to the pack's sod-1
        service = s.service.modpackService
        await service.pin_set(bundle_id, SODIUM[0], "sod-2", EDITOR)
        await service.pin_origin_select(bundle_id, SODIUM[0], PinSource.pack, EDITOR, confirm_downgrade=True)
        await service.pin_unset(bundle_id, SODIUM[0], EDITOR)
        return [(e[2].get("from_version_id"), e[2].get("version_id")) for e in s.audit.events if e[0] == "bundle.pin"]

    assert mongo(scenario) == [("sod-1", "sod-2"), ("sod-2", "sod-1"), ("sod-1", None)]
