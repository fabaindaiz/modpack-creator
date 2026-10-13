from beanie import Document
from datetime import datetime
from pydantic import BaseModel, Field
from pymongo import DESCENDING, IndexModel
from typing import Any, Optional
from src.model.users import ActorRef

class AuditTarget(BaseModel):
    """What an action was done to: a kind (server, bundle, ...), its id and, when known, the name it
    had then (A10): a renamed or deleted target still reads by that name."""
    kind: str
    id: str
    name: Optional[str] = None

class AuditEvent(Document):
    """One thing someone did, written once and never changed."""
    at: datetime
    actor: ActorRef
    action: str
    target: AuditTarget
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None

    class Settings:
        # The page's leading match and sort (A11): newest first, within its dates. Its filters live
        # inside `$facet`, which uses no index, so an index on a filter field would only cost on
        # every write (final review of batch A, 2026-10-13).
        indexes = [
            IndexModel([("at", DESCENDING), ("_id", DESCENDING)]),
        ]

class Facet(BaseModel):
    """One option of an audit filter with how many events it has: a value (a sub, an action,
    `kind:id`), what it reads as (a name; the value when there is none) and its count."""
    value: str
    label: str
    count: int

class AuditPage(BaseModel):
    """A page of events and, for each filter, its options counted with the other filters (A12)."""
    events: list[AuditEvent]
    actors: list[Facet] = Field(default_factory=list)
    actions: list[Facet] = Field(default_factory=list)
    targets: list[Facet] = Field(default_factory=list)
