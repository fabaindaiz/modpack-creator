"""A mod's history in a pack (O8 tanda 2: D2 of d-7985ee-a99db7, T2 and T3 of d-7985ee-16be41): only what
changed, from the builds that carry it, the scans of the pack's servers, the pin changes of the audit, its
past origins, its notes, and its removal and return. Pure."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Collection, Optional, Sequence

from src.model.modpack import PastOrigin


@dataclass(frozen=True)
class BuildPoint:
    """A build of the pack and the mod's version in it (None: the build does not have it)."""
    number: int
    at: datetime
    version: Optional[str]
    version_id: Optional[str]
    test: bool = False
    stable: bool = False


@dataclass(frozen=True)
class ScanPoint:
    """A scan of a server of the pack and the mod's version on it (None: not installed)."""
    server: str
    at: datetime
    version: Optional[str]
    # Its id: two servers may share a name (final review, important 7).
    server_id: str = ""


@dataclass(frozen=True)
class PinEvent:
    """A change of the mod's pin (the audit's «bundle.pin»): to `to_version` (None: unpinned), from
    `from_version`; `known` is False for events written before they said where from."""
    at: datetime
    author: str
    from_version: Optional[str]
    to_version: Optional[str]
    known: bool = True
    # The audit event's id: what orders two changes of the same time (review of tanda 2).
    id: str = ""


@dataclass(frozen=True)
class PresenceEvent:
    """The mod removed from the pack, or added back."""
    at: datetime
    added: bool
    id: str = ""


@dataclass(frozen=True)
class HistoryEntry:
    at: datetime
    kind: str  # build, server, pin, origin, note, removed, added
    text: str
    version: Optional[str] = None
    version_id: Optional[str] = None
    note: Any = None
    # What tells two entries of the same time apart, so «Ver más» pages on (at, key) (review of tanda 2).
    key: str = ""


def _builds(builds: Sequence[BuildPoint]) -> list[HistoryEntry]:
    """Runs of consecutive builds with the same version, one entry each (T3)."""
    found: list[HistoryEntry] = []
    run: list[BuildPoint] = []

    def close() -> None:
        if not run:
            return
        first, last = run[0], run[-1]
        numbers = f"Build {first.number}" if first is last else f"Builds {first.number}–{last.number}"
        marks = [m for m, on in (("prueba", any(b.test for b in run)), ("estable", any(b.stable for b in run))) if on]
        text = " · ".join([numbers, last.version or "sin este mod", *marks])
        found.append(HistoryEntry(at=last.at, kind="build", text=text, version=last.version, version_id=last.version_id,
                                  key=f"build:{last.number}"))

    for point in sorted(builds, key=lambda b: b.number):
        if run and point.version_id != run[-1].version_id:
            close()
            run = []
        run.append(point)
    close()
    return found


def _scans(scans: Sequence[ScanPoint]) -> list[HistoryEntry]:
    """Per server, one entry where the version it runs changed (the first one too)."""
    found: list[HistoryEntry] = []
    last: dict[str, Optional[str]] = {}
    for point in sorted(scans, key=lambda s: s.at):
        key = point.server_id or point.server
        if key in last and last[key] == point.version:
            continue
        shown = point.version or "sin este mod"
        before = last.get(key, "")
        text = f"Servidor {point.server} · {before or 'sin este mod'} → {shown}" if key in last \
            else f"Servidor {point.server} · {shown}"
        found.append(HistoryEntry(at=point.at, kind="server", text=text, version=point.version,
                                  key=f"server:{key}:{point.at.isoformat()}"))
        last[key] = point.version
    return found


def _pin(event: PinEvent, index: int) -> HistoryEntry:
    before = event.from_version or "sin fijar" if event.known else "desconocida"
    done = f"Fijado {event.to_version}" if event.to_version else "Sin fijar"
    return HistoryEntry(at=event.at, kind="pin", text=f"{done} · {event.author} (antes: {before})", version=event.to_version,
                        key=f"pin:{event.id or index}")


def origin_key(origin: Any) -> tuple[str, str, str]:
    """An origin as `past_origins` tells them apart: its source, server and version."""
    return origin.source.value, origin.server_id or "", origin.version_id


