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
    # The target as one string ("1.21.1|fabric,quilt"): the unique key. An index on the `loaders` list
    # would be multikey, unique per element, and a ['fabric'] list would collide with a
    # ['fabric', 'quilt'] one of the same project (code review, 2026-10-09).
    target: str = ""

    def model_post_init(self, context: object) -> None:
        self.target = target_key(self.game_version, self.loaders)

    class Settings:
        indexes = [IndexModel([("project_id", ASCENDING), ("target", ASCENDING)], unique=True, name="project_target_key")]


def target_key(game_version: str, loaders: list[str]) -> str:
    return f"{game_version}|{','.join(sorted(loaders))}"
