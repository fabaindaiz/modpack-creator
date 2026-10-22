"""What a server has installed, read without writing to it (i-7985ee-43df2f, option 1).

Pure functions. Pterodactyl gives no hashes (pterodactyl/panel#4894), so a jar is identified by
the sha1 of its bytes against Modrinth, as the Modrinth App, ferium and Prism do. A jar is
downloaded again only if its path, size or modification time changed, or on a full scan.
Restoring a backup keeps old modification times, which the cache cannot see: the scan reads the
server's activity first and goes full after a restore (docs/research/2026-10-12-actividad-pterodactyl.md).
Wings' ctime (`created_at`) is kept as information only: Wings chowns every file when the server
boots, and that changes it.
"""
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Protocol
from pydantic import BaseModel, field_validator
from src.library.jarmeta import ModMetadata

ScanStatus = Literal["identified", "unknown", "too_large"]

# Why a scan read every jar (owner, 2026-10-21): stored with the scan, None when it read only what
# changed. `first`: no scan before. `forced`: someone asked for it. `periodic`: the scheduler's full
# scan. `restored`: a backup restore, a decompress or a reinstall in the activity. `too_old`: the scan
# before is older than the activity the panel keeps (90 days). `no_activity_permission`: the key lacks
# `activity.read` (403). `activity_unreadable`: the activity failed otherwise. `node_changed`: the
# server moved to another node, which leaves no activity event. `node_unreadable`: the panel did not
# say the server's node.
FullReason = Literal["first", "forced", "periodic", "restored", "too_old", "no_activity_permission",
                     "activity_unreadable", "node_changed", "node_unreadable"]

# What the server's page says next to the last scan, one text per reason (owner, 2026-10-22); an
# unknown code shows only «completo».
FULL_REASON_TEXT: dict[str, str] = {
    "no_activity_permission": "Escaneo completo: la key no tiene permiso para leer la actividad (activity.read); "
                              "con él, los siguientes solo leen lo que cambió",
    "first": "Escaneo completo: es el primer escaneo de este servidor",
    "restored": "Escaneo completo: desde el escaneo anterior hubo una restauración de backup, una descompresión "
                "o una reinstalación",
    "too_old": "Escaneo completo: el escaneo anterior tiene más de 90 días, más de lo que el panel guarda de actividad",
    "node_changed": "Escaneo completo: el servidor cambió de nodo desde el escaneo anterior",
    "activity_unreadable": "Escaneo completo: no se pudo leer la actividad del servidor",
    "forced": "Escaneo completo: lo pediste así",
    "periodic": "Escaneo completo: el repaso periódico que relee todo cada tanto",
    "node_unreadable": "Escaneo completo: no se pudo leer en qué nodo está el servidor",
}


def full_reason_text(reason: Optional[str]) -> Optional[str]:
    """What the panel says about why a scan was full; None when it says nothing more than «completo»."""
    return FULL_REASON_TEXT.get(reason) if reason is not None else None


class ListedFile(BaseModel):
    """A file as the panel lists it: what the cache keys on."""
    path: str
    size: int
    modified_at: datetime
    # Wings' ctime (S4): a restored backup keeps the modification time but not this. None on scans
    # stored before it was kept, which still serve as cache.
    created_at: Optional[datetime] = None

    @field_validator("modified_at", "created_at")
    @classmethod
    def _as_stored(cls, value: Optional[datetime]) -> Optional[datetime]:
        """In UTC, to the millisecond: as MongoDB gives it back, which drops the zone and the
        microseconds. Otherwise no file read back from a scan would match the panel's listing."""
        if value is None:
            return None
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
    # What the jar says it is (fabric.mod.json, mods.toml…), read as data while hashing it (S5): an
    # import needs it for an unknown jar, and the scan already holds its bytes.
    metadata: Optional[ModMetadata] = None
    metadata_read: bool = False
    # False when the hashes came from the build instead of the bytes (an update's seeded rescan, U3):
    # the next scan downloads it once to confirm them (owner, 2026-10-12).
    confirmed: bool = True


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
    cached = {} if full else {f.key(): f for f in previous
                              if f.sha1 and f.status in ("identified", "unknown") and f.confirmed}
    kept: list[ScanFile] = []
    to_hash: list[ListedFile] = []
    too_large: list[ScanFile] = []
    for file in listing:
        hit = cached.get(file.key())
        if hit is not None:
            # The listing's ctime is information only (a boot's chown moves it): the hit keeps it fresh.
            kept.append(hit.model_copy(update={"created_at": file.created_at or hit.created_at}))
        elif file.size > max_bytes:
            too_large.append(ScanFile(**file.model_dump(), status="too_large"))
        else:
            to_hash.append(file)
    return kept, to_hash, too_large


class LocalFile(BaseModel):
    """A jar of a bundle that is not on Modrinth (d-7985ee-c8a15d): known by its sha512, left
    untouched on the server by an update, and never in an .mrpack (it has no allowed URL)."""
    filename: str
    sha512: str
    sha1: str
    size: int


class ClaimedFile(BaseModel):
    """A jar Modrinth does not know by hash that the admin confirmed as a project (an import
    candidate): it is that project, at a version to be replaced by the build's."""
    sha512: str
    project_id: str


