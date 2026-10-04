"""Acciones de operador: solo un operador escribe en un servidor, y cada pedido queda auditado
antes de encolarse.

- Un editor recibe 403 antes de que el servicio lea o escriba nada: ni un `Job` ni un evento.
- El modo simulación también es de operador: lee el panel en nombre de una escritura.
- El evento se graba antes que el `Job` (el hook de la auditoría cuenta los jobs al grabar), y
  si la auditoría falla no se encola nada.
- Las importaciones y la asociación a un bundle se auditan después de tener éxito: solo tocan
  Mongo y leen el panel.
- Un `Job` guardado antes de `requested_by` se sigue leyendo.
- El rollback (d-7985ee-5445a0): un rollback fallido no cierra la actualización; tras un A exitoso
  solo queda B, y tras un B exitoso nada (D1). Cualquier actualización real posterior la supera,
  haya escrito o no; una simulación nunca (R17, R15).
"""
from datetime import datetime, timedelta, timezone
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
        failed = await Job(kind="server_update", key=f"server:{s.server_id}", payload={"server_id": s.server_id, "dry_run": False},
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
        failed = await Job(kind="server_update", key=f"server:{s.server_id}", payload={"server_id": s.server_id, "dry_run": False},
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
        await Job(kind="server_scan", key=f"server:{s.server_id}", payload={"server_id": s.server_id, "dry_run": False},
                  created_at=NOW).insert()
        failed = await Job(kind="server_update", key=f"server:{s.server_id}", payload={"server_id": s.server_id, "dry_run": False},
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


async def failed_update(server_id: str, created_at: datetime, dry_run: bool = False) -> Job:
    return await Job(kind="server_update", key=f"server:{server_id}",
                     payload={"server_id": server_id, "dry_run": dry_run}, status="failed", active=False,
                     checkpoint={"swap": {}, "backup": {}}, created_at=created_at).insert()


def test_rollback_of_a_dry_run_is_refused_before_auditing(mongo: MongoRunner) -> None:
    """A dry run checkpoints `swap` and `backup` without touching the server: nothing to undo."""
    async def scenario() -> tuple[int, int, int]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit)
        dry = await failed_update(s.server_id, NOW, dry_run=True)
        with pytest.raises(HTTPException) as refused:
            await s.update.enqueue_rollback(s.server_id, str(dry.id), "A", actor=OPERATOR)
        return refused.value.status_code, await count_jobs(), len(audit.events)

    assert mongo(scenario) == (409, 1, 0)


def test_rollback_of_a_superseded_update_is_refused_before_auditing(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[int], int, int]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit)
        old = await failed_update(s.server_id, NOW)
        newer = await failed_update(s.server_id, NOW + timedelta(hours=1))
        # A later dry run does not supersede anything: it wrote nothing.
        await failed_update(s.server_id, NOW + timedelta(hours=2), dry_run=True)
        with pytest.raises(HTTPException) as refused:
            await s.update.enqueue_rollback(s.server_id, str(old.id), "A", actor=OPERATOR)
        codes = [refused.value.status_code]
        job = await s.update.enqueue_rollback(s.server_id, str(newer.id), "A", actor=OPERATOR)
        codes.append(200 if job.id else 0)
        return codes, await count_jobs(), len(audit.events)

    # Refused: no event. The latest real update does queue: one event, one more job (4 in all).
    assert mongo(scenario) == ([409, 200], 4, 1)


async def rollback(server_id: str, update: Job, mode: str, created_at: datetime, status: str = "failed") -> Job:
    return await Job(kind="server_rollback", key=f"server:{server_id}",
                     payload={"server_id": server_id, "update_id": str(update.id), "mode": mode}, status=status,
                     active=False, created_at=created_at).insert()


def job_service() -> Any:
    from src.services.jobs import JobService

    return JobService(JobDatabase(), None, None, RecordingAudit())  # type: ignore[arg-type]


async def refusal(s: Any, update: Job, mode: str) -> tuple[int, str]:
    with pytest.raises(HTTPException) as refused:
        await s.update.enqueue_rollback(s.server_id, str(update.id), mode, actor=OPERATOR)
    return refused.value.status_code, str(refused.value.detail)


# Las reglas del dueño para el rollback (d-7985ee-5445a0): D1 y R17.


def test_b_after_a_done_is_accepted_and_a_is_refused(mongo: MongoRunner) -> None:
    """D1: tras un A exitoso de U solo queda B, que pierde lo jugado desde el backup. Contra el
    panel con estado: la actualización falla, A la revierte, y B todavía restaura el backup."""
    async def scenario() -> tuple[Any, ...]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit, crash_after_start=3)
        failed = await s.run(dry_run=False)
        s.panel.crash_after_start = None
        reverted = await s.rollback(failed, "A")
        state = await job_service().rollback_state(s.server_id)
        events = len(audit.events)
        jobs = await count_jobs()
        refused = await refusal(s, failed, "A")
        unchanged = (len(audit.events) == events, await count_jobs() == jobs)
        b = await s.rollback(failed, "B")
        return (reverted.status, state.update.id == failed.id if state else None, state.allowed if state else None,
                state.reverted_with_a.id == reverted.id if state and state.reverted_with_a else None,
                refused, unchanged, b.status, b.payload["mode"], len(audit.events) == events + 1)

    (a_status, same_update, allowed, reverted_with_a, refused, (no_event, no_job), b_status, b_mode,
     one_more_event) = mongo(scenario)
    assert a_status == "done" and same_update and allowed == ["B"] and reverted_with_a
    assert refused == (409, "Ya se revirtió con A: solo queda B") and no_event and no_job
    assert (b_status, b_mode, one_more_event) == ("done", "B", True)


