import asyncio
import logging
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Literal, Optional, Protocol, runtime_checkable
from uuid import uuid4
from src.database.jobs import JobDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.exceptions import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT
from src.model.jobs import Job, JobProgress, RollbackState, UpdateState, is_real_update
from src.library.api.limiter import ON_WAIT
from src.model.audit import AuditTarget
from src.model.users import Actor
from src.services.audit import AuditSink
from src.library.utils import as_utc, utcnow
from src.services.pterodactyl import SCAN_DIRECTORY, PterodactylService

# How often a periodic scan is a full one when SCAN_FULL_EVERY_DAYS is not set (owner, 2026-10-12):
# the server's activity sees a restored backup, but not a move between nodes, a reinstall from the
# admin panel or activity pruned early; a weekly full scan bounds those.
FULL_SCAN_DEFAULT_DAYS = 7
Clock = Callable[[], datetime]
Handler = Callable[[Job, "JobContext"], Awaitable[None]]
logger = logging.getLogger("modpack.jobs")

def _installed(last: Optional[Job]) -> Optional[int]:
    """The build of a server's last real update (`last_real_update`) if it ended done; None otherwise."""
    if last is None or last.kind != "server_update" or last.status != "done":
        return None
    build = last.payload.get("build")
    return build if isinstance(build, int) else None

def done_rollback(rollbacks: list[Job], mode: str) -> Optional[Job]:
    """The newest rollback of this mode that ended done: a failed one closes nothing (D1 of
    d-7985ee-5445a0). UpdateService.enqueue_rollback and JobService.rollback_state share it."""
    return next((r for r in rollbacks if r.status == "done" and r.payload.get("mode") == mode), None)

class RetryLater(Exception):
    """A handler asks for its job to be retried after `wait` (a 429's Retry-After, say)."""
    def __init__(self, wait: timedelta, message: str) -> None:
        super().__init__(message)
        self.wait = wait

class JobFailed(Exception):
    """A handler ends its job failed, without another attempt: retrying would not help, or would
    act again on a real server that a person has to look at first."""

class LeaseLost(Exception):
    """The job was claimed by another worker: this one must stop without writing."""

