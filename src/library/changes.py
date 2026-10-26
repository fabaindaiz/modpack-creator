"""What changes between two locks: a pure difference, no I/O (N3)."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from src.library.resolution import LockEntry, ResolveRequest
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


# The explained comparison of two builds (O8 B4, d-7985ee-a99db7).


@dataclass(frozen=True)
class RequestChange:
    """One thing that changed in what a build asked for: a field of its request (`label` in Spanish),
    for a mod (`project_id`) when the field is per mod; `before` and `after` as shown, «—» for none."""
    field: str
    label: str
    project_id: Optional[str]
    before: str
    after: str


# The request's fields per mod, with their label, in the order shown. Never the floors, `free`,
# `unchecked` or `origins`: they change with every scan or say nothing someone did (user-walk W18).
_PER_MOD = (("pins", "Fijado"), ("replacements", "Reemplazo"), ("channel_exceptions", "Excepción de canal"),
            ("server_decisions", "En el servidor"))


def _shown(value: object, numbers: Mapping[str, str]) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "sí" if value else "no"
    return numbers.get(str(value), str(value))


def _target_shown(value: object) -> str:
    return ", ".join(sorted(value)) or "—" if isinstance(value, frozenset) else str(value)


def request_changes(before: ResolveRequest, after: ResolveRequest, numbers: Mapping[str, str]) -> list[RequestChange]:
    """What changed from one build's request to another's: Minecraft, loader, channels, secondary
    loaders, then per mod its pin, replacement, channel exception, place on the server and «Pasado a
    nativa». A field missing from either side (a build stored before it existed) is not compared.
    `numbers` turns version ids into their numbers."""
    found: list[RequestChange] = []
    both_target = before.target.model_fields_set & after.target.model_fields_set
    for name, label in (("game_version", "Minecraft"), ("loader", "Loader"), ("channels", "Canales"),
                        ("bridges", "Loaders secundarios")):
        if name not in both_target and name not in ("game_version", "loader"):
            continue
        old, new = getattr(before.target, name), getattr(after.target, name)
        if old != new:
            found.append(RequestChange(field=name, label=label, project_id=None, before=_target_shown(old), after=_target_shown(new)))
    both = before.model_fields_set & after.model_fields_set
    for name, label in _PER_MOD:
        if name not in both:
            continue
        old_map, new_map = getattr(before, name), getattr(after, name)
        for project_id in sorted(set(old_map) | set(new_map)):
            if old_map.get(project_id) != new_map.get(project_id):
                found.append(RequestChange(field=name, label=label, project_id=project_id,
                                           before=_shown(old_map.get(project_id), numbers),
                                           after=_shown(new_map.get(project_id), numbers)))
    if "native_only" in both:
        for project_id in sorted(before.native_only ^ after.native_only):
            found.append(RequestChange(field="native_only", label="Pasado a nativa", project_id=project_id,
                                       before=_shown(project_id in before.native_only, numbers),
                                       after=_shown(project_id in after.native_only, numbers)))
    return found


ReasonKind = Literal["pin", "pack", "admin", "dependency", "other"]


@dataclass(frozen=True)
class ChangeReason:
    """Why a mod is in a build: pinned, asked for by the pack or by hand, a dependency of
    `requested_by`, or the resolver's own `text`."""
    kind: ReasonKind
    requested_by: tuple[str, ...]
    text: str


def change_reason(entry: LockEntry, pins: Mapping[str, str]) -> ChangeReason:
    """Why `entry` is in its build, with that build's `pins` (not the pack's of today, W17)."""
    if entry.project_id in pins:
        return ChangeReason(kind="pin", requested_by=(), text="")
    if entry.origin in ("pack", "admin"):
        return ChangeReason(kind=entry.origin, requested_by=(), text="")  # type: ignore[arg-type]
    if entry.requested_by:
        return ChangeReason(kind="dependency", requested_by=tuple(entry.requested_by), text="")
    return ChangeReason(kind="other", requested_by=(), text=entry.reason)


@dataclass(frozen=True)
class ChangeSummary:
    """How many mods moved each way between two builds."""
    up: int
    down: int
    branch: int
    added: int
    removed: int
    # Changed mods whose direction the catalog cannot tell: never said to go up (final review).
    other: int = 0

    def text(self) -> str:
        """«6 cambios: 4 suben, 1 baja, 1 nuevo»: only the parts that are not zero."""
        total = self.up + self.down + self.branch + self.other + self.added + self.removed
        parts = [f"{n} {one if n == 1 else many}" for n, one, many in (
            (self.up, "sube", "suben"), (self.down, "baja", "bajan"), (self.branch, "cambia de rama", "cambian de rama"),
            (self.other, "cambia de versión", "cambian de versión"),
            (self.added, "nuevo", "nuevos"), (self.removed, "quitado", "quitados")) if n]
        return f"{total} {'cambio' if total == 1 else 'cambios'}: {', '.join(parts)}" if total else "Sin cambios"


def summary_of(changes: ChangeSet, directions: Mapping[str, str]) -> ChangeSummary:
    """The summary of `changes`; `directions` says which changed mods go up, down or change branch;
    one it does not name only changes version."""
    count = {way: sum(1 for c in changes.changed if directions.get(c.project_id) == way) for way in ("up", "down", "branch")}
    return ChangeSummary(up=count["up"], down=count["down"], branch=count["branch"],
                         other=len(changes.changed) - sum(count.values()), added=len(changes.added), removed=len(changes.removed))

