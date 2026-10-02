from beanie import Document
from datetime import datetime
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, IndexModel
from typing import Any, Optional
from src.model.users import ActorRef

class AuditTarget(BaseModel):
    """What an action was done to: a kind (server, bundle, ...) and its id."""
    kind: str
    id: str

class AuditEvent(Document):
    """One thing someone did, written once and never changed."""
    at: datetime
    actor: ActorRef
    action: str
    target: AuditTarget
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None

    class Settings:
        indexes = [
            IndexModel([("at", DESCENDING)]),
            IndexModel([("target.kind", ASCENDING), ("target.id", ASCENDING)]),
            IndexModel([("actor.sub", ASCENDING)]),
        ]