class JobContext:
    """What a handler uses to run its steps once each, across retries, and to say how far it is."""
    def __init__(self, job: Job, jobs: JobDatabase, owner: str,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.job = job
        self.jobs = jobs
        self.owner = owner
        self.clock = clock

    async def progress(self, label: str, done: int, total: int) -> None:
        """What the job processes now and how far it is (E8): the panel shows it as it changes."""
        await self.jobs.progress(self.job.id, self.owner, self.clock(), JobProgress(label=label, done=done, total=total))

    async def waiting(self, seconds: float) -> None:
        """The job waits its turn for `seconds` (an external API's limit)."""
        now = self.clock()
        await self.jobs.waiting(self.job.id, self.owner, now, now + timedelta(seconds=seconds))

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

class WorkerBeat:
    """Whether this process's job worker is alive: it beats on every loop and while a job runs,
    and /health/ready fails when the last beat is older than `max_age` or the task ended."""
    def __init__(self, clock: Callable[[], float] = time.monotonic, max_age: float = 120.0) -> None:
        self._clock = clock
        self._max_age = max_age
        self._expected = False
        self._stopped = False
        self._last = clock()

    def expect(self) -> None:
        """This process runs a worker."""
        self._expected = True
        self._last = self._clock()

    def beat(self) -> None:
        self._last = self._clock()

    def stop(self) -> None:
        """The worker task ended."""
        self._stopped = True

    def healthy(self) -> bool:
        if not self._expected:
            return True
        return not self._stopped and self._clock() - self._last <= self._max_age

class JobWorker:
    """Claims and runs jobs. Several workers, in one process or many, share the queue safely."""
    def __init__(self, jobs: JobDatabase, handlers: dict[str, Handler], owner: Optional[str] = None,
                 clock: Clock = utcnow, lease: timedelta = timedelta(seconds=60),
                 heartbeat_every: float = 20.0, beat: Optional[WorkerBeat] = None) -> None:
        self.jobs = jobs
        self.handlers = handlers
        self.owner = owner or f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        self.clock = clock
        self.lease = lease
        self.heartbeat_every = heartbeat_every
        self.beat = beat

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
        context = JobContext(job, self.jobs, self.owner, self.clock)
        waits: set[asyncio.Task[None]] = set()

        def on_wait(seconds: float) -> None:
            task = asyncio.ensure_future(context.waiting(seconds))
            waits.add(task)
            task.add_done_callback(waits.discard)

        # A request this job sends that has to wait its turn says so on the job (E5, E8).
        token = ON_WAIT.set(on_wait)
        try:
            await handler(job, context)
            await self.jobs.finish(job.id, self.owner, self.clock(), error=None)
        except LeaseLost:
            logger.warning("lease lost", extra={"job_id": str(job.id), "kind": job.kind})
        except JobFailed as failed:
            logger.warning("job failed for good", extra={"job_id": str(job.id), "kind": job.kind, "reason": str(failed)})
            await self.jobs.finish(job.id, self.owner, self.clock(), error=str(failed))
        except RetryLater as later:
            if job.attempts >= job.max_attempts:
                await self.jobs.finish(job.id, self.owner, self.clock(), error=str(later))
            else:
                await self.jobs.release(job.id, self.owner, error=str(later), now=self.clock(),
                                        attempts=job.attempts, wait=later.wait)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            logger.exception("job failed", extra={"job_id": str(job.id), "kind": job.kind, "attempt": job.attempts})
            if job.attempts >= job.max_attempts:
                await self.jobs.finish(job.id, self.owner, self.clock(), error=message)
            else:
                await self.jobs.release(job.id, self.owner, error=message, now=self.clock(), attempts=job.attempts)
        finally:
            ON_WAIT.reset(token)
            beat.cancel()
        return True

    async def _heartbeat(self, job: Job) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_every)
            if self.beat is not None:
                self.beat.beat()
            if not await self.jobs.heartbeat(job.id, self.owner, self.clock(), self.lease):
                return

    async def run_forever(self, stop: asyncio.Event, idle: float = 5.0) -> None:
        """Run jobs until `stop` is set, waiting `idle` seconds when the queue is empty."""
        while not stop.is_set():
            if self.beat is not None:
                self.beat.beat()
            try:
                if await self.run_once():
                    continue
            except Exception:
                logger.exception("worker loop error")
            try:
                await asyncio.wait_for(stop.wait(), idle)
            except asyncio.TimeoutError:
                pass

@runtime_checkable
class NotificationSink(Protocol):
    """What JobService needs from the notifier (src/services/notify.py), which itself builds on
    this module: a protocol, not an import, keeps the two from importing each other."""
    async def notify(self, text: str) -> None: ...
    def handlers(self) -> dict[str, Handler]: ...

