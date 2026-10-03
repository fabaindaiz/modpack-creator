from datetime import datetime
from typing import Any, Optional
from beanie import PydanticObjectId, SortDirection
from src.exceptions import *
from src.model.audit import AuditEvent

class AuditDatabase():
    async def insert(self, event: AuditEvent) -> AuditEvent:
        """Store an event."""
        return await AuditEvent.insert(event)

    async def find(self, *,
            actor_sub: Optional[str],
            action: Optional[str],
            target_id: Optional[str],
            before_id: Optional[str],
            limit: int,
            since: Optional[datetime] = None,
            until: Optional[datetime] = None,
            ) -> list[AuditEvent]:
        """Events newest first (then by id), filtered by exact values (equality only, never a pattern)."""
        query: dict[str, Any] = {}
        if actor_sub is not None:
            query["actor.sub"] = {"$eq": actor_sub}
        if action is not None:
            query["action"] = {"$eq": action}
        if target_id is not None:
            query["target.id"] = {"$eq": target_id}
        clauses: list[dict[str, Any]] = [query] if query else []
        if since is not None or until is not None:
            # `since` inclusive, `until` exclusive; both aware UTC. PyMongo stores and compares a naive
            # datetime as if it were UTC, so a naive local time would shift the window silently.
            window: dict[str, datetime] = {}
            if since is not None:
                window["$gte"] = since
            if until is not None:
                window["$lt"] = until
            clauses.append({"at": window})
        if before_id is not None:
            if not PydanticObjectId.is_valid(before_id):
                raise HTTP_404_NOT_FOUND(f"Audit event '{before_id}' not found")
            oid = PydanticObjectId(before_id)
            cursor = await AuditEvent.get(oid)
            if cursor is None:
                raise HTTP_404_NOT_FOUND(f"Audit event '{before_id}' not found")
            # The cursor is the last event shown: `at` alone ties within a millisecond.
            clauses.append({"$or": [{"at": {"$lt": cursor.at}}, {"at": cursor.at, "_id": {"$lt": oid}}]})
        return await AuditEvent.find({"$and": clauses} if clauses else {}) \
            .sort(("at", SortDirection.DESCENDING), ("_id", SortDirection.DESCENDING)).limit(limit).to_list()
