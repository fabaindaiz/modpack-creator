from datetime import datetime
from typing import Any, Optional

from beanie import PydanticObjectId
from beanie.odm.enums import SortDirection
from pymongo.errors import DuplicateKeyError

from src.exceptions import *
from src.model.modpack.known_issue import KnownIssue


class PairTaken(Exception):
    """The write would make a second entry of a pair that is not rejected (`LIVE_PAIR_INDEX`, review B2)."""


class KnowledgeDatabase:
    """The knowledge base: known issues between mods' versions (O8 tanda 3)."""

    @staticmethod
    def _oid(issue_id: str) -> PydanticObjectId:
        try:
            return PydanticObjectId(issue_id)
        except Exception:
            raise HTTP_404_NOT_FOUND(f"Issue '{issue_id}' not found") from None

    async def insert(self, issue: KnownIssue) -> KnownIssue:
        """Store an entry; one with the same `key` already stored is returned instead (a form sent twice). Another
        live entry of the pair is `PairTaken`."""
        try:
            return await KnownIssue.insert(issue)
        except DuplicateKeyError:
            found = await KnownIssue.find_one(KnownIssue.key == issue.key)
            if found is None:
                raise PairTaken() from None
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
        `add` puts values in lists without repeating them. Whether it wrote; a change that would make a second live
        entry of its pair is `PairTaken`."""
        update: dict[str, Any] = {"$set": {**fields, "rev": rev + 1}}
        if add:
            update["$addToSet"] = {key: {"$each": values} for key, values in add.items()}
        try:
            result = await KnownIssue.get_pymongo_collection().update_one({"_id": self._oid(issue_id), "rev": rev}, update)
        except DuplicateKeyError:
            raise PairTaken() from None
        return result.modified_count == 1

    async def add(self, issue_id: str, add: dict[str, list[str]], fields: dict[str, Any],
                  where: Optional[dict[str, Any]] = None) -> bool:
        """Put values in lists without repeating them, whatever the revision (two joins at once both land, review
        B4), and set `fields`; the revision still moves on, so an edit read before is a 409. `where` adds conditions
        the entry must still meet. Whether it matched."""
        update: dict[str, Any] = {"$addToSet": {key: {"$each": values} for key, values in add.items()},
                                  "$set": fields, "$inc": {"rev": 1}}
        result = await KnownIssue.get_pymongo_collection().update_one({"_id": self._oid(issue_id), **(where or {})},
                                                                      update)
        return result.matched_count == 1

    async def find(self, *, project: Optional[str] = None, projects: Optional[list[str]] = None,
                   within: Optional[list[str]] = None, status: Optional[list[str]] = None,
                   game_version: Optional[str] = None, before: Optional[datetime] = None,
                   before_id: Optional[str] = None, limit: int = 20) -> list[KnownIssue]:
        """Entries newest first: of a mod, or of any of `projects` (either side), in these states, seen on this
        Minecraft version (or on any); with `within`, only entries whose every mod is one of them (review 3: the
        read is capped, and an entry naming a mod the pack lacks must not use it up). The next page starts after
        (`before`, `before_id`), the order's own key: entries written at one moment are never skipped (review B5)."""
        clauses: list[dict[str, Any]] = []
        ids = [project] if project is not None else projects
        if ids is not None:
            clauses.append({"$or": [{"a.project_id": {"$in": ids}}, {"b.project_id": {"$in": ids}}]})
        if within is not None:
            clauses.append({"a.project_id": {"$in": within}})
            clauses.append({"$or": [{"b": None}, {"b.project_id": {"$in": within}}]})
        if status is not None:
            clauses.append({"status": {"$in": status}})
        if game_version is not None:
            clauses.append({"$or": [{"game_version": {"$in": [game_version, None]}},
                                    {"also_game_versions": game_version}]})
        if before is not None and before_id is not None:
            clauses.append({"$or": [{"updated_at": {"$lt": before}},
                                    {"updated_at": before, "_id": {"$lt": self._oid(before_id)}}]})
        elif before is not None:
            clauses.append({"updated_at": {"$lt": before}})
        query: dict[str, Any] = {"$and": clauses} if clauses else {}
        return await KnownIssue.find(query).sort(("updated_at", SortDirection.DESCENDING),
                                                 ("_id", SortDirection.DESCENDING)).limit(limit).to_list()

    async def count(self, status: str) -> int:
        return await KnownIssue.find({"status": status}).count()
