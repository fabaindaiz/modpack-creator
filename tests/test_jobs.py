"""Trabajos durables en MongoDB con leases (i-7985ee-0ec295), contra MongoDB real.

Lo que se prueba es lo que la investigación (§1) y las tarjetas piden:
- reclamar es atómico: dos workers nunca corren el mismo trabajo a la vez;
- un lease vencido se reclama, y el dueño anterior ya no puede escribir (latido, checkpoint, fin);
- un trabajo activo por clave lo garantiza un índice único, no un lock en memoria
  (in-process-guarantees);
- la entrega es al menos una vez: los pasos hechos quedan en el checkpoint, y al reintentar el
  handler los ve (retry-over-irreversible-effect);
- los reintentos tienen tope: lo que no termina queda fallido, no vuelve a la cola para siempre.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from src.database.jobs import JobDatabase
from src.model.jobs import Job
from src.services.jobs import JobContext, JobWorker, WorkerBeat
from tests._actors import EDITOR, RecordingAudit
from tests.conftest import MongoRunner

pytestmark = pytest.mark.network

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
LEASE = timedelta(seconds=30)


def test_two_workers_claiming_at_once_never_get_the_same_job(mongo: MongoRunner) -> None:
    async def scenario() -> list[Any]:
        jobs = JobDatabase()
        await jobs.enqueue("scan", key="server-1", payload={})
        claims = await asyncio.gather(*[jobs.claim(f"w{i}", T0, LEASE) for i in range(5)])
        return [c.owner for c in claims if c is not None]

    owners = mongo(scenario)
    assert len(owners) == 1


def test_an_expired_lease_is_claimed_again_and_the_old_owner_can_no_longer_write(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        jobs = JobDatabase()
        job = await jobs.enqueue("scan", key="server-1", payload={})
        first = await jobs.claim("w1", T0, LEASE)
        before_expiry = await jobs.claim("w2", T0 + timedelta(seconds=10), LEASE)
        second = await jobs.claim("w2", T0 + timedelta(seconds=31), LEASE)
        assert first and second
        return (before_expiry, second.owner, second.attempts,
                await jobs.heartbeat(job.id, "w1", T0 + timedelta(seconds=32), LEASE),
                await jobs.checkpoint(job.id, "w1", "backup", {"name": "b1"}),
                await jobs.finish(job.id, "w1", T0 + timedelta(seconds=33), error=None),
                await jobs.heartbeat(job.id, "w2", T0 + timedelta(seconds=32), LEASE))

    assert mongo(scenario) == (None, "w2", 2, False, False, False, True)


def test_one_active_job_per_key(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, str]:
        jobs = JobDatabase()
        job = await jobs.enqueue("update", key="server-1", payload={})
        await jobs.enqueue("update", key="server-2", payload={})
        with pytest.raises(HTTPException) as refused:
            await jobs.enqueue("update", key="server-1", payload={})
        claimed = await jobs.claim("w1", T0, LEASE)
        assert claimed and claimed.id == job.id
        await jobs.finish(job.id, "w1", T0, error=None)
        again = await jobs.enqueue("update", key="server-1", payload={})
        return refused.value.status_code, again.status

    assert mongo(scenario) == (409, "queued")


def test_a_job_out_of_attempts_is_failed_not_claimed(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, str, bool]:
        jobs = JobDatabase()
        job = await jobs.enqueue("scan", key="server-1", payload={}, max_attempts=2)
        await jobs.claim("w1", T0, LEASE)
        await jobs.claim("w2", T0 + timedelta(minutes=1), LEASE)
        third = await jobs.claim("w3", T0 + timedelta(minutes=2), LEASE)
        stored = await jobs.get(str(job.id))
        return third, stored.status, stored.active

    assert mongo(scenario) == (None, "failed", False)


# El worker.


def test_the_worker_runs_a_job_to_done_and_records_its_steps(mongo: MongoRunner) -> None:
    async def handler(job: Job, context: JobContext) -> None:
        await context.step("first", lambda: asyncio.sleep(0, {"ok": 1}))
        await context.step("second", lambda: asyncio.sleep(0, {"ok": 2}))

    async def scenario() -> tuple[bool, str, dict[str, Any]]:
        jobs = JobDatabase()
        job = await jobs.enqueue("demo", key=None, payload={})
        ran = await JobWorker(jobs, {"demo": handler}, owner="w1", clock=lambda: T0).run_once()
        stored = await jobs.get(str(job.id))
        return ran, stored.status, stored.checkpoint

    assert mongo(scenario) == (True, "done", {"first": {"ok": 1}, "second": {"ok": 2}})


def test_a_retried_job_does_not_repeat_a_step_it_already_did(mongo: MongoRunner) -> None:
    """La entrega es al menos una vez: si el proceso muere después de un paso irreversible, el
    reintento ve su checkpoint y no lo repite (retry-over-irreversible-effect)."""
    effects: list[str] = []
    crash = {"once": True}

    async def irreversible() -> dict[str, str]:
        effects.append("backup")
        return {"name": "backup-1"}

    async def handler(job: Job, context: JobContext) -> None:
        await context.step("backup", irreversible)
        if crash.pop("once", False):
            raise RuntimeError("the process died here")
        await context.step("install", lambda: asyncio.sleep(0, {"done": True}))

    async def scenario() -> tuple[list[str], str, int, dict[str, Any]]:
        jobs = JobDatabase()
        job = await jobs.enqueue("update", key="server-1", payload={})
        clock = {"now": T0}
        worker = JobWorker(jobs, {"update": handler}, owner="w1", clock=lambda: clock["now"])
        await worker.run_once()
        failed_once = await jobs.get(str(job.id))
        assert failed_once.status == "queued" and "died" in (failed_once.error or "")
        clock["now"] = T0 + timedelta(hours=1)
        await worker.run_once()
        stored = await jobs.get(str(job.id))
        return effects, stored.status, stored.attempts, stored.checkpoint

    effects_seen, status, attempts, checkpoint = mongo(scenario)
    assert effects_seen == ["backup"] and status == "done" and attempts == 2
    assert checkpoint == {"backup": {"name": "backup-1"}, "install": {"done": True}}


def test_a_job_that_keeps_failing_ends_failed(mongo: MongoRunner) -> None:
    async def handler(job: Job, context: JobContext) -> None:
        raise ValueError("always")

    async def scenario() -> tuple[str, int, bool]:
        jobs = JobDatabase()
        job = await jobs.enqueue("demo", key="k", payload={}, max_attempts=3)
        hours = iter(range(100))
        # Each attempt an hour after the last: past every wait between retries.
        worker = JobWorker(jobs, {"demo": handler}, owner="w1", clock=lambda: T0 + timedelta(hours=next(hours)))
        while await worker.run_once():
            pass
        stored = await jobs.get(str(job.id))
        return stored.status, stored.attempts, stored.active

    assert mongo(scenario) == ("failed", 3, False)


def test_a_job_of_an_unknown_kind_fails_without_retrying(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, int]:
        jobs = JobDatabase()
        job = await jobs.enqueue("nobody-handles-this", key=None, payload={})
        await JobWorker(jobs, {}, owner="w1", clock=lambda: T0).run_once()
        stored = await jobs.get(str(job.id))
        return stored.status, stored.attempts

    assert mongo(scenario) == ("failed", 1)


def test_two_workers_run_every_job_exactly_once(mongo: MongoRunner) -> None:
    runs: list[str] = []

    async def handler(job: Job, context: JobContext) -> None:
        runs.append(job.payload["n"])
        await asyncio.sleep(0.01)

    async def drain(worker: JobWorker) -> None:
        while await worker.run_once():
            pass

    async def scenario() -> list[str]:
        jobs = JobDatabase()
        for n in range(12):
            await jobs.enqueue("demo", key=None, payload={"n": str(n)})
        workers = [JobWorker(jobs, {"demo": handler}, owner=f"w{i}", clock=lambda: T0) for i in range(2)]
        await asyncio.gather(*[drain(w) for w in workers])
        return sorted(runs, key=int)

    assert mongo(scenario) == [str(n) for n in range(12)]


# Escanear en segundo plano: el primer tipo de trabajo, de solo lectura.


class FakeScans:
    def __init__(self) -> None:
        self.scanned: list[tuple[Any, ...]] = []

    async def scan(self, server_id: str, actor: Any, full: bool = False, report: Any = None,
                   directory: str = "/mods") -> Any:
        self.scanned.append((server_id, full) if directory == "/mods" else (server_id, full, directory))
        return type("Scan", (), {"id": "scan-1", "files": [1, 2, 3]})()

    async def drift(self, server_id: str) -> None:
        return None


def test_a_scan_job_runs_the_scan_and_is_exclusive_per_server(mongo: MongoRunner) -> None:
    from typing import cast

    from src.services.jobs import JobService

    async def scenario() -> tuple[str, dict[str, Any], list[tuple[str, bool]], int]:
        scans = FakeScans()
        service = JobService(jobDB=JobDatabase(), pterodactylService=cast(Any, scans), pterodactylDB=cast(Any, None), audit=RecordingAudit())
        job = await service.enqueue_scan("abc", actor=EDITOR, full=True)
        with pytest.raises(HTTPException) as refused:
            await service.enqueue_scan("abc", actor=EDITOR)
        await JobWorker(JobDatabase(), service.handlers(), owner="w1", clock=lambda: T0).run_once()
        stored = await service.get(str(job.id))
        return stored.status, stored.checkpoint, scans.scanned, refused.value.status_code

    assert mongo(scenario) == ("done", {"scan": {"scan_id": "scan-1", "files": 3}, "drift": {"notified": False}},
                               [("abc", True)], 409)


def test_a_scan_job_of_another_directory_scans_it_and_compares_nothing(mongo: MongoRunner) -> None:
    # O6: POST /v1/server/{id}/scan queues the job and keeps its `directory`; only /mods has a drift.
    from typing import cast

    from src.services.jobs import JobService

    async def scenario() -> tuple[Any, ...]:
        scans = FakeScans()
        service = JobService(jobDB=JobDatabase(), pterodactylService=cast(Any, scans), pterodactylDB=cast(Any, None), audit=RecordingAudit())
        job = await service.enqueue_scan("abc", actor=EDITOR, directory="/config")
        await JobWorker(JobDatabase(), service.handlers(), owner="w1", clock=lambda: T0).run_once()
        stored = await service.get(str(job.id))
        return stored.status, stored.payload, stored.checkpoint, scans.scanned

    status, payload, checkpoint, scanned = mongo(scenario)
    assert status == "done" and payload["directory"] == "/config" and "drift" not in checkpoint
    assert scanned == [("abc", False, "/config")]


def test_a_failed_attempt_waits_before_the_next_one(mongo: MongoRunner) -> None:
    """Contra un panel caído, tres intentos seguidos solo lo martillan: el siguiente espera."""
    async def handler(job: Job, context: JobContext) -> None:
        raise ConnectionError("panel down")

    async def scenario() -> tuple[bool, bool, int]:
        jobs = JobDatabase()
        await jobs.enqueue("demo", key="k", payload={}, now=T0)
        clock = {"now": T0}
        worker = JobWorker(jobs, {"demo": handler}, owner="w1", clock=lambda: clock["now"])
        await worker.run_once()
        too_soon = await worker.run_once()
        clock["now"] = T0 + timedelta(minutes=5)
        later = await worker.run_once()
        stored = await JobDatabase().get(str((await Job.find_one(Job.kind == "demo")).id))  # type: ignore[union-attr]
        return too_soon, later, stored.attempts

    assert mongo(scenario) == (False, True, 2)


# El programador de escaneos.


def test_the_scheduler_queues_what_is_due_and_a_full_scan_when_the_last_one_is_old(mongo: MongoRunner) -> None:
    from typing import cast

    from src.database.pterodactyl import PterodactylDatabase
    from src.model.pterodactyl import PterodactylServer, ServerScan
    from src.services.jobs import JobService

    def server(name: str) -> PterodactylServer:
        return PterodactylServer(identifier=name, internal_id=1, uuid=name, name=name, node="n", description="",
                                 status=None, is_suspended=False, is_installing=False, is_transferring=False)

    async def scenario() -> tuple[dict[str, bool], list[Job]]:
        db = PterodactylDatabase()
        never = await db.server_insert(server("never"))
        fresh = await db.server_insert(server("fresh"))
        stale_full = await db.server_insert(server("stale"))
        await db.scan_insert(ServerScan(server_id=fresh.id, directory="/mods", scanned_at=T0 - timedelta(hours=1), full=True, files=[]))  # type: ignore[arg-type]
        await db.scan_insert(ServerScan(server_id=stale_full.id, directory="/mods", scanned_at=T0 - timedelta(days=8), full=True, files=[]))  # type: ignore[arg-type]
        await db.scan_insert(ServerScan(server_id=stale_full.id, directory="/mods", scanned_at=T0 - timedelta(hours=7), full=False, files=[]))  # type: ignore[arg-type]
        service = JobService(jobDB=JobDatabase(), pterodactylService=cast(Any, None), pterodactylDB=db, audit=RecordingAudit())
        first = await service.schedule_scans(T0, every=timedelta(hours=6), full_every=timedelta(days=7))
        again = await service.schedule_scans(T0, every=timedelta(hours=6), full_every=timedelta(days=7))
        names = {str(never.id): "never", str(stale_full.id): "stale"}
        return {names[j.payload["server_id"]]: j.payload["full"] for j in first}, again

    queued, again = mongo(scenario)
    # "fresh" se escaneó hace una hora: no toca. "stale" toca, y completo: el último completo
    # tiene 8 días (una restauración conserva fechas viejas; la caché no lo vería).
    assert queued == {"never": True, "stale": True}
    assert again == []


@pytest.mark.network
def test_worker_beats_while_a_long_step_runs(mongo: MongoRunner) -> None:
    class CountingBeat(WorkerBeat):
        calls = 0

        def beat(self) -> None:
            type(self).calls += 1
            super().beat()

    async def scenario() -> int:
        release = asyncio.Event()

        async def handler(job: Job, context: JobContext) -> None:
            await release.wait()

        jobs = JobDatabase()
        await jobs.enqueue("demo", key=None, payload={})
        beat = CountingBeat()
        worker = JobWorker(jobs, {"demo": handler}, owner="w1", heartbeat_every=0.01, beat=beat)
        task = asyncio.create_task(worker.run_once())
        before = beat.calls
        for _ in range(200):
            if beat.calls - before >= 2:
                break
            await asyncio.sleep(0.01)
        seen = beat.calls - before
        release.set()
        await task
        return seen

    assert mongo(scenario) >= 2


def test_find_filters_by_exact_server_kind_and_status_and_pages_by_id(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        db = JobDatabase()
        a = await db.enqueue("server_scan", key="server:s1", payload={}, now=T0)
        await Job.get_pymongo_collection().update_one({"_id": a.id}, {"$set": {"active": False}})
        for minutes, (kind, key, status) in enumerate([
                ("server_scan", "server:s1", "done"), ("server_update", "server:s1", "failed"),
                ("server_scan", "server:s2", "done"), ("notify", None, "done")], start=1):
            job = await db.enqueue(kind, key=key, payload={}, now=T0 + timedelta(minutes=minutes))
            await Job.get_pymongo_collection().update_one(
                {"_id": job.id}, {"$set": {"status": status, "active": False}})
        literal = await db.enqueue("server_scan", key="server:.*", payload={}, now=T0 + timedelta(minutes=9))
        everything = await db.find(server_id=None, kind=None, status=None, before_id=None, limit=100)
        s1 = await db.find(server_id="s1", kind=None, status=None, before_id=None, limit=100)
        both = await db.find(server_id="s1", kind="server_scan", status="done", before_id=None, limit=100)
        by_status = await db.find(server_id=None, kind=None, status="failed", before_id=None, limit=100)
        pattern = await db.find(server_id=".*", kind=None, status=None, before_id=None, limit=100)
        kind_pattern = await db.find(server_id=None, kind="server_.*", status=None, before_id=None, limit=100)
        page1 = await db.find(server_id=None, kind=None, status=None, before_id=None, limit=2)
        page2 = await db.find(server_id=None, kind=None, status=None, before_id=str(page1[-1].id), limit=2)
        clamped = await db.find(server_id=None, kind=None, status=None, before_id=None, limit=0)
        try:
            await db.find(server_id=None, kind=None, status=None, before_id="zzz", limit=5)
            bad = 0
        except HTTPException as error:
            bad = error.status_code
        return (len(everything), [j.kind for j in s1], len(both), len(by_status), [j.id for j in pattern] == [literal.id],
                len(kind_pattern), [j.id for j in page1 + page2], [j.id for j in everything], len(clamped), bad)

    total, s1_kinds, both, failed, literal_only, kind_pattern, paged, ordered, clamped, bad = mongo(scenario)
    assert total == 6 and s1_kinds == ["server_update", "server_scan", "server_scan"]
    assert both == 1 and failed == 1 and literal_only and kind_pattern == 0
    assert paged == ordered[:4] and clamped == 1 and bad == 404


# Avance visible (spec 2026-10-09-economia-de-requests, E7–E8): qué se procesa, cuánto va y si
# espera turno, para que se vea que la app no se quedó pegada.


def test_a_job_reports_what_it_processes_and_how_far_it_is(mongo: MongoRunner) -> None:
    async def handler(job: Job, context: JobContext) -> None:
        await context.progress("Versiones de los mods", 3, 10)

    async def scenario() -> tuple[Any, Any]:
        jobs = JobDatabase()
        job = await jobs.enqueue("demo", key=None, payload={})
        await JobWorker(jobs, {"demo": handler}, owner="w1", clock=lambda: T0).run_once()
        stored = await jobs.get(str(job.id))
        return stored.progress, stored.updated_at

    progress, updated_at = mongo(scenario)
    assert progress is not None and (progress.label, progress.done, progress.total) == ("Versiones de los mods", 3, 10)
    assert updated_at is not None and updated_at.replace(tzinfo=timezone.utc) == T0


def test_a_job_waiting_for_its_turn_says_until_when_and_progress_clears_it(mongo: MongoRunner) -> None:
    from src.library.api.limiter import ON_WAIT

    seen: list[Any] = []

    async def handler(job: Job, context: JobContext) -> None:
        on_wait = ON_WAIT.get()
        assert on_wait is not None
        on_wait(12.0)
        await asyncio.sleep(0.05)
        seen.append((await JobDatabase().get(str(job.id))).waiting_until)
        await context.progress("Versiones de los mods", 1, 2)

    async def scenario() -> Any:
        jobs = JobDatabase()
        job = await jobs.enqueue("demo", key=None, payload={})
        await JobWorker(jobs, {"demo": handler}, owner="w1", clock=lambda: T0).run_once()
        return (await jobs.get(str(job.id))).waiting_until

    after = mongo(scenario)
    assert seen[0] is not None and seen[0].replace(tzinfo=timezone.utc) == T0 + timedelta(seconds=12)
    assert after is None


def test_the_polled_job_lists_leave_out_payload_content_and_checkpoints(mongo: MongoRunner) -> None:
    # Code review (2026-10-09): the banner and the bar poll every 2-3 s, and an import job's payload
    # holds the whole read .mrpack.
    async def scenario() -> tuple[Any, Any]:
        jobs = JobDatabase()
        job = await jobs.enqueue("modpack_import", key="import:x", requested_by=EDITOR.ref(),
                                 payload={"content": {"files": ["f"] * 5000}, "source": "Pack 1.0"})
        await Job.get_pymongo_collection().update_one({"_id": job.id}, {"$set": {"checkpoint": {"import": {"big": "x" * 1000}}}})
        [mine] = await jobs.active_for_requester(EDITOR.iss, EDITOR.sub)
        [keyed] = await jobs.active_for_keys(["import:x"])
        return mine, keyed

    mine, keyed = mongo(scenario)
    for found in (mine, keyed):
        assert "content" not in found.payload and found.payload["source"] == "Pack 1.0"
        assert found.checkpoint == {} and found.kind == "modpack_import"
