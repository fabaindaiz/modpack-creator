"""What changes between two locks: a pure difference, no I/O (N3)."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from src.library.utils import as_utc


@dataclass(frozen=True)
class VersionFacts:
    """What tells whether a version comes after another: when it was published, and for what."""
    published: datetime
    game_versions: tuple[str, ...]
    loaders: tuple[str, ...]


Direction = Literal["up", "down", "branch", "unknown"]


def direction(before: Optional[VersionFacts], after: Optional[VersionFacts]) -> Direction:
    """Whether going from `before` to `after` moves a mod forward or back (F27, d-7985ee-322302): by
    publication date, as the resolver's floor (d-7985ee-6dc34c); a version for another Minecraft
    version or loader is a "branch" change, never a step back; without either side, "unknown"."""
    if before is None or after is None:
        return "unknown"
    if not set(before.game_versions) & set(after.game_versions) or not set(before.loaders) & set(after.loaders):
        return "branch"
    return "down" if as_utc(after.published) < as_utc(before.published) else "up"


@dataclass(frozen=True)
class Change:
    """One project: the version it had and the one it has (None where it was not there)."""
    project_id: str
    from_version: Optional[str]
    to_version: Optional[str]


@dataclass(frozen=True)
class ChangeSet:
    added: list[Change]
    removed: list[Change]
    changed: list[Change]
    unchanged: int


def diff_entries(before: Mapping[str, str], after: Mapping[str, str]) -> ChangeSet:
    """Compare two maps of project -> version id. Each list is sorted by project id."""
    added = [Change(p, None, after[p]) for p in sorted(after.keys() - before.keys())]
    removed = [Change(p, before[p], None) for p in sorted(before.keys() - after.keys())]
    both = sorted(before.keys() & after.keys())
    changed = [Change(p, before[p], after[p]) for p in both if before[p] != after[p]]
    return ChangeSet(added=added, removed=removed, changed=changed, unchanged=len(both) - len(changed))
