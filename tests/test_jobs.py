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
from src.services.jobs import JobContext, JobWorker
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
        self.scanned: list[tuple[str, bool]] = []

    async def scan(self, server_id: str, full: bool = False) -> Any:
        self.scanned.append((server_id, full))
        return type("Scan", (), {"id": "scan-1", "files": [1, 2, 3]})()


def test_a_scan_job_runs_the_scan_and_is_exclusive_per_server(mongo: MongoRunner) -> None:
    from typing import cast

    from src.services.jobs import JobService

    async def scenario() -> tuple[str, dict[str, Any], list[tuple[str, bool]], int]:
        scans = FakeScans()
        service = JobService(jobDB=JobDatabase(), pterodactylService=cast(Any, scans))
        job = await service.enqueue_scan("abc", full=True)
        with pytest.raises(HTTPException) as refused:
            await service.enqueue_scan("abc")
        await JobWorker(JobDatabase(), service.handlers(), owner="w1", clock=lambda: T0).run_once()
        stored = await service.get(str(job.id))
        return stored.status, stored.checkpoint, scans.scanned, refused.value.status_code

    assert mongo(scenario) == ("done", {"scan": {"scan_id": "scan-1", "files": 3}}, [("abc", True)], 409)


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
