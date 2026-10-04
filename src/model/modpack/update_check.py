from beanie import Document, PydanticObjectId
from datetime import datetime
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, IndexModel
from typing import Optional

class AvailableUpdate(BaseModel):
    """A newer version Modrinth has for a file of the build (version numbers, for people to read).
    `pinned` marks a mod the admin fixed: Modrinth's answer ignores pins, so it is shown, never
    counted or notified."""
    project_id: str
    from_version: str
    to_version: str
    pinned: bool = False

class UpdateCheck(Document):
    """One check of a modpack's recommended build against Modrinth, never edited afterwards.
    `error` is set when Modrinth could not answer; `notified_digest` when this check sent a
    notification, so the same updates are not announced twice."""
    bundle_id: PydanticObjectId
    build_number: int
    checked_at: datetime
    updates: list[AvailableUpdate] = Field(default_factory=list)
    error: Optional[str] = None
    notified_digest: Optional[str] = None

    class Settings:
        indexes = [IndexModel([("bundle_id", ASCENDING), ("checked_at", DESCENDING)])]