class JobService():
    """Queues jobs and holds the handlers the worker runs. Today only reads: scanning a server.
    Every job about a server uses the key server:{id}, so a scan and a future update of the same
    server never run at once."""
    def __init__(self,
            jobDB: JobDatabase,
            pterodactylService: PterodactylService,
            pterodactylDB: PterodactylDatabase,
            audit: AuditSink,
            notifier: Optional[NotificationSink] = None,
            ) -> None:
        self.jobDB = jobDB
        self.pterodactylService = pterodactylService
        self.pterodactylDB = pterodactylDB
        self.audit = audit
        self.notifier = notifier

    async def enqueue_scan(self, server_id: str, actor: Optional[Actor], full: bool = False,
                           directory: str = SCAN_DIRECTORY, full_reason: Optional[str] = None) -> Job:
        """Queue a scan of a server (of its mods, or of `directory`); 409 if the server already has
        an active job. `actor` is None for the scheduler, which is not recorded. `full_reason` is why
        a full scan was asked for (`periodic` for the scheduler's; the scan says `forced` without it)."""
        payload: dict[str, Any] = {"server_id": server_id, "full": full}
        if full and full_reason is not None:
            payload["full_reason"] = full_reason
        details: dict[str, Any] = {"full": full}
        if directory != SCAN_DIRECTORY:
            payload["directory"] = details["directory"] = directory
        job = await self.jobDB.enqueue("server_scan", key=f"server:{server_id}", payload=payload,
                                       requested_by=actor.ref() if actor is not None else None)
        if actor is not None:
            await self.audit.record(actor, "server.scan.queue", AuditTarget(kind="server", id=server_id), details)
        return job

    async def get(self, job_id: str) -> Job:
        return await self.jobDB.get(job_id)

    async def active_for_server(self, server_id: str) -> Optional[Job]:
        """The server's active job, if any (scans, updates and rollbacks share its key)."""
        return await self.jobDB.active_for(f"server:{server_id}")

    async def installed_build(self, server_id: str) -> Optional[int]:
        """The build the panel last installed on the server: that of its newest real update, if it
        ended done. None when the newest is not (it failed, is running, or was rolled back), or
        when the panel never updated it: what was installed by hand is not known."""
        return _installed(await self.jobDB.last_real_update(f"server:{server_id}"))

    async def update_state(self, server_id: str) -> UpdateState:
        """`installed_build` and `rollback_state` from one read of the server's last real update
        (review of batch B, minor 5): what its page shows."""
        last = await self.jobDB.last_real_update(f"server:{server_id}")
        return UpdateState(installed=_installed(last), rollback=await self._rollback_from(server_id, last))

    async def simulated_builds(self, server_ids: list[str], bundle_id: str) -> dict[str, int]:
        """`simulated_build` of each of these servers, in one query (review of batch B, minor 5): the
        pack's set-up asks it of every linked server. A server that never finished one is absent."""
        found = await self.jobDB.last_done_update_builds(server_ids, bundle_id)
        return {s: b for s, b in found.items() if isinstance(b, int)}

    async def simulated_build(self, server_id: str, bundle_id: str) -> Optional[int]:
        """The build of the server's newest finished update of this pack, a simulation or a real
        one: the set-up's last step (B1, d-7985ee-20935c). Only the pack's own: a server that
        changed pack does not count the other pack's build of the same number (final review of
        batch B). None when it never finished one."""
        return (await self.simulated_builds([server_id], bundle_id)).get(server_id)

    async def rollback_state(self, server_id: str) -> Optional[RollbackState]:
        """What the panel offers to undo the server's last real update, if it failed
        (d-7985ee-5445a0): None when there is nothing to offer. The newest rollback or real update
        of the server decides: a rollback points back to the update it undoes, and any later real
        update supersedes it (R17). After a done B nothing is left; after a done A only B. A
        failed rollback closes nothing. UpdateService.enqueue_rollback checks it all again."""
        return await self._rollback_from(server_id, await self.jobDB.last_real_update(f"server:{server_id}"))

    async def _rollback_from(self, server_id: str, update: Optional[Job]) -> Optional[RollbackState]:
        """`rollback_state` from the server's last real update or rollback, already read."""
        if update is not None and update.kind == "server_rollback":
            try:
                update = await self.jobDB.get(str(update.payload.get("update_id")))
            except HTTP_404_NOT_FOUND:
                return None
        if update is None or update.kind != "server_update" or update.status != "failed" \
                or update.payload.get("server_id") != server_id or not is_real_update(update):
            return None
        rollbacks = await self.jobDB.rollbacks_of(str(update.id))
        if done_rollback(rollbacks, "B") is not None:
            return None
        reverted_with_a = done_rollback(rollbacks, "A")
        allowed: list[Literal["A", "B"]] = []
        if "swap" in update.checkpoint and reverted_with_a is None:
            allowed.append("A")
        if "backup" in update.checkpoint:
            allowed.append("B")
        if not allowed:
            return None
        failed = [r for r in rollbacks if r.status == "failed"]
        failed_b = next((r for r in failed if r.payload.get("mode") == "B"), None)
        return RollbackState(update=update, allowed=allowed, reverted_with_a=reverted_with_a, failed_b=failed_b,
                             last_failed=failed[0] if failed else None)

    async def recent(self, limit: int = 10) -> list[Job]:
        """The newest jobs first, whatever their status."""
        return await self.jobDB.recent(limit)

    async def find(self, *, server_id: Optional[str], kind: Optional[str], status: Optional[str],
                   before_id: Optional[str], limit: int, bundle_id: Optional[str] = None) -> list[Job]:
        """Jobs newest first, by exact filters (a server, a pack: O9 D13), a page after the job `before_id`."""
        return await self.jobDB.find(server_id=server_id, bundle_id=bundle_id, kind=kind, status=status,
                                     before_id=before_id, limit=limit)

    async def schedule_scans(self, now: datetime, every: timedelta, full_every: timedelta) -> list[Job]:
        """
        Queue a scan of every server whose last scan is older than `every`, full if its last full
        scan is older than `full_every`: restoring a backup keeps old modification times, which
        the cache cannot see (derived-copy-goes-stale-silently). A server with an active job is
        skipped; the per-server index keeps several schedulers from queuing it twice.

        :return: The jobs queued.
        """
        queued = []
        for server in await self.pterodactylDB.server_find().to_list():
            server_id = str(server.id)
            scans = await self.pterodactylDB.scan_find(server_id)
            if scans and as_utc(scans[0].scanned_at) > now - every:
                continue
            last_full = next((s for s in scans if s.full), None)
            full = last_full is None or as_utc(last_full.scanned_at) <= now - full_every
            try:
                queued.append(await self.enqueue_scan(server_id, actor=None, full=full,
                                                      full_reason="periodic" if full else None))
            except HTTP_409_CONFLICT:
                continue
        return queued

    async def run_scheduler(self, stop: asyncio.Event, every: timedelta, full_every: timedelta,
                            check: float = 300.0) -> None:
        """Call schedule_scans every `check` seconds until `stop` is set."""
        while not stop.is_set():
            try:
                await self.schedule_scans(utcnow(), every, full_every)
            except Exception:
                logger.exception("scheduler error")
            try:
                await asyncio.wait_for(stop.wait(), check)
            except asyncio.TimeoutError:
                pass

    async def active_for_user(self, actor: Actor) -> list[Job]:
        """The queued or running jobs this person asked for: the bar shows them (E8)."""
        return await self.jobDB.active_for_requester(actor.iss, actor.sub)

    def handlers(self) -> dict[str, Handler]:
        handlers: dict[str, Handler] = {"server_scan": self._server_scan}
        if self.notifier is not None:
            handlers.update(self.notifier.handlers())
        return handlers

    async def _server_scan(self, job: Job, context: JobContext) -> None:
        server_id = job.payload["server_id"]
        directory = str(job.payload.get("directory") or SCAN_DIRECTORY)

        async def scan() -> dict[str, Any]:
            result = await self.pterodactylService.scan(server_id, actor=None, full=bool(job.payload.get("full")),
                                                        report=context.progress, directory=directory,
                                                        full_reason=job.payload.get("full_reason"))
            return {"scan_id": str(result.id), "files": len(result.files)}
        await context.step("scan", scan)
        if directory != SCAN_DIRECTORY:
            return  # only the mods compare with the server's build

        async def drift() -> dict[str, Any]:
            """Announce a change in how the server compares with its build: drifting, a new
            difference, or back in sync. The same state is not announced twice."""
            message = await self.pterodactylService.drift(server_id)
            if message is None:
                return {"notified": False}
            previous = await self.pterodactylDB.scan_previous_drift(server_id)
            await self.pterodactylDB.scan_set_drift(server_id, message)
            # A first scan that matches is not news; anything else that changed is.
            announce = message != previous and not (previous is None and message.startswith("✅"))
            if announce and self.notifier is not None:
                await self.notifier.notify(message)
            return {"notified": announce}
        await context.step("drift", drift)
