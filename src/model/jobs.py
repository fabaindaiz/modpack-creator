from beanie import Document
from datetime import datetime
from pydantic import Field
from pymongo import ASCENDING, IndexModel
from typing import Any, Optional

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

    class Settings:
        indexes = [
            IndexModel([("key", ASCENDING)], unique=True, name="one_active_job_per_key",
                       partialFilterExpression={"active": True, "key": {"$type": "string"}}),
            IndexModel([("status", ASCENDING), ("created_at", ASCENDING)]),
        ]
