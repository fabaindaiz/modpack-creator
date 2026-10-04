"""What changes between two locks: a pure difference, no I/O (N3)."""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional


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