def test_nothing_after_b_done(mongo: MongoRunner) -> None:
    """D1: tras un B exitoso de U no queda nada: ni A ni otro B, sin evento ni job."""
    async def scenario() -> tuple[Any, ...]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit, crash_after_start=3)
        failed = await s.run(dry_run=False)
        s.panel.crash_after_start = None
        restored = await s.rollback(failed, "B")
        events, jobs = len(audit.events), await count_jobs()
        refused = [await refusal(s, failed, mode) for mode in ("A", "B")]
        state = await job_service().rollback_state(s.server_id)
        return restored.status, refused, len(audit.events) - events, await count_jobs() - jobs, state

    status, refused, new_events, new_jobs, state = mongo(scenario)
    assert status == "done"
    assert refused == [(409, "La actualización ya se revirtió con B")] * 2
    assert (new_events, new_jobs, state) == (0, 0, None)


def test_nothing_after_b_done_even_after_a_done(mongo: MongoRunner) -> None:
    """A, luego B, ambos exitosos: el B cierra U; el orden de los rechazos da el mensaje del B."""
    async def scenario() -> tuple[Any, ...]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit)
        update = await failed_update(s.server_id, NOW)
        await rollback(s.server_id, update, "A", NOW + timedelta(hours=1), status="done")
        await rollback(s.server_id, update, "B", NOW + timedelta(hours=2), status="done")
        refused = [await refusal(s, update, mode) for mode in ("A", "B")]
        return refused, len(audit.events), await job_service().rollback_state(s.server_id)

    refused, events, state = mongo(scenario)
    assert refused == [(409, "La actualización ya se revirtió con B")] * 2 and events == 0 and state is None


def test_an_update_id_in_capitals_is_stored_as_the_update_id(mongo: MongoRunner) -> None:
    """The same id in capitals finds the same job: the rollback stores the update's own id, or
    rollbacks_of would not find it and a done B could be followed by another one."""
    async def scenario() -> tuple[Any, ...]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit)
        update = await failed_update(s.server_id, NOW)
        job = await s.update.enqueue_rollback(s.server_id, str(update.id).upper(), "B", actor=OPERATOR)
        await Job.find_one(Job.id == job.id).update({"$set": {"status": "done", "active": False}})
        refused = await refusal(s, Job.model_construct(id=str(update.id).upper()), "B")
        return job.payload["update_id"] == str(update.id), audit.events[0][2]["update_id"] == str(update.id), refused

    assert mongo(scenario) == (True, True, (409, "La actualización ya se revirtió con B"))


