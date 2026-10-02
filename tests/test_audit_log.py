"""The audit log: who did what, to what, and when; stored in MongoDB, read back newest first."""
from datetime import datetime, timedelta, timezone

import pytest

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
        second = await service.find(before=first[-1].at, limit=2)
        aware = await service.find(before=T0 + timedelta(minutes=2), limit=10)
        return [e.action for e in first], [e.action for e in second], [e.action for e in aware]

    first, second, aware = mongo(scenario)
    assert first == ["a4", "a3"]
    assert second == ["a2", "a1"]
    assert aware == ["a1", "a0"]


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