class ExpectedEntry(BaseModel):
    """What a build says a project should be: from a lock entry. `version_number` is what a person
    reads; absent only when the entry came without it."""
    project_id: str
    version_id: str
    server: str
    version_number: Optional[str] = None


class Comparison(BaseModel):
    """A server's files against a build, for the server pack only."""
    matching: list[ScanFile]
    different_version: list[tuple[ScanFile, ExpectedEntry]]
    missing: list[ExpectedEntry]
    extra: list[ScanFile]
    unknown: list[ScanFile]
    # Files the build explains as its local files.
    local: list[ScanFile] = []
    # Projects of the extra files that the build has but keeps off the server (client only).
    client_only: list[str] = []

    @property
    def in_sync(self) -> bool:
        return not (self.different_version or self.missing or self.extra or self.unknown)


def compare(files: Iterable[ScanFile], lock_entries: Iterable[Any],
            local_files: Iterable[LocalFile] = (), claimed: Iterable[ClaimedFile] = ()) -> Comparison:
    """
    Compare what a server has with what a build puts on the server.

    Only the build's server entries count: a client-only mod is expected to be absent. A second
    file of the same project is extra; a file Modrinth does not know, or too large to hash, is
    unknown: it is reported, never guessed or deleted.

    :param files: The files of a scan.
    :param lock_entries: The entries of a build's lock (LockEntry objects or mappings).
    :param local_files: The build's local files: a file with one of their sha512 is explained.
    :param claimed: Jars the admin confirmed as a project: compared as that project, at a version
        that is never the build's.
    """
    local = {f.sha512 for f in local_files}
    claims = {c.sha512: c.project_id for c in claimed}
    expected = [ExpectedEntry.model_validate(e if isinstance(e, Mapping) else e.model_dump())
                for e in lock_entries]
    by_project = {e.project_id: e for e in expected if e.server == "include"}
    seen: set[str] = set()
    result = Comparison(matching=[], different_version=[], missing=[], extra=[], unknown=[])
    for file in files:
        if file.status == "unknown" and file.sha512 in claims:
            file = file.model_copy(update={"status": "identified", "project_id": claims[file.sha512]})
        if file.sha512 is not None and file.sha512 in local:
            result.local.append(file)
        elif file.status != "identified" or file.project_id is None:
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
    excluded = {e.project_id for e in expected if e.server == "exclude"}
    result.client_only = sorted({f.project_id for f in result.extra if f.project_id is not None and f.project_id in excluded})
    return result


def drift_message(server: str, bundle: str, build: int, comparison: Comparison) -> str:
    """What a server differs in from its build, for a person to read."""
    if comparison.in_sync:
        return f"✅ {server} coincide con {bundle}, build {build}."
    lines = [f"⚠️ {server} no coincide con {bundle}, build {build}:"]
    lines += [f"- {f.path}: otra versión ({f.version_id}; el build pide {e.version_id})" for f, e in comparison.different_version]
    lines += [f"- falta {e.project_id} ({e.version_id})" for e in comparison.missing]
    lines += [f"- sobra {f.path}" for f in comparison.extra]
    lines += [f"- desconocido {f.path}" for f in comparison.unknown]
    return "\n".join(lines)


class Unexplained(BaseModel):
    """The jars of a server that its build does not explain, by fix (F26): `in_pack` the bundle
    already has (as a local or claimed file), so a new build explains them; `foreign` it does not,
    so importing the server brings them in, except `too_large`, the part of `foreign` too large to
    read, which importing only lists. Paths, in the comparison's order."""
    in_pack: list[str]
    foreign: list[str]
    too_large: list[str] = []


def unexplained(comparison: Comparison, bundle_projects: Iterable[str], kept_sha512s: Iterable[str]) -> Unexplained:
    """
    What stops an update to the compared build, and what importing would bring.

    :param bundle_projects: The bundle's project ids: an extra of one of them is a copy to remove,
        explained.
    :param kept_sha512s: The sha512 of the bundle's local and claimed files: an unknown jar with
        one of them is in the pack, though not in this build.
    """
    projects, kept = set(bundle_projects), set(kept_sha512s)
    in_pack = [f.path for f in comparison.unknown if f.sha512 is not None and f.sha512 in kept]
    foreign = ([f.path for f in comparison.unknown if f.sha512 is None or f.sha512 not in kept]
               + [f.path for f in comparison.extra if f.project_id not in projects])
    too_large = [f.path for f in comparison.unknown if f.status == "too_large"]
    return Unexplained(in_pack=in_pack, foreign=foreign, too_large=too_large)


class _HasProjectId(Protocol):
    @property
    def project_id(self) -> str: ...


class _HasSha512(Protocol):
    @property
    def sha512(self) -> str: ...


class BundleContents(Protocol):
    """What `unexplained_for` reads of a bundle (a ModpackBundle; named here to avoid importing
    the model, which imports this module)."""
    @property
    def projects(self) -> Sequence[_HasProjectId]: ...
    @property
    def local_files(self) -> Sequence[_HasSha512]: ...
    @property
    def claimed_files(self) -> Sequence[_HasSha512]: ...


def unexplained_for(comparison: Comparison, bundle: BundleContents) -> Unexplained:
    """`unexplained` with what a bundle has: its projects, and its local and claimed jars."""
    return unexplained(comparison, (p.project_id for p in bundle.projects),
                       [*(f.sha512 for f in bundle.local_files), *(c.sha512 for c in bundle.claimed_files)])
