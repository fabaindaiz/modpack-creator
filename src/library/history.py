"""A mod's history in a pack (O8 tanda 2: D2 of d-7985ee-a99db7, T2 and T3 of d-7985ee-16be41): only what
changed, from the builds that carry it, the scans of the pack's servers, the pin changes of the audit, its
past origins, its notes, and its removal and return. Pure."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

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


@dataclass(frozen=True)
class PinEvent:
    """A change of the mod's pin (the audit's «bundle.pin»): to `to_version` (None: unpinned), from
    `from_version`; `known` is False for events written before they said where from."""
    at: datetime
    author: str
    from_version: Optional[str]
    to_version: Optional[str]
    known: bool = True


@dataclass(frozen=True)
class PresenceEvent:
    """The mod removed from the pack, or added back."""
    at: datetime
    added: bool


@dataclass(frozen=True)
class HistoryEntry:
    at: datetime
    kind: str  # build, server, pin, origin, note, removed, added
    text: str
    version: Optional[str] = None
    version_id: Optional[str] = None
    note: Any = None


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
        found.append(HistoryEntry(at=last.at, kind="build", text=text, version=last.version, version_id=last.version_id))

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
        if point.server in last and last[point.server] == point.version:
            continue
        shown = point.version or "sin este mod"
        before = last.get(point.server, "")
        text = f"Servidor {point.server} · {before or 'sin este mod'} → {shown}" if point.server in last \
            else f"Servidor {point.server} · {shown}"
        found.append(HistoryEntry(at=point.at, kind="server", text=text, version=point.version))
        last[point.server] = point.version
    return found


def _pin(event: PinEvent) -> HistoryEntry:
    before = event.from_version or "sin fijar" if event.known else "desconocida"
    done = f"Fijado {event.to_version}" if event.to_version else "Sin fijar"
    return HistoryEntry(at=event.at, kind="pin", text=f"{done} · {event.author} (antes: {before})", version=event.to_version)


def mod_history(builds: Sequence[BuildPoint], scans: Sequence[ScanPoint], pins: Sequence[PinEvent],
                past: Sequence[PastOrigin], notes: Sequence[Any], presence: Sequence[PresenceEvent]) -> list[HistoryEntry]:
    """Everything that changed for the mod in the pack, newest first."""
    entries = [*_builds(builds), *_scans(scans), *(_pin(e) for e in pins)]
    entries += [HistoryEntry(at=p.until, kind="origin", version=p.origin.version_number or p.origin.version_id,
                             text=f"Origen pasado: {p.origin.label or p.origin.source.value} "
                                  f"{p.origin.version_number or p.origin.version_id}") for p in past if not p.removed_mod]
    entries += [HistoryEntry(at=n.at, kind="note", text=n.text, version=n.version_number, version_id=n.version_id, note=n)
                for n in notes]
    entries += [HistoryEntry(at=e.at, kind="added" if e.added else "removed",
                             text="Vuelto a agregar al pack" if e.added else "Quitado del pack") for e in presence]
    return sorted(entries, key=lambda e: e.at, reverse=True)
