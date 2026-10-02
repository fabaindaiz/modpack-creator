"""The audit log: who did what, to what, and when; stored in MongoDB, read back newest first."""
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
from fastapi import HTTPException

from src.database.audit import AuditDatabase
from src.library.logging import _request_id
from src.model.audit import AuditEvent, AuditTarget
from src.services.audit import AuditService
from tests._actors import EDITOR, OPERATOR
from tests.conftest import MongoRunner

pytestmark = pytest.mark.network

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _service(times: list[datetime]) -> AuditService:
    clock = iter(times)
    return AuditService(AuditDatabase(), lambda: next(clock))


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
