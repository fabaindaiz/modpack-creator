from beanie import PydanticObjectId, SortDirection
from typing import Any, Iterable, Optional
from src.model.modpack import UpdateCheck

class UpdateCheckDatabase():
    async def insert(self, check: UpdateCheck) -> UpdateCheck:
        return await UpdateCheck.insert(check)

    async def latest(self, bundle_id: PydanticObjectId) -> Optional[UpdateCheck]:
        """The newest check of a modpack, failed or not."""
        return await UpdateCheck.find(UpdateCheck.bundle_id == bundle_id) \
            .sort(("checked_at", SortDirection.DESCENDING), ("_id", SortDirection.DESCENDING)).first_or_none()

    async def last_notified(self, bundle_id: PydanticObjectId) -> Optional[UpdateCheck]:
        """The newest check that sent a notification: its digest is what was last announced."""
        return await UpdateCheck.find({"bundle_id": bundle_id, "notified_digest": {"$ne": None}}) \
            .sort(("checked_at", SortDirection.DESCENDING), ("_id", SortDirection.DESCENDING)).first_or_none()

    async def latest_many(self, bundle_ids: Iterable[PydanticObjectId]) -> dict[PydanticObjectId, UpdateCheck]:
        """The newest check of each modpack, by id, in one query; one never checked is left out."""
        ids = list(bundle_ids)
        if not ids:
            return {}
        # Index order, and only the first check of each modpack leaves MongoDB.
        pipeline: list[dict[str, Any]] = [{"$match": {"bundle_id": {"$in": ids}}},
                    {"$sort": {"bundle_id": 1, "checked_at": -1, "_id": -1}},
                    {"$group": {"_id": "$bundle_id", "doc": {"$first": "$$ROOT"}}}]
        cursor = await UpdateCheck.get_pymongo_collection().aggregate(pipeline)
        return {row["_id"]: UpdateCheck.model_validate(row["doc"]) async for row in cursor}
