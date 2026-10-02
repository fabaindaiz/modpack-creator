"""Acciones de operador: solo un operador escribe en un servidor, y cada pedido queda auditado
antes de encolarse.

- Un editor recibe 403 antes de que el servicio lea o escriba nada: ni un `Job` ni un evento.
- El modo simulación también es de operador: lee el panel en nombre de una escritura.
- El evento se graba antes que el `Job` (el hook de la auditoría cuenta los jobs al grabar), y
  si la auditoría falla no se encola nada.
- Las importaciones y la asociación a un bundle se auditan después de tener éxito: solo tocan
  Mongo y leen el panel.
- Un `Job` guardado antes de `requested_by` se sigue leyendo.
"""
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from src.database.jobs import JobDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.exceptions import HTTP_403_FORBIDDEN
from src.model.audit import AuditTarget
from src.model.jobs import Job
from src.model.modpack import ModpackBundle
from src.model.pterodactyl import PterodactylServer
from tests._actors import EDITOR, OPERATOR, RecordingAudit
from tests.conftest import MongoRunner
from tests.test_import import LITHIUM, fabric_jar, linked_server
from tests.test_server_scan import FakePanel, HashModrinthAPI, build_with_fabric_api
from tests.test_server_scan import setup as server_setup
from tests.test_update import setup as update_setup

pytestmark = pytest.mark.network

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


async def count_jobs() -> int:
    return await Job.find_all().count()


def test_editor_cannot_enqueue_update_and_nothing_is_queued(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, int]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit)
        with pytest.raises(HTTP_403_FORBIDDEN):
            await s.update.enqueue_update(s.server_id, actor=EDITOR, dry_run=False)
        with pytest.raises(HTTP_403_FORBIDDEN):
            await s.update.enqueue_rollback(s.server_id, "000000000000000000000000", "A", actor=EDITOR)
        return await count_jobs(), len(audit.events)

    assert mongo(scenario) == (0, 0)


def test_editor_cannot_dry_run_either(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, int]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit)
        with pytest.raises(HTTP_403_FORBIDDEN):
            await s.update.enqueue_update(s.server_id, actor=EDITOR, dry_run=True)
        return await count_jobs(), len(audit.events)

    assert mongo(scenario) == (0, 0)


def test_operator_update_records_the_event_before_the_job(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, list[tuple[str, Any, Any, Any, Any]], Any]:
        audit = RecordingAudit(on_record=count_jobs)
        s = await update_setup(audit=audit)
        job = await s.update.enqueue_update(s.server_id, actor=OPERATOR, dry_run=False)
        stored = await JobDatabase().get(str(job.id))
        return s.server_id, audit.events, stored.requested_by

    server_id, events, requested_by = mongo(scenario)
    # hook_result 0: cuando se grabó el evento, todavía no había ningún job.
    assert events == [("server.update", AuditTarget(kind="server", id=server_id), {"build": 1, "dry_run": False}, 0, OPERATOR.ref())]
    assert requested_by == OPERATOR.ref()


def test_operator_rollback_records_the_event_before_the_job(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, str, list[tuple[str, Any, Any, Any, Any]], Any, int]:
        audit = RecordingAudit(on_record=count_jobs)
        s = await update_setup(audit=audit)
        failed = await Job(kind="server_update", key=f"server:{s.server_id}", payload={"server_id": s.server_id},
                           status="failed", active=False, checkpoint={"swap": {}}, created_at=NOW).insert()
        job = await s.update.enqueue_rollback(s.server_id, str(failed.id), "A", actor=OPERATOR)
        stored = await JobDatabase().get(str(job.id))
        return s.server_id, str(failed.id), audit.events, stored.requested_by, await count_jobs()

    server_id, update_id, events, requested_by, jobs = mongo(scenario)
    # hook_result 1: al grabar el evento solo existía la actualización fallida.
    assert events == [("server.rollback", AuditTarget(kind="server", id=server_id),
                       {"update_id": update_id, "mode": "A"}, 1, OPERATOR.ref())]
    assert requested_by == OPERATOR.ref() and jobs == 2


def test_failed_audit_queues_nothing(mongo: MongoRunner) -> None:
    async def scenario() -> int:
        s = await update_setup(audit=RecordingAudit(fail=True))
        with pytest.raises(RuntimeError):
            await s.update.enqueue_update(s.server_id, actor=OPERATOR, dry_run=False)
        failed = await Job(kind="server_update", key=f"server:{s.server_id}", payload={"server_id": s.server_id},
                           status="failed", active=False, checkpoint={"swap": {}}, created_at=NOW).insert()
        with pytest.raises(RuntimeError):
            await s.update.enqueue_rollback(s.server_id, str(failed.id), "A", actor=OPERATOR)
        return await count_jobs()

    # Only the failed update this test inserted by hand: neither request queued anything.
    assert mongo(scenario) == 1


