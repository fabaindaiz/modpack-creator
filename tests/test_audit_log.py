"""The audit log: who did what, to what, and when; stored in MongoDB, read back newest first."""
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from src.database.audit import AuditDatabase
from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.library.logging import _request_id
from src.model.audit import AuditEvent, AuditTarget
from src.model.modpack import ModpackBundle, ModpackBundlePatch
from src.model.pterodactyl import PterodactylServer
from src.model.utils import MinecraftLoaders
from src.services.audit import AuditService, TargetNames
from tests._actors import EDITOR, OPERATOR
from tests.conftest import MongoRunner
from tests.test_mod_list import project, version

pytestmark = pytest.mark.network

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _service(times: list[datetime]) -> AuditService:
    clock = iter(times)
    return AuditService(AuditDatabase(), clock=lambda: next(clock))


def test_record_stores_actor_action_target_and_time(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[AuditEvent, list[AuditEvent]]:
        service = _service([T0])
        token = _request_id.set("req-1")
        try:
            event = await service.record(OPERATOR, "server.power", AuditTarget(kind="server", id="s1"), {"signal": "stop"})
        finally:
            _request_id.reset(token)
        return event, await AuditEvent.find_all().to_list()

    event, stored = mongo(scenario)
    assert len(stored) == 1
    got = stored[0]
    assert got.actor.sub == OPERATOR.sub and got.actor.iss == OPERATOR.iss and got.actor.name == OPERATOR.name
    assert got.action == "server.power"
    assert (got.target.kind, got.target.id) == ("server", "s1")
    assert got.details == {"signal": "stop"}
    assert got.request_id == "req-1"
    assert got.at.replace(tzinfo=timezone.utc) == T0
    assert event.id == got.id


def test_find_newest_first_and_paginates_with_before(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[str], list[str], list[str]]:
        service = _service([T0 + timedelta(minutes=i) for i in range(5)])
        for i in range(5):
            await service.record(EDITOR, f"a{i}", AuditTarget(kind="bundle", id="b1"))
        first = await service.find(limit=2)
        second = await service.find(before_id=str(first[-1].id), limit=2)
        rest = await service.find(before_id=str(second[-1].id), limit=10)
        return [e.action for e in first], [e.action for e in second], [e.action for e in rest]

    first, second, rest = mongo(scenario)
    assert first == ["a4", "a3"]
    assert second == ["a2", "a1"]
    assert rest == ["a0"]


def test_find_pages_through_events_sharing_a_millisecond(mongo: MongoRunner) -> None:
    async def scenario() -> list[str]:
        service = _service([T0] * 5)
        for i in range(5):
            await service.record(EDITOR, f"a{i}", AuditTarget(kind="bundle", id="b1"))
        seen: list[str] = []
        cursor = None
        while True:
            page = await service.find(before_id=cursor, limit=2)
            if not page:
                return seen
            seen += [str(e.id) for e in page]
            cursor = str(page[-1].id)

    seen = mongo(scenario)
    assert len(seen) == 5 and len(set(seen)) == 5
    assert seen == sorted(seen, reverse=True)


def test_find_unknown_before_id_is_404(mongo: MongoRunner) -> None:
    async def scenario() -> list[int]:
        service = _service([])
        codes = []
        for bad in ("not-an-id", "0" * 24):
            try:
                await service.find(before_id=bad)
            except HTTPException as error:
                codes.append(error.status_code)
        return codes

    assert mongo(scenario) == [404, 404]


def test_limit_is_clamped_to_200(mongo: MongoRunner) -> None:
    async def scenario() -> int:
        events = [AuditEvent(at=T0 + timedelta(seconds=i), actor=OPERATOR.ref(), action="x",
                             target=AuditTarget(kind="k", id="1")) for i in range(201)]
        await AuditEvent.insert_many(events)
        return len(await AuditService(AuditDatabase()).find(limit=1000))

    assert mongo(scenario) == 200


def test_request_id_is_none_outside_a_request(mongo: MongoRunner) -> None:
    async def scenario() -> Optional[str]:
        return (await _service([T0]).record(OPERATOR, "x", AuditTarget(kind="k", id="1"))).request_id

    assert mongo(scenario) is None


def test_nested_secret_keys_are_rejected(mongo: MongoRunner) -> None:
    async def scenario() -> bool:
        try:
            await _service([T0]).record(OPERATOR, "x", AuditTarget(kind="k", id="1"), {"meta": {"api_token": "x"}})
        except ValueError:
            return True
        return False

    assert mongo(scenario)


def test_find_filters_by_exact_values(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, list[str]]:
        service = _service([T0, T0 + timedelta(minutes=1)])
        await service.record(OPERATOR, "server.power", AuditTarget(kind="server", id="s1"))
        await service.record(EDITOR, "bundle.edit", AuditTarget(kind="bundle", id="b1"))
        return {
            "actor": [e.action for e in await service.find(actor_sub=EDITOR.sub)],
            "action": [e.action for e in await service.find(action="server.power")],
            "target": [e.action for e in await service.find(target_id="b1")],
            "regex": [e.action for e in await service.find(actor_sub=".*")],
            "where": [e.action for e in await service.find(action="$where")],
            "clamped": [e.action for e in await service.find(limit=0)],
        }

    got = mongo(scenario)
    assert got["actor"] == ["bundle.edit"]
    assert got["action"] == ["server.power"]
    assert got["target"] == ["bundle.edit"]
    assert got["regex"] == []
    assert got["where"] == []
    assert len(got["clamped"]) == 1


@pytest.mark.parametrize("key", ["token", "Secret", "PASSWORD", "Authorization"])
def test_details_never_hold_unknown_secrets_keys(mongo: MongoRunner, key: str) -> None:
    async def scenario() -> tuple[str, int]:
        service = _service([T0])
        message = ""
        try:
            await service.record(OPERATOR, "x", AuditTarget(kind="k", id="1"), {"ok": 1, key: "v"})
        except ValueError as error:
            message = str(error)
        return message, await AuditEvent.find_all().count()

    message, count = mongo(scenario)
    assert message and count == 0


def test_find_filters_by_date_range_inclusive_since_exclusive_until(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, list[str]]:
        day = datetime(2026, 10, 2, tzinfo=timezone.utc)
        times = [day - timedelta(milliseconds=1), day, day + timedelta(hours=23, minutes=59),
                 day + timedelta(days=1)]
        service = _service(times)
        for i in range(4):
            await service.record(OPERATOR, f"a{i}", AuditTarget(kind="server", id="s1"))
        next_day = day + timedelta(days=1)
        return {
            "day": [e.action for e in await service.find(since=day, until=next_day)],
            "since": [e.action for e in await service.find(since=day)],
            "until": [e.action for e in await service.find(until=day)],
            "and": [e.action for e in await service.find(since=day, until=next_day, action="a2")],
            "and_none": [e.action for e in await service.find(since=day, until=next_day, action="a0")],
        }

    got = mongo(scenario)
    assert got["day"] == ["a2", "a1"]
    assert got["since"] == ["a3", "a2", "a1"]
    assert got["until"] == ["a0"]
    assert got["and"] == ["a2"]
    assert got["and_none"] == []


def test_find_pages_within_a_date_range(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        service = _service([T0 + timedelta(minutes=i) for i in range(5)])
        for i in range(5):
            await service.record(OPERATOR, f"a{i}", AuditTarget(kind="server", id="s1"))
        since, until = T0 + timedelta(minutes=1), T0 + timedelta(minutes=4)
        first = await service.find(since=since, until=until, limit=2)
        second = await service.find(since=since, until=until, limit=2, before_id=str(first[-1].id))
        return [e.action for e in first], [e.action for e in second]

    first, second = mongo(scenario)
    assert first == ["a3", "a2"]
    assert second == ["a1"]


# The target's name (A10): written with the event, so a renamed or deleted target still reads.

def _named_service(times: list[datetime]) -> AuditService:
    clock = iter(times)
    names = TargetNames(ModpackDatabase(), PterodactylDatabase(), ModrinthDatabase())
    return AuditService(AuditDatabase(), names, clock=lambda: next(clock))


def test_an_event_keeps_the_target_name_it_had(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Optional[str], Optional[str]]:
        db = ModpackDatabase()
        bundle = await db.bundle_insert(ModpackBundle(name="Survival", description="", version="1.21.1",
                                                      loader=MinecraftLoaders.fabric))
        service = _named_service([T0])
        event = await service.record(EDITOR, "bundle.pin", AuditTarget(kind="bundle", id=str(bundle.id)))
        await db.bundle_update(str(bundle.id), ModpackBundlePatch(name="Creativo"))
        stored = await AuditEvent.get(event.id)
        assert stored is not None
        return event.target.name, stored.target.name

    assert mongo(scenario) == ("Survival", "Survival")


def test_servers_and_replacements_are_named_from_the_catalog(mongo: MongoRunner) -> None:
    async def scenario() -> list[Optional[str]]:
        server = await PterodactylDatabase().server_insert(PterodactylServer(
            identifier="1a1a", internal_id=1, uuid="1a1a-0", name="Lobby", node="n1", description="",
            status=None, is_suspended=False, is_installing=False, is_transferring=False))
        await ModrinthDatabase().project_upsert_many([project("JJJJ", "JEI")])
        service = _named_service([T0, T0])
        on_server = await service.record(OPERATOR, "server.scan", AuditTarget(kind="server", id=str(server.id)))
        on_rule = await service.record(EDITOR, "replacement.set", AuditTarget(kind="replacement", id="JJJJ"))
        return [on_server.target.name, on_rule.target.name]

    assert mongo(scenario) == ["Lobby", "JEI"]


def test_a_name_given_by_the_caller_is_kept(mongo: MongoRunner) -> None:
    async def scenario() -> Optional[str]:
        gone = str(PydanticObjectId())  # the pack is already deleted: nothing to look up
        event = await _named_service([T0]).record(
            EDITOR, "bundle.delete", AuditTarget(kind="bundle", id=gone, name="Test"))
        stored = await AuditEvent.get(event.id)
        assert stored is not None
        return stored.target.name

    assert mongo(scenario) == "Test"


def test_an_unknown_target_records_without_a_name(mongo: MongoRunner) -> None:
    async def scenario() -> list[Optional[str]]:
        service = _named_service([T0] * 4)
        targets = [AuditTarget(kind="bundle", id=str(PydanticObjectId())), AuditTarget(kind="bundle", id="nuevo"),
                   AuditTarget(kind="server", id="not-an-id"), AuditTarget(kind="job", id=str(PydanticObjectId()))]
        names = [(await service.record(EDITOR, "x", target)).target.name for target in targets]
        assert await AuditEvent.find_all().count() == 4
        return names

    assert mongo(scenario) == [None, None, None, None]


def test_a_page_s_mods_and_versions_are_named_from_the_catalog(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, str]:
        catalog = ModrinthDatabase()
        await catalog.project_upsert_many([project("JJJJ", "JEI")])
        await catalog.version_upsert_many([version("VVVV", "JJJJ", "19.60", 1)])
        service = _named_service([T0, T0])
        await service.record(EDITOR, "bundle.pin", AuditTarget(kind="bundle", id="b1"),
                             {"project_id": "JJJJ", "version_id": "VVVV"})
        await service.record(EDITOR, "bundle.project.add", AuditTarget(kind="bundle", id="b1"),
                             {"refs": ["JJJJ", "ZZZZ"]})
        return await service.names_for(await service.find())

    assert mongo(scenario) == {"JJJJ": "JEI", "VVVV": "19.60"}
