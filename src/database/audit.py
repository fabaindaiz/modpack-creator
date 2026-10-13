from datetime import datetime
from typing import Any, Optional
from beanie import PydanticObjectId
from src.exceptions import *
from src.model.audit import AuditEvent, AuditPage, Facet

# The most targets the filter offers (the busiest); people and actions are few, all are offered.
TARGET_FACET_LIMIT = 200

class AuditDatabase():
    async def insert(self, event: AuditEvent) -> AuditEvent:
        """Store an event."""
        return await AuditEvent.insert(event)

    async def page(self, *,
            actor_sub: Optional[str],
            action: Optional[str],
            target: Optional[tuple[str, str]],
            before_id: Optional[str],
            limit: int,
            target_name: Optional[str] = None,
            since: Optional[datetime] = None,
            until: Optional[datetime] = None,
            ) -> AuditPage:
        """
        A page of events newest first (then by id) and each filter's options with their counts, in
        one aggregation (A11, A12): the date window bounds everything; each count applies the other
        two filters and not its own; the cursor (`before_id`, the last event shown) pages the
        events only. Filters are exact values (equality only, never a pattern); `target` is
        (kind, id), `target_name` the name the event kept.
        """
        actor_match: dict[str, Any] = {} if actor_sub is None else {"actor.sub": {"$eq": actor_sub}}
        action_match: dict[str, Any] = {} if action is None else {"action": {"$eq": action}}
        target_match: dict[str, Any] = {}
        if target is not None:
            target_match = {"target.kind": {"$eq": target[0]}, "target.id": {"$eq": target[1]}}
        elif target_name is not None:
            target_match = {"target.name": {"$eq": target_name}}
        # `since` inclusive, `until` exclusive; both aware UTC. PyMongo stores and compares a naive
        # datetime as if it were UTC, so a naive local time would shift the window silently.
        window: dict[str, datetime] = {}
        if since is not None:
            window["$gte"] = since
        if until is not None:
            window["$lt"] = until
        page_match = {**actor_match, **action_match, **target_match}
        if before_id is not None:
            if not PydanticObjectId.is_valid(before_id):
                raise HTTP_404_NOT_FOUND(f"Audit event '{before_id}' not found")
            oid = PydanticObjectId(before_id)
            last = await AuditEvent.get(oid)
            if last is None:
                raise HTTP_404_NOT_FOUND(f"Audit event '{before_id}' not found")
            # The cursor is the last event shown: `at` alone ties within a millisecond.
            page_match["$or"] = [{"at": {"$lt": last.at}}, {"at": last.at, "_id": {"$lt": oid}}]
        by_count = {"$sort": {"count": -1, "_id": 1}}
        pipeline: list[dict[str, Any]] = [
            {"$match": {"at": window} if window else {}},
            {"$sort": {"at": -1, "_id": -1}},  # newest first: `$first` is each group's latest name
            {"$facet": {
                "events": [{"$match": page_match}, {"$limit": limit}],
                "actors": [{"$match": {**action_match, **target_match}},
                           {"$group": {"_id": "$actor.sub", "label": {"$first": "$actor.name"}, "count": {"$sum": 1}}},
                           by_count],
                "actions": [{"$match": {**actor_match, **target_match}},
                            {"$group": {"_id": "$action", "count": {"$sum": 1}}},
                            by_count],
                "targets": [{"$match": {**actor_match, **action_match}},
                            {"$group": {"_id": {"kind": "$target.kind", "id": "$target.id"},
                                        "label": {"$first": "$target.name"}, "count": {"$sum": 1}}},
                            by_count, {"$limit": TARGET_FACET_LIMIT}],
            }},
        ]
        cursor = await AuditEvent.get_pymongo_collection().aggregate(pipeline)
        rows = [row async for row in cursor]
        found = rows[0] if rows else {}
        return AuditPage(
            events=[AuditEvent.model_validate(doc) for doc in found.get("events", [])],
            actors=[Facet(value=row["_id"], label=row.get("label") or row["_id"], count=row["count"])
                    for row in found.get("actors", [])],
            actions=[Facet(value=row["_id"], label=row["_id"], count=row["count"]) for row in found.get("actions", [])],
            targets=[Facet(value=f"{row['_id']['kind']}:{row['_id']['id']}",
                           label=row.get("label") or f"{row['_id']['kind']}:{row['_id']['id']}", count=row["count"])
                     for row in found.get("targets", [])],
        )