@pytest.mark.parametrize("failed_mode,requested", [("A", "A"), ("A", "B"), ("B", "A"), ("B", "B")])
def test_failed_rollback_does_not_close_the_update(mongo: MongoRunner, failed_mode: str, requested: str) -> None:
    """D1: un rollback fallido deja el servidor parado y U abierta: A y B se siguen ofreciendo."""
    async def scenario() -> tuple[Any, ...]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit)
        update = await failed_update(s.server_id, NOW)
        await rollback(s.server_id, update, failed_mode, NOW + timedelta(hours=1))
        state = await job_service().rollback_state(s.server_id)
        job = await s.update.enqueue_rollback(s.server_id, str(update.id), requested, actor=OPERATOR)
        return (state.update.id == update.id if state else None, state.allowed if state else None,
                state.reverted_with_a if state else "no state", job.payload["mode"], len(audit.events),
                (state.failed_b is not None) if state else None,
                state.last_failed.payload["mode"] if state and state.last_failed else None)

    # The panel says a B attempt failed (RollbackState.failed_b); a failed A is not that. The newest
    # failed attempt, A or B, is RollbackState.last_failed.
    assert mongo(scenario) == (True, ["A", "B"], None, requested, 1, failed_mode == "B", failed_mode)


def test_last_failed_is_the_newest_failed_attempt(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        s = await update_setup()
        update = await failed_update(s.server_id, NOW)
        clean = await job_service().rollback_state(s.server_id)
        await rollback(s.server_id, update, "B", NOW + timedelta(hours=1))
        newest = await rollback(s.server_id, update, "A", NOW + timedelta(hours=2))
        state = await job_service().rollback_state(s.server_id)
        return (clean.last_failed if clean else "no state",
                state.last_failed.id == newest.id if state and state.last_failed else None)

    assert mongo(scenario) == (None, True)


@pytest.mark.parametrize("status", ["failed", "done"])
@pytest.mark.parametrize("checkpoint", [{}, {"preflight": {}}, {"preflight": {}, "staging": {}},
                                        {"backup": {}}, {"backup": {}, "swap": {}}])
def test_any_later_real_update_supersedes(mongo: MongoRunner, checkpoint: dict[str, Any], status: str) -> None:
    """R17: cualquier actualización real posterior supera a U, haya escrito o no: también una
    rechazada en el preflight, sin checkpoint. U ya no se ofrece; se restaura a mano."""
    async def scenario() -> tuple[Any, ...]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit)
        update = await failed_update(s.server_id, NOW)
        # A rollback of U in between does not change who supersedes whom.
        await rollback(s.server_id, update, "A", NOW + timedelta(minutes=30))
        later = await Job(kind="server_update", key=f"server:{s.server_id}",
                          payload={"server_id": s.server_id, "dry_run": False}, status=status, active=False,
                          checkpoint=checkpoint, created_at=NOW + timedelta(hours=1)).insert()
        refused = [(await refusal(s, update, mode))[0] for mode in ("A", "B")]
        state = await job_service().rollback_state(s.server_id)
        return (refused, await count_jobs(), len(audit.events),
                state.update.id == later.id if state else None)

    refused, jobs, events, state_is_later = mongo(scenario)
    assert (refused, jobs, events) == ([409, 409], 3, 0)
    # Only a later update that failed after reaching backup or swap is itself offered.
    assert state_is_later is (True if status == "failed" and "backup" in checkpoint else None)


def test_a_later_dry_run_does_not_supersede(mongo: MongoRunner) -> None:
    """R15: una simulación posterior, terminada o fallida, no escribió nada: U sigue siendo la
    que se revierte, aunque su checkpoint tenga `backup` y `swap`."""
    async def scenario() -> tuple[Any, ...]:
        audit = RecordingAudit()
        s = await update_setup(audit=audit)
        update = await failed_update(s.server_id, NOW)
        await failed_update(s.server_id, NOW + timedelta(hours=1), dry_run=True)
        await Job(kind="server_update", key=f"server:{s.server_id}",
                  payload={"server_id": s.server_id, "dry_run": True}, status="done", active=False,
                  checkpoint={"preflight": {}, "backup": {}, "swap": {}}, created_at=NOW + timedelta(hours=2)).insert()
        state = await job_service().rollback_state(s.server_id)
        job = await s.update.enqueue_rollback(s.server_id, str(update.id), "B", actor=OPERATOR)
        return (state.update.id == update.id if state else None, state.allowed if state else None,
                job.payload["update_id"] == str(update.id), len(audit.events))

    assert mongo(scenario) == (True, ["A", "B"], True, 1)


