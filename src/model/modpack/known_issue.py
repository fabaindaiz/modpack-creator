from datetime import datetime
from typing import Literal, Optional

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel

from src.model.users.actor import ActorRef

# Suspected by an editor, confirmed or rejected (a false alarm) by an operator (K-Q2 of d-7985ee-57d185).
IssueStatus = Literal["suspected", "confirmed", "rejected"]


class IssueVersion(BaseModel):
    """An end of a range, kept whole when it is marked: drawing a warning never asks Modrinth, and another
    pack's catalog may not have it (user-walk K5)."""
    version_id: str
    version_number: str
    published: datetime
    game_versions: list[str]
    loaders: list[str]


class IssueSide(BaseModel):
    """A mod of a known issue and where it breaks: from `broken_from` (the first broken, included) to
    `fixed_in` (the first good, left out); empty is any version, or not known yet (K-Q2)."""
    project_id: str
    # Its name when it was marked: the page reads even if Modrinth retires the mod (K16).
    title: str
    broken_from: Optional[IssueVersion] = None
    fixed_in: Optional[IssueVersion] = None


class KnownIssue(Document):
    """An entry of the knowledge base (O8 tanda 3, d-7985ee-a99db7 D6–D10, d-7985ee-57d185): two mods that fail
    together, or one version alone (`b` empty, K-Q4). Global to the installation, with the packs it was seen in.
    It only warns (D8): it never changes a resolution. A pair is stored with `a.project_id < b.project_id`."""
    a: IssueSide
    b: Optional[IssueSide] = None
    # Where it was seen, empty for any.
    game_version: Optional[str] = None
    loader: Optional[str] = None
    status: IssueStatus
    summary: str
    note_ids: list[str] = Field(default_factory=list)
    seen_in: list[str] = Field(default_factory=list)
    author: ActorRef
    at: datetime
    updated_at: datetime
    # How many times it was written: an edit carries the revision it read (user-walk K11).
    rev: int = 0
    key: str

    class Settings:
        indexes = [
            IndexModel([("a.project_id", ASCENDING)]),
            IndexModel([("b.project_id", ASCENDING)]),
            IndexModel([("key", ASCENDING)], unique=True),
        ]
