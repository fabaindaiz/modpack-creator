from datetime import datetime, timezone
from typing import Any, Optional
from beanie import SortDirection
from src.model.audit import AuditEvent

class AuditDatabase():
    async def insert(self, event: AuditEvent) -> AuditEvent:
        """Store an event."""
        return await AuditEvent.insert(event)

    async def find(self, *,
            actor_sub: Optional[str],
            action: Optional[str],
            target_id: Optional[str],
            before: Optional[datetime],
            limit: int,
            ) -> list[AuditEvent]:
        """Events newest first, filtered by exact values (equality only, never a pattern)."""
        query: dict[str, Any] = {}
        if actor_sub is not None:
            query["actor.sub"] = {"$eq": actor_sub}
        if action is not None:
            query["action"] = {"$eq": action}
        if target_id is not None:
            query["target.id"] = {"$eq": target_id}
        if before is not None:
            # MongoDB gives datetimes back without a zone; they are UTC.
            query["at"] = {"$lt": before if before.tzinfo else before.replace(tzinfo=timezone.utc)}
        return await AuditEvent.find(query).sort(("at", SortDirection.DESCENDING)).limit(limit).to_list()