def test_the_state_offers_only_the_steps_the_update_reached(mongo: MongoRunner) -> None:
    async def scenario() -> list[Any]:
        s = await update_setup()
        found = []
        checkpoints: list[dict[str, Any]] = [{"backup": {}}, {"backup": {}, "swap": {}}, {"preflight": {}}]
        for hours, checkpoint in enumerate(checkpoints):
            await Job(kind="server_update", key=f"server:{s.server_id}",
                      payload={"server_id": s.server_id, "dry_run": False}, status="failed", active=False,
                      checkpoint=checkpoint, created_at=NOW + timedelta(hours=hours)).insert()
            state = await job_service().rollback_state(s.server_id)
            found.append(state.allowed if state else None)
        return found

    assert mongo(scenario) == [["B"], ["A", "B"], None]


def test_rollbacks_of_lists_the_rollbacks_of_one_update_newest_first(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        s = await update_setup()
        update = await failed_update(s.server_id, NOW)
        other = await failed_update(s.server_id, NOW - timedelta(days=1))
        await rollback(s.server_id, other, "A", NOW - timedelta(hours=1))
        await rollback(s.server_id, update, "A", NOW + timedelta(hours=1))
        await rollback(s.server_id, update, "B", NOW + timedelta(hours=2), status="done")
        found = await JobDatabase().rollbacks_of(str(update.id))
        return [j.payload["mode"] for j in found], [j.payload["update_id"] for j in found]

    modes, update_ids = mongo(scenario)
    assert modes == ["B", "A"] and len(set(update_ids)) == 1


def test_rollback_state_survives_sixty_newer_scans(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, Any, Any]:
        s = await update_setup()
        failed = await failed_update(s.server_id, NOW)
        for minute in range(60):
            await Job(kind="server_scan", key=f"server:{s.server_id}", payload={"server_id": s.server_id, "dry_run": False},
                      status="done", active=False, created_at=NOW + timedelta(minutes=minute + 1)).insert()
        await failed_update(s.server_id, NOW + timedelta(days=1), dry_run=True)
        service = job_service()
        state = await service.rollback_state(s.server_id)
        await Job(kind="server_scan", key=f"server:{s.server_id}", payload={}, created_at=NOW).insert()
        active = await service.active_for_server(s.server_id)
        return state.update.id if state else None, failed.id, active.kind if active else None

    found, failed_id, active_kind = mongo(scenario)
    assert found == failed_id and active_kind == "server_scan"


def test_jobs_have_an_index_for_the_rollbacks_of_an_update(mongo: MongoRunner) -> None:
    """rollbacks_of filters by `payload.update_id` among the rollbacks and sorts by `created_at`:
    a partial index serves it without reading every scan of the collection."""
    async def scenario() -> Any:
        info = await Job.get_pymongo_collection().index_information()
        return {tuple(spec["key"]): spec.get("partialFilterExpression") for spec in info.values()}

    indexes = mongo(scenario)
    assert indexes.get((("payload.update_id", 1), ("created_at", -1))) == {"kind": "server_rollback"}


def test_jobs_have_an_index_for_the_newest_job_of_a_key(mongo: MongoRunner) -> None:
    """last_real_update filters by `key` and sorts by `created_at` descending: an index serves it
    instead of scanning every job of the collection. Created at startup by init_beanie."""
    async def scenario() -> Any:
        info = await Job.get_pymongo_collection().index_information()
        return [spec["key"] for spec in info.values()]

    assert [("key", 1), ("created_at", -1)] in mongo(scenario)
