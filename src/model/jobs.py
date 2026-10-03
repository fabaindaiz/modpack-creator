from beanie import Document
from datetime import datetime
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, IndexModel
from typing import Any, Literal, Optional
from src.model.users.actor import ActorRef

class Job(Document):
    """A durable job (i-7985ee-0ec295): claimed atomically by one worker at a time under a lease,
    with a checkpoint per step. Delivery is at least once: a step that already ran is in
    `checkpoint`, and a retry sees it."""
    kind: str
    # At most one active job per key (a server, say): enforced by a unique partial index, not a
    # lock in memory, so it holds across processes (in-process-guarantees).
    key: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)

    status: str = "queued"  # queued, running, done or failed
    # True while queued or running: the partial index filters on it. A boolean and not
    # `status $in [...]`, which partial indexes accept only from MongoDB 6.0.
    active: bool = True
    attempts: int = 0
    max_attempts: int = 3
    owner: Optional[str] = None
    lease_until: Optional[datetime] = None
    step: Optional[str] = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: datetime
    # Not claimed before this time: a failed attempt waits before the next (JobDatabase.release).
    available_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # Who asked for it; None for the worker's own jobs and for jobs stored before this field.
    requested_by: Optional[ActorRef] = None

    class Settings:
        indexes = [
            IndexModel([("key", ASCENDING)], unique=True, name="one_active_job_per_key",
                       partialFilterExpression={"active": True, "key": {"$type": "string"}}),
            IndexModel([("status", ASCENDING), ("created_at", ASCENDING)]),
            # The panel's list of recent jobs.
            IndexModel([("created_at", DESCENDING)]),
            # The newest job of a key: JobDatabase.last_real_update, which a rollback checks.
            IndexModel([("key", ASCENDING), ("created_at", DESCENDING)]),
            # The rollbacks of one update: JobDatabase.rollbacks_of. Partial: only rollbacks carry it.
            IndexModel([("payload.update_id", ASCENDING), ("created_at", DESCENDING)], name="rollbacks_of_an_update",
                       partialFilterExpression={"kind": "server_rollback"}),
        ]

class RollbackState(BaseModel):
    """What the panel can still offer to undo a failed update (d-7985ee-5445a0): the modes
    allowed, the done rollback A, if one reverted it (then only B is left), the newest B that
    failed and the newest attempt of either mode that failed, so the page can say that an attempt
    already did not work."""
    update: Job
    allowed: list[Literal["A", "B"]]
    reverted_with_a: Optional[Job] = None
    failed_b: Optional[Job] = None
    last_failed: Optional[Job] = None
