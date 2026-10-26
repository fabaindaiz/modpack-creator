from datetime import datetime
from typing import Literal, Optional

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel

from src.model.users.actor import ActorRef

# What a note says of what it is about (O8 D4): it worked, it failed, it worked with problems, not tried.
Verdict = Literal["works", "fails", "issues", "untested"]


class NoteTarget(BaseModel):
    """What a note is about (O8 D3): a build of the pack, or a mod's version in it."""
    build_number: Optional[int] = None
    project_id: Optional[str] = None
    version_id: Optional[str] = None
    version_number: Optional[str] = None


class NoteVersion(BaseModel):
    """A note as it was before an edit (O8 D11): kept, a click away."""
    verdict: Verdict
    text: str
    error: Optional[str] = None
    at: datetime


class VersionNote(Document):
    """A note on a build or on a mod's version in a pack (O8 tanda 2, d-7985ee-a99db7 D3–D5, D11): its
    verdict, a comment and the error pasted (cut and masked, P2 and P3 of d-7985ee-a68b5c). Only its
    author edits it; archiving is deleting. `key` makes a form sent twice one note."""
    bundle_id: str
    build_number: Optional[int] = None
    project_id: Optional[str] = None
    version_id: Optional[str] = None
    version_number: Optional[str] = None
    # Where it was seen, with the server's name then (user-walk N22).
    server_id: Optional[str] = None
    server_name: Optional[str] = None
    verdict: Verdict
    text: str
    error: Optional[str] = None
    author: ActorRef
    at: datetime
    edited_at: Optional[datetime] = None
    previous: list[NoteVersion] = Field(default_factory=list)
    archived: bool = False
    # How many times it was written: an edit carries the revision it read (user-walk N15).
    rev: int = 0
    key: str

    class Settings:
        indexes = [
            IndexModel([("bundle_id", ASCENDING), ("project_id", ASCENDING), ("version_id", ASCENDING)]),
            IndexModel([("bundle_id", ASCENDING), ("build_number", ASCENDING)]),
            IndexModel([("key", ASCENDING)], unique=True),
        ]
