from datetime import datetime
from typing import Any, Callable, Optional, Protocol, runtime_checkable
from src.database.audit import AuditDatabase
from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.library.logging import request_id
from src.model.audit import AuditEvent, AuditTarget
from src.model.users import Actor
from src.library.utils import utcnow

SECRET_KEYS = ("token", "secret", "password", "authorization")
MAX_LIMIT = 200

def _check_details(details: Any) -> None:
    """Refuse a key that looks like a secret, at any depth: the log is read by many people."""
    if isinstance(details, dict):
        for key, value in details.items():
            if any(word in str(key).lower() for word in SECRET_KEYS):
                raise ValueError(f"audit details must not hold '{key}'")
            _check_details(value)
    elif isinstance(details, (list, tuple)):
        for item in details:
            _check_details(item)

@runtime_checkable
class AuditSink(Protocol):
    """Where a service records what an actor asked for: AuditService, or a test double."""
    async def record(self, actor: Actor, action: str, target: AuditTarget,
                     details: Optional[dict[str, Any]] = None) -> Any: ...

class TargetNames():
    """The name of an event's target, looked up when the event is written (A10)."""
    def __init__(self,
            modpackDB: ModpackDatabase,
            pterodactylDB: PterodactylDatabase,
            modrinthDB: ModrinthDatabase,
            ) -> None:
        self.modpackDB = modpackDB
        self.pterodactylDB = pterodactylDB
        self.modrinthDB = modrinthDB

    async def name(self, target: AuditTarget) -> Optional[str]:
        return None

class AuditService():
    def __init__(self,
            auditDB: AuditDatabase,
            names: Optional[TargetNames] = None,
            clock: Callable[[], datetime] = utcnow,
            ) -> None:
        self.auditDB = auditDB
        self.names = names
        self.clock = clock

    async def record(self, actor: Actor, action: str, target: AuditTarget, details: Optional[dict[str, Any]] = None) -> AuditEvent:
        """Write who did what to what. The request id comes from the log's context."""
        details = dict(details or {})
        _check_details(details)
        return await self.auditDB.insert(AuditEvent(
            at=self.clock(), actor=actor.ref(), action=action, target=target,
            details=details, request_id=request_id()))

    async def find(self, *,
            actor_sub: Optional[str] = None,
            action: Optional[str] = None,
            target_id: Optional[str] = None,
            before_id: Optional[str] = None,
            limit: int = 50,
            since: Optional[datetime] = None,
            until: Optional[datetime] = None,
            ) -> list[AuditEvent]:
        """Events newest first; `before_id` is the id of the last event of the previous page; `limit` is held to 1..200;
        `since` (inclusive) and `until` (exclusive) are aware UTC datetimes."""
        return await self.auditDB.find(
            actor_sub=actor_sub, action=action, target_id=target_id, before_id=before_id,
            limit=max(1, min(limit, MAX_LIMIT)), since=since, until=until)