def test_a_refused_request_is_not_audited(mongo: MongoRunner) -> None:
    """Un servidor sin bundle ni build responde 409 antes de auditar: no queda ningún evento."""
    async def scenario() -> tuple[int, int]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit)
        unlinked = await PterodactylDatabase().server_insert(PterodactylServer(
            identifier="9z9z", internal_id=2, uuid="9z9z-0", name="Creative", node="n1", description="",
            status=None, is_suspended=False, is_installing=False, is_transferring=False))
        with pytest.raises(HTTPException) as refused:
            await s.update.enqueue_update(str(unlinked.id), actor=OPERATOR)
        assert refused.value.status_code == 409
        return await count_jobs(), len(audit.events)

    assert mongo(scenario) == (0, 0)


def test_a_busy_server_is_refused_before_auditing(mongo: MongoRunner) -> None:
    """Un escaneo activo comparte la clave `server:{id}`: el 409 llega antes del evento."""
    async def scenario() -> tuple[list[int], int, int]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit)
        await Job(kind="server_scan", key=f"server:{s.server_id}", payload={"server_id": s.server_id},
                  created_at=NOW).insert()
        failed = await Job(kind="server_update", key=None, payload={"server_id": s.server_id},
                           status="failed", active=False, checkpoint={"swap": {}}, created_at=NOW).insert()
        codes = []
        for call in (s.update.enqueue_update(s.server_id, actor=OPERATOR, dry_run=False),
                     s.update.enqueue_rollback(s.server_id, str(failed.id), "A", actor=OPERATOR)):
            with pytest.raises(HTTPException) as refused:
                await call
            codes.append(refused.value.status_code)
        assert refused.value.detail == f"There is already an active job for server:{s.server_id}"
        return codes, await count_jobs(), len(audit.events)

    # Two 409s; the scan and the failed update are the only jobs; no event.
    assert mongo(scenario) == ([409, 409], 2, 0)


def test_editor_cannot_import_or_link(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, int, int, int]:
        audit = RecordingAudit()
        modrinth = HashModrinthAPI({})
        bundle_id, _ = await build_with_fabric_api(modrinth)
        service, server_id = await server_setup(FakePanel(), modrinth, audit=audit)
        for call in (lambda: service.server_link_bundle(server_id, bundle_id, actor=EDITOR),
                     lambda: service.import_server(server_id, actor=EDITOR),
                     lambda: service.import_confirm(server_id, "/mods/x.jar", LITHIUM, actor=EDITOR),
                     lambda: service.import_local(server_id, "/mods/x.jar", actor=EDITOR)):
            with pytest.raises(HTTP_403_FORBIDDEN):
                await call()
        server = await PterodactylServer.get(server_id)
        bundle = await ModpackBundle.get(bundle_id)
        assert server is not None and bundle is not None
        return server.bundle_id, len(bundle.projects), len(bundle.local_files), len(audit.events)

    assert mongo(scenario) == (None, 1, 0, 0)


def test_operator_imports_and_links_are_recorded_after_they_succeed(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, str, list[tuple[str, Any, Any, Any, Any]]]:
        service, server_id, bundle_id = await linked_server({"lithium-dev.jar": fabric_jar("lithium", "Lithium"),
                                                             "privado.jar": b"opaque"})
        await service.import_server(server_id, actor=OPERATOR)
        await service.import_confirm(server_id, "/mods/lithium-dev.jar", LITHIUM, actor=OPERATOR)
        await service.import_local(server_id, "/mods/privado.jar", actor=OPERATOR)
        return server_id, bundle_id, service.audit.events

    server_id, bundle_id, events = mongo(scenario)
    target = AuditTarget(kind="server", id=server_id)
    assert [(action, t) for action, t, _, _, _ in events] == [
        ("server.bundle", target), ("server.import", target), ("server.import.confirm", target),
        ("server.import.local", target)]
    assert events[0][2] == {"bundle_id": bundle_id}
    assert events[2][2] == {"bundle_id": bundle_id, "path": "/mods/lithium-dev.jar", "project_id": LITHIUM}
    assert events[3][2] == {"bundle_id": bundle_id, "path": "/mods/privado.jar"}
    assert [event[4] for event in events] == [OPERATOR.ref()] * 4


def test_job_without_requested_by_still_loads(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        inserted = await Job.get_pymongo_collection().insert_one({
            "kind": "scan", "key": None, "payload": {}, "status": "done", "active": False, "attempts": 1,
            "max_attempts": 3, "checkpoint": {}, "created_at": NOW})
        job = await JobDatabase().get(str(inserted.inserted_id))
        return job.requested_by

    assert mongo(scenario) is None
