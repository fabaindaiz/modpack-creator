"""What a server has installed, read without writing to it (i-7985ee-43df2f, option 1).

Pure functions. Pterodactyl gives no hashes (pterodactyl/panel#4894), so a jar is identified by
the sha1 of its bytes against Modrinth, as the Modrinth App, ferium and Prism do. A jar is
downloaded again only if its path, size or modification time changed, or on a full scan:
restoring a backup keeps old modification times, which the cache cannot see.
"""
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from pydantic import BaseModel, field_validator

ScanStatus = Literal["identified", "unknown", "too_large"]


class ListedFile(BaseModel):
    """A file as the panel lists it: what the cache keys on."""
    path: str
    size: int
    modified_at: datetime

    @field_validator("modified_at")
    @classmethod
    def _as_stored(cls, value: datetime) -> datetime:
        """In UTC, to the millisecond: as MongoDB gives it back, which drops the zone and the
        microseconds. Otherwise no file read back from a scan would match the panel's listing."""
        value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return value.replace(microsecond=value.microsecond // 1000 * 1000)

    def key(self) -> tuple[str, int, datetime]:
        return self.path, self.size, self.modified_at


class ScanFile(ListedFile):
    """A file of a scan: its hashes, and the Modrinth version they belong to, if any."""
    status: ScanStatus
    sha1: Optional[str] = None
    sha512: Optional[str] = None
    project_id: Optional[str] = None
    version_id: Optional[str] = None


def plan_scan(listing: Iterable[ListedFile], previous: Iterable[ScanFile], full: bool,
              max_bytes: int) -> tuple[list[ScanFile], list[ListedFile], list[ScanFile]]:
    """
    Decide what a scan downloads.

    :param listing: The files in the directory now.
    :param previous: The files of the last scan of that directory.
    :param full: Hash everything again, trusting no cached hash.
    :param max_bytes: A jar larger than this is not downloaded: it comes from the server, and is
        never trusted (untrusted-package-is-parsed-never-loaded).
    :return: The files kept from the previous scan, the files to download and hash, and the files
        too large to download. A kept file that was unknown keeps its hashes, so it can be asked
        about again without downloading it.
    """
    cached = {} if full else {f.key(): f for f in previous if f.sha1 and f.status in ("identified", "unknown")}
    kept: list[ScanFile] = []
    to_hash: list[ListedFile] = []
    too_large: list[ScanFile] = []
    for file in listing:
        if file.key() in cached:
            kept.append(cached[file.key()])
        elif file.size > max_bytes:
            too_large.append(ScanFile(**file.model_dump(), status="too_large"))
        else:
            to_hash.append(file)
    return kept, to_hash, too_large


class ExpectedEntry(BaseModel):
    """What a build says a project should be: from a lock entry."""
    project_id: str
    version_id: str
    server: str


class Comparison(BaseModel):
    """A server's files against a build, for the server pack only."""
    matching: list[ScanFile]
    different_version: list[tuple[ScanFile, ExpectedEntry]]
    missing: list[ExpectedEntry]
    extra: list[ScanFile]
    unknown: list[ScanFile]

    @property
    def in_sync(self) -> bool:
        return not (self.different_version or self.missing or self.extra or self.unknown)


def compare(files: Iterable[ScanFile], lock_entries: Iterable[Any]) -> Comparison:
    """
    Compare what a server has with what a build puts on the server.

    Only the build's server entries count: a client-only mod is expected to be absent. A second
    file of the same project is extra; a file Modrinth does not know, or too large to hash, is
    unknown: it is reported, never guessed or deleted.

    :param files: The files of a scan.
    :param lock_entries: The entries of a build's lock (LockEntry objects or mappings).
    """
    expected = [ExpectedEntry.model_validate(e if isinstance(e, Mapping) else e.model_dump())
                for e in lock_entries]
    by_project = {e.project_id: e for e in expected if e.server == "include"}
    seen: set[str] = set()
    result = Comparison(matching=[], different_version=[], missing=[], extra=[], unknown=[])
    for file in files:
        if file.status != "identified" or file.project_id is None:
            result.unknown.append(file)
        elif file.project_id not in by_project or file.project_id in seen:
            result.extra.append(file)
        else:
            seen.add(file.project_id)
            entry = by_project[file.project_id]
            if file.version_id == entry.version_id:
                result.matching.append(file)
            else:
                result.different_version.append((file, entry))
    result.missing = [e for p, e in by_project.items() if p not in seen]
    return result
