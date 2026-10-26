import re
from beanie import PydanticObjectId, SortDirection
from datetime import datetime, timedelta, timezone
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from typing import Any, Optional
from src.model.jobs import Job, JobProgress
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

    async def active_for_keys(self, keys: list[str]) -> list[Job]:
        """The active jobs holding these keys, oldest first, for a banner: without what it never shows."""
        return await self._polled({"key": {"$in": keys}, "active": True})

    async def active_for_pack(self, keys: list[str], bundle_id: str) -> list[Job]:
        """`active_for_keys` and the imports into this pack, which hold their person's key (O9
        user-walk, A3)."""
        return await self._polled({"active": True, "$or": [{"key": {"$in": keys}},
                                                           {"kind": "modpack_import", "payload.into": bundle_id}]})

    async def active_for_requester(self, iss: str, sub: str) -> list[Job]:
        """The active jobs this person asked for, oldest first, for the bar."""
        return await self._polled({"active": True, "requested_by.iss": iss, "requested_by.sub": sub})

    async def active_of_kind(self, kind: str) -> list[Job]:
        """The queued or running jobs of this kind, oldest first, without their read content."""
        return await self._polled({"kind": kind, "active": True})

    @staticmethod
    async def _polled(query: dict[str, Any]) -> list[Job]:
        """Jobs for a page that polls every few seconds: without the read file an import carries
        (`payload.content`) nor the checkpoints (code review, 2026-10-09)."""
        cursor = Job.get_pymongo_collection().find(query, {"payload.content": 0, "checkpoint": 0}).sort(
            [("created_at", 1), ("_id", 1)])  # two jobs of the same millisecond, in the order they came
        return [Job.model_validate(document) for document in await cursor.to_list(None)]

    async def last_of_kind(self, kind: str, key: str) -> Optional[Job]:
        """The newest job of this kind and key that ended well."""
        return await Job.find(Job.kind == kind, Job.key == key, Job.status == "done").sort(
            [("created_at", SortDirection.DESCENDING), ("_id", SortDirection.DESCENDING)]).first_or_none()

    async def last_for_key(self, key: str) -> Optional[Job]:
        """The newest job of this key, active or not."""
        return await Job.find(Job.key == key).sort([("created_at", SortDirection.DESCENDING), ("_id", SortDirection.DESCENDING)]).first_or_none()

    async def force_if_queued(self, job_id: Any) -> bool:
        """Set `payload.force` on a job still queued; False if it is already running or done."""
        result = await Job.get_pymongo_collection().update_one(
            {"_id": job_id, "status": "queued"}, {"$set": {"payload.force": True}})
        return result.modified_count == 1

    async def candidates_add_if_queued(self, job_id: Any, candidates: list[str]) -> bool:
        """Add to `payload.candidates` of a job still queued; False if it is already running or done."""
        result = await Job.get_pymongo_collection().update_one(
            {"_id": job_id, "status": "queued"}, {"$addToSet": {"payload.candidates": {"$each": candidates}}})
        return result.matched_count == 1

    async def active_for(self, key: str) -> Optional[Job]:
        """The active job holding this key, if any."""
        return await Job.find_one(Job.key == key, Job.active == True)  # noqa: E712

    async def last_real_update(self, key: str) -> Optional[Job]:
        """The newest rollback, or real update, of this key (d-7985ee-5445a0). Any real update
        counts, written or not: one refused in preflight still supersedes an earlier one (R17).
        A dry run never does, nor does an update without `dry_run`: they wrote nothing. A rollback names the update it undoes in
        `payload.update_id`: whoever reads it follows it back there."""
        real = {"kind": "server_update", "payload.dry_run": False}
        return await Job.find({"key": key, "$or": [{"kind": "server_rollback"}, real]}
                              ).sort([("created_at", SortDirection.DESCENDING), ("_id", SortDirection.DESCENDING)]).first_or_none()

    async def last_done_update_builds(self, server_ids: list[str], bundle_id: str) -> dict[str, Any]:
        """The build of each server's newest finished update (a simulation or a real one) of this
        pack, by server id, in one query; a server without one is absent."""
        if not server_ids:
            return {}
        rows = await Job.get_pymongo_collection().aggregate([
            {"$match": {"kind": "server_update", "status": "done", "payload.bundle_id": bundle_id,
                        "key": {"$in": [f"server:{s}" for s in server_ids]}}},
            {"$sort": {"created_at": -1, "_id": -1}},
            {"$group": {"_id": "$key", "build": {"$first": "$payload.build"}}},
        ])
        return {row["_id"][len("server:"):]: row["build"] async for row in rows}

    async def rollbacks_of(self, update_id: str) -> list[Job]:
        """The rollbacks of an update, whatever their status, the newest first."""
        return await Job.find({"kind": "server_rollback", "payload.update_id": update_id}
                              ).sort([("created_at", SortDirection.DESCENDING), ("_id", SortDirection.DESCENDING)]).to_list()

    async def recent(self, limit: int) -> list[Job]:
        """The newest jobs, whatever their status."""
        return await Job.find().sort(("created_at", SortDirection.DESCENDING)).limit(limit).to_list()

    async def find(self, *,
            server_id: Optional[str],
            kind: Optional[str],
            bundle_id: Optional[str] = None,
            status: Optional[str],
            before_id: Optional[str],
            limit: int,
            ) -> list[Job]:
        """Jobs newest first (then by id), filtered by exact values (equality only, never a
        pattern); `before_id` is the last job of the previous page; `limit` is kept within 1..200."""
        query: dict[str, Any] = {}
        if server_id is not None:
            query["key"] = {"$eq": f"server:{server_id}"}
        if bundle_id is not None:
            # A pack's jobs name it in their payload (O9 D13): its catalog, builds, checks, imports.
            query["payload.bundle_id"] = {"$eq": bundle_id}
        if kind is not None:
            query["kind"] = {"$eq": kind}
        if status is not None:
            query["status"] = {"$eq": status}
        clauses: list[dict[str, Any]] = [query] if query else []
        if before_id is not None:
            cursor = await self.get(before_id)  # 404 when it is not an id or not a job
            clauses.append({"$or": [{"created_at": {"$lt": cursor.created_at}},
                                    {"created_at": cursor.created_at, "_id": {"$lt": cursor.id}}]})
        return await Job.find({"$and": clauses} if clauses else {}) \
            .sort(("created_at", SortDirection.DESCENDING), ("_id", SortDirection.DESCENDING)) \
            .limit(max(1, min(limit, 200))).to_list()

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
        return await self._owned(job_id, owner, {"$set": {"lease_until": now + lease, "updated_at": now}})

    async def progress(self, job_id: Any, owner: str, now: datetime, progress: JobProgress) -> bool:
        """What the job processes and how far it is; it is no longer waiting."""
        return await self._owned(job_id, owner, {"$set": {"progress": progress.model_dump(), "waiting_until": None,
                                                          "updated_at": now}})

    async def waiting(self, job_id: Any, owner: str, now: datetime, until: datetime) -> bool:
        """The job waits its turn until `until`."""
        return await self._owned(job_id, owner, {"$set": {"waiting_until": until, "updated_at": now}})

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
