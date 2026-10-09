from datetime import datetime
from typing import Annotated
from beanie import Document, Indexed
from pymongo import ASCENDING, IndexModel

# How long an id Modrinth did not know is not asked again (spec 2026-10-09-economia-de-requests, F6).
UNKNOWN_FOR_SECONDS = 24 * 3600


class ModrinthUnknown(Document):
    """A project id Modrinth did not return, and when: a page asks for it again only after a day.
    MongoDB removes the record itself then (a TTL index)."""
    project_id: Annotated[str, Indexed(unique=True)]
    checked_at: datetime

    class Settings:
        indexes = [IndexModel([("checked_at", ASCENDING)], expireAfterSeconds=UNKNOWN_FOR_SECONDS, name="forget_after_a_day")]


class ModrinthGone(Document):
    """A version id Modrinth did not return (a pin to a version it removed, say), and when: resolving
    takes it as gone for a day without asking. MongoDB removes the record itself then."""
    version_id: Annotated[str, Indexed(unique=True)]
    checked_at: datetime

    class Settings:
        indexes = [IndexModel([("checked_at", ASCENDING)], expireAfterSeconds=UNKNOWN_FOR_SECONDS, name="forget_after_a_day")]
