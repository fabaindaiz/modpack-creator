from datetime import datetime
from typing import Annotated, Any
from beanie import Document, Indexed
from pymongo import ASCENDING, IndexModel

# How long a search's hits are reused (spec 2026-10-09-economia-de-requests, F6).
SEARCH_FOR_SECONDS = 600


class ModrinthSearch(Document):
    """The hits of one search (its text, target and limit as `key`), reused for ten minutes: typing
    the same thing again asks Modrinth nothing. MongoDB removes it itself then (a TTL index)."""
    key: Annotated[str, Indexed(unique=True)]
    hits: list[dict[str, Any]]
    at: datetime

    class Settings:
        indexes = [IndexModel([("at", ASCENDING)], expireAfterSeconds=SEARCH_FOR_SECONDS, name="forget_after_ten_minutes")]
