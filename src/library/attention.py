"""
What the dashboard asks to attend first (B2, d-7985ee-c96727): pure, from the facts the dashboard
already reads. Each item is one card with one action; the list goes by severity and then newest
first. Research: docs/research/2026-10-05-tablero-necesita-atencion.md.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Optional, Sequence

from src.library.utils import as_utc
from src.model.jobs import Job

# How many cards the strip shows before «Ver todas».
VISIBLE = 5
# A failed job is news for a day; after that the jobs page keeps it.
FAILED_WINDOW = timedelta(hours=24)

# Severity: lower is more urgent.
FAILED = 0  # something failed and may have left a server half done
BLOCKING = 1  # a server is not what its build says, or a decision blocks the next build
AVAILABLE = 2  # there is work to do: updates to read, a server to scan
TIDY = 3  # a loose end: a server without a pack

# What failed, with its article, as it reads in "Falló … de X." Unknown kinds read as a job.
FAILED_KIND = {
    "server_scan": "el escaneo",
    "server_import": "la importación",
    "server_update": "la actualización",
    "server_rollback": "el rollback",
    "update_check": "la revisión de actualizaciones",
    "catalog_refresh": "la carga del catálogo",
    "build_create": "el build",
    "modpack_import": "la importación del modpack",
    "notify": "el aviso",
}


@dataclass(frozen=True)
class Attention:
    severity: int
    text: str
    action_label: str
    href: str
    at: Optional[datetime]


@dataclass(frozen=True)
class ServerFact:
    """A server as the dashboard read it. `state` is that of its latest scan: "sync", "drift",
    "unknown" (scanned, not compared) or "none" (never scanned)."""
    id: str
    name: str
    state: str
    has_pack: bool
    scanned_at: Optional[datetime]


@dataclass(frozen=True)
class PackFact:
    """A pack: what its saved resolution leaves pending, and its unpinned updates."""
    id: str
    name: str
    undecided: int = 0
    problems: int = 0
    updates: int = 0
    checked_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


@dataclass(frozen=True)
class FailedJob:
    id: str
    kind: str
    about: Optional[str]  # the server's or the pack's name, when known
    at: datetime


def failed_job_fact(job: Job, server_names: Mapping[str, str], pack_names: Mapping[str, str]) -> FailedJob:
    """A failed job with the name of what it was about: its server (payload or key `server:<id>`)
    or its pack (payload `bundle_id`)."""
    server_id = job.payload.get("server_id") or (job.key[7:] if job.key and job.key.startswith("server:") else None)
    bundle_id = job.payload.get("bundle_id")
    about = server_names.get(str(server_id)) if server_id else None
    if about is None and bundle_id:
        about = pack_names.get(str(bundle_id))
    return FailedJob(id=str(job.id), kind=job.kind, about=about, at=job.finished_at or job.created_at)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _pending(fact: PackFact) -> str:
    parts = []
    if fact.problems:
        parts.append(_plural(fact.problems, "problema", "problemas"))
    if fact.undecided:
        parts.append(_plural(fact.undecided, "mod por decidir", "mods por decidir"))
    return " y ".join(parts)


def attention(servers: Sequence[ServerFact], packs: Sequence[PackFact], failed: Sequence[FailedJob],
              now: datetime) -> list[Attention]:
    """Every item that needs attention, by severity and then newest first (undated last)."""
    items: list[Attention] = []
    for job in failed:
        if as_utc(job.at) < now - FAILED_WINDOW:
            continue
        what = FAILED_KIND.get(job.kind, "un trabajo")
        text = f"Falló {what} de {job.about}." if job.about else f"Falló {what}."
        items.append(Attention(FAILED, text, "Ver", f"/jobs/{job.id}", job.at))
    for server in servers:
        if server.state == "drift":
            items.append(Attention(BLOCKING, f"{server.name} no coincide con su build.", "Simular",
                                   f"/servers/{server.id}#actualizar", server.scanned_at))
        if server.state == "none":
            items.append(Attention(AVAILABLE, f"{server.name} nunca se ha escaneado.", "Escanear",
                                   f"/servers/{server.id}", None))
        if not server.has_pack:
            items.append(Attention(TIDY, f"{server.name} no tiene un modpack vinculado.", "Vincular",
                                   f"/servers/{server.id}#modpack", None))
    for pack in packs:
        if pack.problems or pack.undecided:
            vista = "problemas" if pack.problems else "decidir"
            items.append(Attention(BLOCKING, f"{pack.name} tiene {_pending(pack)}.", "Decidir",
                                   f"/modpacks/{pack.id}?vista={vista}", pack.resolved_at))
        if pack.updates:
            updates = _plural(pack.updates, "actualización disponible", "actualizaciones disponibles")
            items.append(Attention(AVAILABLE, f"{pack.name} tiene {updates}.", "Ver actualizaciones",
                                   f"/modpacks/{pack.id}/actualizaciones", pack.checked_at))
    # Stable: within a severity and a date, the order the facts came in.
    items.sort(key=lambda i: -as_utc(i.at).timestamp() if i.at else float("inf"))
    items.sort(key=lambda i: i.severity)
    return items
