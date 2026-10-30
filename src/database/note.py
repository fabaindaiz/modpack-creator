from datetime import datetime
from typing import Any, Optional

from beanie import PydanticObjectId
from beanie.odm.enums import SortDirection
from pymongo.errors import DuplicateKeyError

from src.exceptions import *
from src.model.modpack.note import NoteVersion, VersionNote


class NoteDatabase:
    """The notes on builds and mods' versions (O8 tanda 2)."""

    @staticmethod
    def _oid(note_id: str) -> PydanticObjectId:
        try:
            return PydanticObjectId(note_id)
        except Exception:
            raise HTTP_404_NOT_FOUND(f"Note '{note_id}' not found") from None

    async def insert(self, note: VersionNote) -> VersionNote:
        """Store a note; one with the same `key` already stored is returned instead (a form sent twice)."""
        try:
            return await VersionNote.insert(note)
        except DuplicateKeyError:
            found = await VersionNote.find_one(VersionNote.key == note.key)
            if found is None:
                raise
            return found

    async def get(self, note_id: str) -> VersionNote:
        found = await VersionNote.get(self._oid(note_id))
        if found is None:
            raise HTTP_404_NOT_FOUND(f"Note '{note_id}' not found")
        return found

    async def update(self, note_id: str, rev: int, fields: dict[str, Any], previous: Optional[NoteVersion] = None) -> bool:
        """Write these fields only while the note is still the revision `rev` (user-walk N15), as the next
        one; `previous` is added to its history. Whether it wrote."""
        update: dict[str, Any] = {"$set": {**fields, "rev": rev + 1}}
        if previous is not None:
            update["$push"] = {"previous": previous.model_dump()}
        result = await VersionNote.get_pymongo_collection().update_one({"_id": self._oid(note_id), "rev": rev}, update)
        return result.modified_count == 1

    async def find_by_ids(self, note_ids: list[str]) -> list[VersionNote]:
        """These notes, archived ones left out, newest first; an id that is not one is skipped."""
        oids = []
        for note_id in note_ids:
            try:
                oids.append(PydanticObjectId(note_id))
            except Exception:
                continue
        if not oids:
            return []
        return await VersionNote.find({"_id": {"$in": oids}, "archived": False}) \
            .sort(("at", SortDirection.DESCENDING)).to_list()

    async def find(self, bundle_id: str, *, build_number: Optional[int] = None, project_id: Optional[str] = None,
                   version_ids: Optional[list[str]] = None, archived: bool = False, builds_only: bool = False,
                   before: Optional[datetime] = None, limit: int = 200) -> list[VersionNote]:
        """A pack's notes, newest first: of a build, or of a mod (some of its versions), archived or not."""
        query: dict[str, Any] = {"bundle_id": bundle_id, "archived": archived}
        if build_number is not None:
            query["build_number"] = build_number
        elif builds_only:
            query["build_number"] = {"$ne": None}
        if project_id is not None:
            query["project_id"] = project_id
        if version_ids is not None:
            query["version_id"] = {"$in": version_ids}
        if before is not None:
            query["at"] = {"$lt": before}
        return await VersionNote.find(query).sort(("at", SortDirection.DESCENDING), ("_id", SortDirection.DESCENDING)) \
            .limit(limit).to_list()
