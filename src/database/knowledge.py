from datetime import datetime
from typing import Any, Optional

from beanie import PydanticObjectId
from beanie.odm.enums import SortDirection
from pymongo.errors import DuplicateKeyError

from src.exceptions import *
from src.model.modpack.known_issue import KnownIssue


class KnowledgeDatabase:
    """The knowledge base: known issues between mods' versions (O8 tanda 3)."""

    @staticmethod
    def _oid(issue_id: str) -> PydanticObjectId:
        try:
            return PydanticObjectId(issue_id)
        except Exception:
            raise HTTP_404_NOT_FOUND(f"Issue '{issue_id}' not found") from None

    async def insert(self, issue: KnownIssue) -> KnownIssue:
        """Store an entry; one with the same `key` already stored is returned instead (a form sent twice)."""
        try:
            return await KnownIssue.insert(issue)
        except DuplicateKeyError:
            found = await KnownIssue.find_one(KnownIssue.key == issue.key)
            if found is None:
                raise
            return found

    async def get(self, issue_id: str) -> KnownIssue:
        found = await KnownIssue.get(self._oid(issue_id))
        if found is None:
            raise HTTP_404_NOT_FOUND(f"Issue '{issue_id}' not found")
        return found

    async def find_pair(self, a: str, b: Optional[str]) -> Optional[KnownIssue]:
        """The entry of this pair (or of `a` alone) that is not rejected, if there is one."""
        return await KnownIssue.find_one({"a.project_id": a, "b.project_id" if b else "b": b,
                                          "status": {"$ne": "rejected"}})

    async def update(self, issue_id: str, rev: int, fields: dict[str, Any],
                     add: Optional[dict[str, list[str]]] = None) -> bool:
        """Write these fields only while the entry is still the revision `rev` (user-walk K11), as the next one;
        `add` puts values in lists without repeating them. Whether it wrote."""
        update: dict[str, Any] = {"$set": {**fields, "rev": rev + 1}}
        if add:
            update["$addToSet"] = {key: {"$each": values} for key, values in add.items()}
        result = await KnownIssue.get_pymongo_collection().update_one({"_id": self._oid(issue_id), "rev": rev}, update)
        return result.modified_count == 1

    async def find(self, *, project: Optional[str] = None, projects: Optional[list[str]] = None,
                   status: Optional[list[str]] = None, game_version: Optional[str] = None,
                   before: Optional[datetime] = None, limit: int = 20) -> list[KnownIssue]:
        """Entries newest first: of a mod, or of any of `projects` (either side), in these states, seen on this
        Minecraft version (or on any)."""
        query: dict[str, Any] = {}
        ids = [project] if project is not None else projects
        if ids is not None:
            query["$or"] = [{"a.project_id": {"$in": ids}}, {"b.project_id": {"$in": ids}}]
        if status is not None:
            query["status"] = {"$in": status}
        if game_version is not None:
            query["game_version"] = {"$in": [game_version, None]}
        if before is not None:
            query["updated_at"] = {"$lt": before}
        return await KnownIssue.find(query).sort(("updated_at", SortDirection.DESCENDING),
                                                 ("_id", SortDirection.DESCENDING)).limit(limit).to_list()

    async def count(self, status: str) -> int:
        return await KnownIssue.find({"status": status}).count()
