import re
from beanie import PydanticObjectId, SortDirection
from datetime import datetime, timedelta, timezone
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from typing import Any, Optional
from src.model.jobs import Job
from src.model.users.actor import ActorRef
from src.exceptions import *

# After a failed attempt, the next one waits RETRY_BASE * 2**(attempts - 1), at most RETRY_MAX:
# a panel that is down is not hammered.
RETRY_BASE = timedelta(seconds=30)
RETRY_MAX = timedelta(minutes=10)
# A step name becomes a field of `checkpoint`: no dots or dollars.
STEP_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

class JobDatabase():
    """Every write after a claim is conditioned on still owning the job: a worker whose lease
    expired and was claimed by another can no longer heartbeat, checkpoint or finish it."""

    async def enqueue(self, kind: str, key: Optional[str], payload: dict[str, Any],
                      max_attempts: int = 3, now: Optional[datetime] = None,
                      requested_by: Optional[ActorRef] = None) -> Job:
        """Queue a job; 409 if its key already has an active one."""
        # available_at stays unset: a new job waits for nothing; only a retry sets it.
        job = Job(kind=kind, key=key, payload=payload, max_attempts=max_attempts,
                  created_at=now or datetime.now(timezone.utc), requested_by=requested_by)
        try:
            return await Job.insert(job)
        except DuplicateKeyError as error:
            raise HTTP_409_CONFLICT(f"There is already an active job for {key}") from error

    async def active_for(self, key: str) -> Optional[Job]:
        """The active job holding this key, if any."""
        return await Job.find_one(Job.key == key, Job.active == True)  # noqa: E712

    async def last_real_update(self, key: str) -> Optional[Job]:
        """The newest update or rollback job of this key that is not a dry run (which writes nothing)."""
        return await Job.find({"key": key, "kind": {"$in": ["server_update", "server_rollback"]},
                               "payload.dry_run": {"$ne": True}}
                              ).sort([("created_at", SortDirection.DESCENDING), ("_id", SortDirection.DESCENDING)]).first_or_none()

    async def recent(self, limit: int) -> list[Job]:
        """The newest jobs, whatever their status."""
        return await Job.find().sort(("created_at", SortDirection.DESCENDING)).limit(limit).to_list()

    async def get(self, job_id: str) -> Job:
        job = await Job.get(job_id) if PydanticObjectId.is_valid(job_id) else None
        if job is None:
            raise HTTP_404_NOT_FOUND(f"Job '{job_id}' not found")
        return job

    async def claim(self, owner: str, now: datetime, lease: timedelta) -> Optional[Job]:
        """
        Atomically take the oldest job that is queued, or running with an expired lease, with
        attempts left; None if there is none. Expired jobs out of attempts are failed first.
        """
        collection = Job.get_pymongo_collection()
        out_of_attempts = {"$expr": {"$gte": ["$attempts", "$max_attempts"]}}
        await collection.update_many(
            {"status": "running", "lease_until": {"$lt": now}, **out_of_attempts},
            {"$set": {"status": "failed", "active": False, "finished_at": now,
                      "error": "the lease expired on the last attempt"}})
        raw = await collection.find_one_and_update(
            {"$or": [{"status": "queued", "available_at": {"$lte": now}},
                     {"status": "queued", "available_at": None},
                     {"status": "running", "lease_until": {"$lt": now}}],
             "$expr": {"$lt": ["$attempts", "$max_attempts"]}},
            {"$set": {"status": "running", "owner": owner, "lease_until": now + lease},
             "$inc": {"attempts": 1}},
            sort=[("created_at", 1)], return_document=ReturnDocument.AFTER)
        return Job.model_validate(raw) if raw else None

    async def _owned(self, job_id: Any, owner: str, update: dict[str, Any]) -> bool:
        result = await Job.get_pymongo_collection().update_one(
            {"_id": job_id, "owner": owner, "status": "running"}, update)
        return result.modified_count == 1

    async def heartbeat(self, job_id: Any, owner: str, now: datetime, lease: timedelta) -> bool:
        """Extend the lease; False if the job is no longer this worker's."""
        return await self._owned(job_id, owner, {"$set": {"lease_until": now + lease}})

    async def checkpoint(self, job_id: Any, owner: str, step: str, data: dict[str, Any]) -> bool:
        """Record that a step ran and what it left; False if the job is no longer this worker's."""
        if not STEP_NAME.match(step):
            raise ValueError(f"Invalid step name {step!r}")
        return await self._owned(job_id, owner, {"$set": {"step": step, f"checkpoint.{step}": data}})

    async def finish(self, job_id: Any, owner: str, now: datetime, error: Optional[str]) -> bool:
        """Done, or failed with `error`; frees the job's key."""
        return await self._owned(job_id, owner, {"$set": {
            "status": "failed" if error else "done", "active": False, "finished_at": now, "error": error}})

    async def release(self, job_id: Any, owner: str, error: str, now: datetime, attempts: int,
                      wait: Optional[timedelta] = None) -> bool:
        """Back to the queue for another attempt, keeping its checkpoint and key: after `wait` if
        given, else after a wait that doubles each time."""
        wait = wait if wait is not None else min(RETRY_BASE * 2 ** max(attempts - 1, 0), RETRY_MAX)
        return await self._owned(job_id, owner, {"$set": {
            "status": "queued", "owner": None, "lease_until": None, "error": error, "available_at": now + wait}})
