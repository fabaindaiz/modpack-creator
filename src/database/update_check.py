from beanie import PydanticObjectId, SortDirection
from typing import Iterable, Optional
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
        checks = await UpdateCheck.find({"bundle_id": {"$in": ids}}) \
            .sort(("checked_at", SortDirection.DESCENDING), ("_id", SortDirection.DESCENDING)).to_list()
        found: dict[PydanticObjectId, UpdateCheck] = {}
        for check in checks:
            found.setdefault(check.bundle_id, check)
        return found