def mod_history(builds: Sequence[BuildPoint], scans: Sequence[ScanPoint], pins: Sequence[PinEvent],
                past: Sequence[PastOrigin], notes: Sequence[Any], presence: Sequence[PresenceEvent],
                in_use: Collection[tuple[str, str, str]] = ()) -> list[HistoryEntry]:
    """Everything that changed for the mod in the pack, newest first. A past origin that is one of the mod's
    origins now (`in_use`, by `origin_key`) keeps its entry, the day it was replaced, but is not called past
    (review of tanda 2)."""
    entries = [*_builds(builds), *_scans(scans), *(_pin(e, i) for i, e in enumerate(pins))]
    entries += [HistoryEntry(at=p.until, kind="origin", version=p.origin.version_number or p.origin.version_id,
                             text=f"{'Origen otra vez en uso' if origin_key(p.origin) in in_use else 'Origen pasado'}: "
                                  f"{p.origin.label or p.origin.source.value} "
                                  f"{p.origin.version_number or p.origin.version_id}", key=f"origin:{p.until.isoformat()}:{p.origin.version_id}")
                for p in past if not p.removed_mod]
    entries += [HistoryEntry(at=n.at, kind="note", text=n.text, version=n.version_number, version_id=n.version_id, note=n,
                             key=f"note:{n.id}") for n in notes]
    entries += [HistoryEntry(at=e.at, kind="added" if e.added else "removed",
                             text="Vuelto a agregar al pack" if e.added else "Quitado del pack", key=f"presence:{e.id or i}")
                for i, e in enumerate(presence)]
    return sorted(entries, key=lambda e: (e.at, e.key), reverse=True)


@dataclass(frozen=True)
class HistoryPageOf:
    """A page of a history: its entries, and whether older ones remain."""
    entries: list[HistoryEntry]
    more: bool


def history_page(entries: Sequence[HistoryEntry], before: Optional[datetime], after_key: Optional[str],
                 limit: int) -> HistoryPageOf:
    """The `limit` entries after the one at `before` with `after_key` (newest first, as `mod_history` orders
    them): on (at, key), so entries of the same time are never lost at a page's edge (review of tanda 2).
    Without a key, older than `before` (a link made before keys)."""
    older = [e for e in entries if before is None or e.at < before
             or (after_key is not None and e.at == before and e.key < after_key)]
    return HistoryPageOf(entries=older[:limit], more=len(older) > limit)


@dataclass(frozen=True)
class Suggested:
    """A version to pin a mod to (T4 of d-7985ee-16be41), and what backs it: a stable build of the pack
    («stable»), or a note that it worked («note»); `why` says which."""
    version_id: str
    version_number: str
    source: str
    why: str


def last_stable(builds: Sequence[Any], game_version: str, loader: str) -> Optional[Any]:
    """The newest build marked stable, not a test, for the pack's Minecraft version and loader now."""
    fitting = [b for b in builds if b.stable and not b.test
               and (b.request.target.game_version, b.request.target.loader) == (game_version, loader)]
    return max(fitting, key=lambda b: b.number, default=None)


def suggested_version(project_id: str, builds: Sequence[Any], notes: Sequence[Any], game_version: str, loader: str,
                      via: Optional[str], fits: Callable[[str], bool]) -> Optional[Suggested]:
    """The version to suggest for a mod (T4; user-walk N13, N19): its version in the last stable build when
    it runs the same way there (`via`); else the newest version whose latest note of the mod says it worked,
    and that fits the pack now (`fits`). A note on a whole build counts for the mod's version in it only when
    no note of the mod says otherwise. Archived notes never count."""
    stable = last_stable(builds, game_version, loader)
    entry = next((e for e in stable.lock.entries if e.project_id == project_id), None) if stable else None
    if stable is not None and entry is not None and entry.via == via:
        return Suggested(entry.version_id, entry.version_number, "stable", f"build {stable.number} estable")
    live = [n for n in notes if not n.archived]
    latest: dict[str, Any] = {}
    for note in sorted((n for n in live if n.project_id == project_id and n.version_id), key=lambda n: n.at):
        latest[note.version_id] = note
    by_number = {b.number: b for b in builds}
    for note in sorted((n for n in live if n.build_number is not None and n.verdict == "works"), key=lambda n: n.at):
        build = by_number.get(note.build_number)
        found = next((e for e in build.lock.entries if e.project_id == project_id), None) if build else None
        if found is not None and found.version_id not in latest:
            latest[found.version_id] = note.model_copy(update={"version_id": found.version_id,
                                                               "version_number": found.version_number})
    worked = [n for n in latest.values() if n.verdict == "works" and fits(n.version_id)]
    if not worked:
        return None
    best = max(worked, key=lambda n: n.at)
    return Suggested(best.version_id, best.version_number or best.version_id, "note",
                     f"nota de {best.author.name} · {best.at:%d-%m}: funcionó")

