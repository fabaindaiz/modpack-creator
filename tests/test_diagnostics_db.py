"""Diagnóstico en MongoDB (O7): los eventos se guardan en lote, se leen en orden desde una fecha y
expiran a los 30 días por un índice TTL sobre `at` (el monitor de TTL de MongoDB borra cada 60 s)."""
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.database.diagnostics import DiagnosticsDatabase
from src.library.diagnostics import RETENTION_DAYS, DiagnosticsRecorder, derive_key
from src.model.diagnostics import DiagnosticEvent
from src.services.diagnostics import DiagnosticsService
from tests._actors import OPERATOR
from tests.conftest import MongoRunner

pytestmark = pytest.mark.network

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def test_the_collection_expires_its_events_after_thirty_days(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, Any]:
        return dict(await DiagnosticEvent.get_pymongo_collection().index_information())

    indexes = mongo(scenario)
    ttl = [index for index in indexes.values() if "expireAfterSeconds" in index]
    assert RETENTION_DAYS == 30
    assert [(index["key"], index["expireAfterSeconds"]) for index in ttl] == [([("at", 1)], 30 * 24 * 3600)]


def test_events_are_stored_in_a_batch_and_read_back_in_order_from_a_date(mongo: MongoRunner) -> None:
    db = DiagnosticsDatabase()
    events = [
        {"at": T0 - timedelta(days=2), "kind": "request", "method": "GET", "route": "/", "status": 200,
         "duration_ms": 1.0, "request_id": "old", "user": None},
        {"at": T0, "kind": "job", "job_kind": "server_scan", "job_id": "j1", "attempt": 1, "outcome": "done",
         "duration_ms": 9.5},
        {"at": T0 - timedelta(hours=1), "kind": "error", "source": "request", "error_type": "ValueError",
         "message": "boom", "traceback": "Traceback...", "request_id": "r2", "route": "/x", "user": "abcd"},
    ]

    async def scenario() -> list[dict[str, Any]]:
        await db.insert_many(events)
        return [event async for event in db.since(T0 - timedelta(days=1))]

    found = mongo(scenario)
    assert [(e["kind"], e.get("request_id")) for e in found] == [("error", "r2"), ("job", None)]
    assert "_id" not in found[0] and "id" not in found[0] and "user" in found[0]
    assert found[1]["at"].replace(tzinfo=timezone.utc) == T0


def test_the_recorder_flushes_into_the_collection_and_the_export_reads_it(mongo: MongoRunner) -> None:
    recorder = DiagnosticsRecorder(derive_key("s"), clock=lambda: T0)
    recorder.add({"at": T0, "kind": "request", "method": "GET", "route": "/", "status": 200, "duration_ms": 2.0,
                  "request_id": "r1", "user": None})
    db = DiagnosticsDatabase()
    service = DiagnosticsService(db, version="0.3.2", clock=lambda: T0 + timedelta(minutes=1), recorder=recorder)

    async def scenario() -> list[str]:
        await recorder.flush(db.insert_many)
        return [line async for line in await service.export(1, actor=OPERATOR)]

    lines = [json.loads(line) for line in mongo(scenario)]
    assert [line.get("type", line.get("kind")) for line in lines] == ["header", "request", "end"]
    assert lines[1]["request_id"] == "r1" and lines[-1]["complete"] is True
