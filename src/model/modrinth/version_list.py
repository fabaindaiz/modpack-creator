from datetime import datetime
from beanie import Document
from pymongo import ASCENDING, IndexModel


class ModrinthVersionList(Document):
    """What the catalog accounts for of a project for one target (spec 2026-10-09-economia-de-requests,
    F2): every version Modrinth listed for it that fits `game_version` and `loaders` is stored, and
    `version_ids` are the ids it listed then. A check compares the project's list against these ids,
    never against the stored project: another page may refresh that without fetching versions."""
    project_id: str
    game_version: str
    # Sorted: the key is the set.
    loaders: list[str]
    version_ids: list[str]
    fetched_at: datetime

    class Settings:
        indexes = [IndexModel([("project_id", ASCENDING), ("game_version", ASCENDING), ("loaders", ASCENDING)],
                              unique=True, name="project_target")]
