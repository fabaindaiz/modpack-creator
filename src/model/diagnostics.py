from beanie import Document
from datetime import datetime
from pymongo import ASCENDING, IndexModel
from typing import Literal, Optional
from src.library.diagnostics import RETENTION_DAYS

class DiagnosticEvent(Document):
    """One request, unhandled error or job run (O7). Flat: the fields of the other kinds stay unset.
    SCHEMA in `src/library/diagnostics.py` says what each field means; the downloaded file repeats it."""
    at: datetime
    kind: Literal["request", "error", "job"]
    request_id: Optional[str] = None
    user: Optional[str] = None
    # request
    method: Optional[str] = None
    route: Optional[str] = None
    status: Optional[int] = None
    duration_ms: Optional[float] = None
    # error
    source: Optional[str] = None
    logger: Optional[str] = None
    error_type: Optional[str] = None
    message: Optional[str] = None
    traceback: Optional[str] = None
    # job
    job_kind: Optional[str] = None
    job_id: Optional[str] = None
    attempt: Optional[int] = None
    outcome: Optional[str] = None

    class Settings:
        # TTL: MongoDB's monitor deletes an event RETENTION_DAYS after its `at` (a BSON date), every
        # 60 s. A single-field index (a compound one ignores expireAfterSeconds); it also serves the
        # export's range and order. Changing the days later is a `collMod`, not a new createIndex.
        indexes = [
            IndexModel([("at", ASCENDING)], name="at_ttl", expireAfterSeconds=RETENTION_DAYS * 24 * 3600),
        ]
