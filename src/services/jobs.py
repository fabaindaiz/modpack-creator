import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4
from src.database.jobs import JobDatabase
from src.model.jobs import Job
from src.services.pterodactyl import PterodactylService

Clock = Callable[[], datetime]
Handler = Callable[[Job, "JobContext"], Awaitable[None]]
logger = logging.getLogger("modpack.jobs")

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class LeaseLost(Exception):
    """The job was claimed by another worker: this one must stop without writing."""

class JobContext:
    """What a handler uses to run its steps once each, across retries."""
    def __init__(self, job: Job, jobs: JobDatabase, owner: str) -> None:
        self.job = job
        self.jobs = jobs
        self.owner = owner

    async def step(self, name: str, action: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        """
        Run a step unless a previous attempt already did, and record what it left.

        Delivery is at least once (retry-over-irreversible-effect): if the process dies after
        the action and before the checkpoint, the step runs again, so an irreversible action
        must itself check whether it already happened (find the backup by name before making one).
        """
        if name in self.job.checkpoint:
            return dict(self.job.checkpoint[name])
        result = await action()
        if not await self.jobs.checkpoint(self.job.id, self.owner, name, result):
            raise LeaseLost(f"job {self.job.id} is no longer {self.owner}'s")
        self.job.checkpoint[name] = result
        return result

class JobWorker:
    """Claims and runs jobs. Several workers, in one process or many, share the queue safely."""
    def __init__(self, jobs: JobDatabase, handlers: dict[str, Handler], owner: Optional[str] = None,
                 clock: Clock = utcnow, lease: timedelta = timedelta(seconds=60),
                 heartbeat_every: float = 20.0) -> None:
        self.jobs = jobs
        self.handlers = handlers
        self.owner = owner or f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        self.clock = clock
        self.lease = lease
        self.heartbeat_every = heartbeat_every

    async def run_once(self) -> bool:
        """Run one job, if there is one to claim; whether there was."""
        job = await self.jobs.claim(self.owner, self.clock(), self.lease)
        if job is None:
            return False
        handler = self.handlers.get(job.kind)
        if handler is None:
            await self.jobs.finish(job.id, self.owner, self.clock(), error=f"no handler for {job.kind}")
            return True
        beat = asyncio.create_task(self._heartbeat(job))
        try:
            await handler(job, JobContext(job, self.jobs, self.owner))
            await self.jobs.finish(job.id, self.owner, self.clock(), error=None)
        except LeaseLost:
            logger.warning("lease lost", extra={"job_id": str(job.id), "kind": job.kind})
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            logger.exception("job failed", extra={"job_id": str(job.id), "kind": job.kind, "attempt": job.attempts})
            if job.attempts >= job.max_attempts:
                await self.jobs.finish(job.id, self.owner, self.clock(), error=message)
            else:
                await self.jobs.release(job.id, self.owner, error=message, now=self.clock(), attempts=job.attempts)
        finally:
            beat.cancel()
        return True

    async def _heartbeat(self, job: Job) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_every)
            if not await self.jobs.heartbeat(job.id, self.owner, self.clock(), self.lease):
                return

    async def run_forever(self, stop: asyncio.Event, idle: float = 5.0) -> None:
        """Run jobs until `stop` is set, waiting `idle` seconds when the queue is empty."""
        while not stop.is_set():
            try:
                if await self.run_once():
                    continue
            except Exception:
                logger.exception("worker loop error")
            try:
                await asyncio.wait_for(stop.wait(), idle)
            except asyncio.TimeoutError:
                pass

class JobService():
    """Queues jobs and holds the handlers the worker runs. Today only reads: scanning a server.
    Every job about a server uses the key server:{id}, so a scan and a future update of the same
    server never run at once."""
    def __init__(self,
            jobDB: JobDatabase,
            pterodactylService: PterodactylService,
            ) -> None:
        self.jobDB = jobDB
        self.pterodactylService = pterodactylService

    async def enqueue_scan(self, server_id: str, full: bool = False) -> Job:
        """Queue a scan of a server; 409 if the server already has an active job."""
        return await self.jobDB.enqueue("server_scan", key=f"server:{server_id}",
                                        payload={"server_id": server_id, "full": full})

    async def get(self, job_id: str) -> Job:
        return await self.jobDB.get(job_id)

    def handlers(self) -> dict[str, Handler]:
        return {"server_scan": self._server_scan}

    async def _server_scan(self, job: Job, context: JobContext) -> None:
        async def scan() -> dict[str, Any]:
            result = await self.pterodactylService.scan(job.payload["server_id"], full=bool(job.payload.get("full")))
            return {"scan_id": str(result.id), "files": len(result.files)}
        await context.step("scan", scan)
