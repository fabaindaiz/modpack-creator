"""Resolve a modpack: from what the admin asked for and a catalog of versions, to a lock.

A pure function (i-7985ee-7f735a): no network, no database. The caller fetches what the lock says
it needs (`needs_projects`, `needs_versions`) and calls again until the lock is complete. The
algorithm is docs/research/2026-10-resolucion-y-formatos.md §5, with three decisions:
- d-7985ee-86fffa: replacement rules are global, and a pack can add or override one.
- d-7985ee-20b06b: two exact requirements that differ are reported, never backtracked.
- d-7985ee-f0c7a6: on the server, what Modrinth leaves ambiguous is the admin's call.
"""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from src.library.bridges import FABRIC_API, REPLACES, Bridge, bridge as find_bridge, bridges_for, companions
from pydantic import BaseModel, field_validator

ADMIN = "admin"
CHANNEL_RANK = {"release": 0, "beta": 1, "alpha": 2}
# The channels a project's exception may accept: the less stable ones (d-7985ee-b1ce87).
EXCEPTION_CHANNELS = ("beta", "alpha")
# Quilt loads Fabric mods; the reverse is not true.
LOADER_ACCEPTS = {"quilt": {"quilt", "fabric"}}

# What each project type is loaded by (d-7985ee-9170b8). A mod uses the pack's loader; the rest
# have loaders of their own on Modrinth.
TYPE_LOADERS = {"shader": {"iris", "optifine", "canvas", "vanilla"}, "resourcepack": {"minecraft"},
                "datapack": {"datapack"}}
SUPPORTED_TYPES = {"mod", *TYPE_LOADERS}
# A shader loader that is a mod on Modrinth: a shader for it is useless in a pack without it, so
# resolution suggests it. OptiFine is not on Modrinth.
SHADER_LOADER_MODS = {"iris": "YL57xq9U"}
# On the server: shaders and resource packs never, datapacks always, mods by their environment.
TYPE_SERVER = {"shader": "exclude", "resourcepack": "exclude", "datapack": "include"}

Server = Literal["include", "exclude", "admin"]
SERVER_INCLUDE = {"client_and_server", "server_only", "server_only_client_optional",
                  "dedicated_server_only", "client_or_server_prefers_both"}
SERVER_EXCLUDE = {"client_only", "singleplayer_only"}


def server_inclusion(environment: Optional[str], project_type: str = "mod") -> Server:
    """
    Whether a version belongs in the server pack.

    By type first: shaders and resource packs are the client's, datapacks the server's
    (d-7985ee-9170b8). A mod by Modrinth's environment: ambiguous (client_or_server,
    client_only_server_optional), unknown or missing is the admin's call, since a missing
    environment is not "both" (d-7985ee-f0c7a6).
    """
    if project_type in TYPE_SERVER:
        return "include" if TYPE_SERVER[project_type] == "include" else "exclude"
    if environment in SERVER_INCLUDE:
        return "include"
    if environment in SERVER_EXCLUDE:
        return "exclude"
    return "admin"


def merge_replacements(global_rules: Mapping[str, Optional[str]],
                       pack_rules: Mapping[str, Optional[str]]) -> dict[str, Optional[str]]:
    """
    The replacement rules a pack resolves with: the global ones, plus the pack's, which win for
    the same project (d-7985ee-86fffa). A rule maps a project to its substitute, or to None to
    drop it.
    """
    return {**global_rules, **pack_rules}


class CandidateFile(BaseModel):
    filename: str
    url: str
    sha1: str
    sha512: str
    size: int


class CandidateDependency(BaseModel):
    project_id: Optional[str]
    version_id: Optional[str]
    file_name: Optional[str]
    dependency_type: str


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class Candidate(BaseModel):
    """One version of a project, with what resolution needs and nothing else."""
    project_id: str
    version_id: str
    version_number: str
    version_type: str
    status: str
    date_published: datetime
    game_versions: list[str]
    loaders: list[str]
    environment: Optional[str]
    dependencies: list[CandidateDependency]
    file: Optional[CandidateFile]

    @field_validator("date_published")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        """MongoDB gives dates back without a zone (UTC); Modrinth sends them with one: always UTC."""
        return _as_utc(value)

    @classmethod
    def from_modrinth(cls, version: Mapping[str, Any]) -> "Candidate":
        """From a version as Modrinth's API returns it (/project/{id}/version, /versions)."""
        files = version.get("files", [])
        primary = next((f for f in files if f.get("primary")), files[0] if files else None)
        return cls(
            project_id=version["project_id"],
            version_id=version["id"],
            version_number=version["version_number"],
            version_type=version["version_type"],
            status=version.get("status", "listed"),
            date_published=version["date_published"],
            game_versions=version["game_versions"],
            loaders=version["loaders"],
            environment=version.get("environment"),
            dependencies=[CandidateDependency(
                project_id=d.get("project_id"), version_id=d.get("version_id"),
                file_name=d.get("file_name"), dependency_type=d["dependency_type"])
                for d in version.get("dependencies", [])],
            file=None if primary is None else CandidateFile(
                filename=primary["filename"], url=primary["url"], sha1=primary["hashes"]["sha1"],
                sha512=primary["hashes"]["sha512"], size=primary["size"]),
        )


class Target(BaseModel):
    game_version: str
    loader: str
    channels: frozenset[str] = frozenset({"release"})
    # The bridges the pack turned on (batch C, d-7985ee-a8d395): keys of src/library/bridges.py.
    bridges: frozenset[str] = frozenset()

    def loaders(self, project_type: str) -> set[str]:
        """The Modrinth loaders a project of this type runs on natively in this pack. Only the
        native ones (d-7985ee-6ebc03): a use that must see bridged versions asks for
        `loaders_with_bridges`."""
        return TYPE_LOADERS.get(project_type) or LOADER_ACCEPTS.get(self.loader, {self.loader})

    def _active_bridges(self) -> list[Any]:
        return [b for b in bridges_for(self.loader, self.game_version) if b.key in self.bridges]

    def loaders_with_bridges(self, project_type: str) -> set[str]:
        """The native loaders and, for a mod, those its active bridges run on this version."""
        native = self.loaders(project_type)
        if project_type in TYPE_LOADERS:
            return native
        return native | {b.guest for b in self._active_bridges()}

    def via(self, candidate: "Candidate", project_type: str = "mod") -> Optional[str]:
        """None when the version runs natively (a jar for both loaders is native); otherwise the key
        of the active bridge it runs through, or None when none does."""
        if self.loaders(project_type) & set(candidate.loaders) or project_type in TYPE_LOADERS:
            return None
        return next((b.key for b in self._active_bridges() if b.guest in candidate.loaders), None)

    def fits(self, candidate: Candidate, project_type: str = "mod") -> bool:
        """Runs on this game version and loader, and is listed. Channels aside: a mod that pins
        an exact version of another gets that version, whatever its channel."""
        accepted = self.loaders_with_bridges(project_type)
        return (self.game_version in candidate.game_versions
                and bool(accepted & set(candidate.loaders))
                and candidate.status == "listed")

    def allows(self, candidate: Candidate, project_type: str = "mod") -> bool:
        return self.fits(candidate, project_type) and candidate.version_type in self.channels


@dataclass
class Catalog:
    """The versions resolution may choose from. A project missing from `by_project` has not been
    fetched yet; one mapped to an empty list was fetched and has no versions. `types` holds each
    known project's type: without it a project cannot be resolved, and is asked for. `gone` holds
    the version ids Modrinth was asked for and did not return."""
    by_project: dict[str, list[Candidate]] = field(default_factory=dict)
    by_id: dict[str, Candidate] = field(default_factory=dict)
    types: dict[str, str] = field(default_factory=dict)
    gone: set[str] = field(default_factory=set)
    # The projects whose versions for the active bridges' loaders were fetched (d-7985ee-4fd88e):
    # only those with no native version that fits.
    bridged_loaded: set[str] = field(default_factory=set)

    @classmethod
    def of(cls, candidates: Iterable[Candidate], types: Optional[Mapping[str, str]] = None) -> "Catalog":
        """A catalog of these candidates. Without `types`, every project is a mod."""
        catalog = cls()
        candidates = list(candidates)
        for candidate in candidates:
            catalog.add(candidate)
        catalog.types = dict(types) if types is not None else {c.project_id: "mod" for c in candidates}
        return catalog

    def add(self, candidate: Candidate) -> None:
        if candidate.version_id not in self.by_id:
            self.by_project.setdefault(candidate.project_id, []).append(candidate)
            self.by_id[candidate.version_id] = candidate


ORIGIN_REASON = {"pack": "en el pack", "admin": "agregado por el admin"}


class ResolveRequest(BaseModel):
    target: Target
    projects: list[str]
    # Project id → where it came from: "pack" or "admin" (d-7985ee-b15dd4). Both resolve the same.
    origins: dict[str, str] = {}
    pins: dict[str, str] = {}
    replacements: dict[str, Optional[str]] = {}
    server_decisions: dict[str, bool] = {}
    # Project id → the least stable channel it also accepts ("beta" or "alpha"), only while none of
    # its versions fits in the pack's channels: once one does, the exception has no effect.
    channel_exceptions: dict[str, str] = {}
    # Project id → the versions it has in the build in force and on the linked servers: a mod never
    # goes back (owner's rule, 2026-10-12, d-7985ee-6dc34c). The floor is the newest of them that
    # fits the target (one for another game version or loader does not count); a free choice only
    # takes versions published at or after it, and keeps it when none is. A pin may go lower.
    floors: dict[str, list[str]] = {}
    # Projects the admin moved to their native versions («Pasar a nativa», d-7985ee-8e5f9e).
    native_only: set[str] = set()

    @field_validator("floors", mode="before")
    @classmethod
    def _floor_lists(cls, value: Any) -> Any:
        """A single version id per project reads as a list of one."""
        if isinstance(value, dict):
            return {k: [v] if isinstance(v, str) else v for k, v in value.items()}
        return value


def request_digest(request: ResolveRequest) -> str:
    """A stable sha256 of what a resolution was asked for: the same request, in any order of
    projects, keys or channels, gives the same digest; any change to it gives another."""
    data = request.model_dump(mode="json")
    # Left out when empty: every resolution saved before the field existed keeps its digest.
    if not data["channel_exceptions"]:
        del data["channel_exceptions"]
    # Batch C's too (final review): a pack with no bridge and no mod moved to native keeps its digest.
    for parent, key in ((data["target"], "bridges"), (data, "native_only")):
        if parent[key]:
            parent[key] = sorted(parent[key])
        else:
            del parent[key]
    # The floors follow the build in force, not what the admin asked for: a new build must not
    # make the saved resolution read as stale.
    del data["floors"]
    data["projects"] = sorted(data["projects"])
    data["target"]["channels"] = sorted(data["target"]["channels"])
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


class LockEntry(BaseModel):
    project_id: str
    # "pack" or "admin" for what was asked for; None for what came in as a dependency.
    origin: Optional[str] = None
    # mod, shader, resourcepack or datapack: where the file goes in an instance. Builds frozen
    # before this field read as mods, which they all were.
    project_type: str = "mod"
    version_id: str
    version_number: str
    file: Optional[CandidateFile]
    environment: Optional[str]
    server: Server
    reason: str
    requested_by: list[str]
    # The bridge it runs through (batch C), None when native.
    via: Optional[str] = None


def _asked(requester: str) -> list[str]:
    return [] if requester == ADMIN else [requester]


def _preference(candidate: Candidate) -> tuple[int, float]:
    """Most stable channel first, then the newest."""
    return (CHANNEL_RANK.get(candidate.version_type, 99), -candidate.date_published.timestamp())


class Alternative(BaseModel):
    """A version that fits the game version and loader but is not in the pack's channels."""
    version_id: str
    version_number: str
    channel: str
    published: datetime


class Problem(BaseModel):
    kind: Literal["unresolvable", "pin_mismatch", "pin_conflict", "incompatible", "no_file",
                  "bridge_dependency", "bridge_missing", "bridge_conflict", "via_change"]
    project_id: Optional[str]
    detail: str
    requested_by: list[str] = []
    alternative: Optional[Alternative] = None
    conflict_with: Optional[str] = None
    # About a fixed version: True if the admin's pin is at fault, False if a dependency asks for an
    # exact version; None otherwise, and in problems stored before this field.
    pinned: Optional[bool] = None


class Suggestion(BaseModel):
    project_id: str
    requested_by: list[str]


class Lock(BaseModel):
    entries: list[LockEntry]
    problems: list[Problem]
    suggestions: list[Suggestion]
    # What to fetch before calling again: a project and its versions; only a project, for its
    # type; versions by id.
    needs_projects: set[str]
    needs_types: set[str]
    needs_versions: set[str]
    # Projects resolved to a version outside the pack's channels through their channel exception.
    excepted: list[str] = []
    # Projects with no native version that fits, whose versions for the active bridges' loaders
    # are still to fetch (d-7985ee-4fd88e).
    needs_bridged: set[str] = set()
    # A mod installed through a bridge whose native version caught up with it (published at or after
    # the installed one): offered as «Pasar a nativa», never taken (d-7985ee-7e602f). Project -> version.
    native_available: dict[str, str] = {}
    # One whose native version is still older than the installed one: not offered yet.
    native_behind: dict[str, str] = {}

    @property
    def complete(self) -> bool:
        """Nothing left to fetch: the lock says all there is to say about this catalog."""
        return not self.needs_projects and not self.needs_types and not self.needs_versions and not self.needs_bridged

    @property
    def undecided(self) -> list[LockEntry]:
        """Entries whose place on the server waits for the admin (d-7985ee-f0c7a6)."""
        return [e for e in self.entries if e.server == "admin"]


@dataclass
class _Chosen:
    candidate: Candidate
    reason: str
    requested_by: list[str]
    exact: bool


@dataclass
class _Exact:
    version_id: str
    requested_by: list[str]


@dataclass
class _Restart(Exception):
    project_id: str
    exact: _Exact


class _Run:
    """One pass of the greedy worklist. Raises _Restart when an exact requirement arrives for a
    project already chosen freely: the pass starts over with that version fixed."""

    def __init__(self, request: ResolveRequest, catalog: Catalog, exact: dict[str, _Exact]) -> None:
        self.request = request
        self.catalog = catalog
        self.exact = {project: _Exact(e.version_id, list(e.requested_by)) for project, e in exact.items()}
        self.chosen: dict[str, _Chosen] = {}
        self.problems: list[Problem] = []
        self.suggested: dict[str, list[str]] = {}
        self.incompatible: list[tuple[str, CandidateDependency]] = []
        self.needs_projects: set[str] = set()
        self.needs_types: set[str] = set()
        self.needs_versions: set[str] = set()
        self.needs_bridged: set[str] = set()
        self.native_available: dict[str, str] = {}
        self.native_behind: dict[str, str] = {}
        self.excepted: set[str] = set()

    def run(self) -> Lock:
        origins = self.request.origins
        work: list[tuple[Optional[str], Optional[str], str, str]] = [
            (project, None, ADMIN, ORIGIN_REASON.get(origins.get(project, ""), "pedido por el admin"))
            for project in self.request.projects]
        while work:
            project_id, version_id, requester, reason = work.pop(0)
            candidate = self._choose(project_id, version_id, requester, reason)
            if candidate is not None:
                work += self._dependencies(candidate)
        self._check_incompatible()
        return self._lock()

    def _choose(self, project_id: Optional[str], version_id: Optional[str],
                requester: str, reason: str) -> Optional[Candidate]:
        """Choose a version for one requirement; the candidate, if it was newly chosen."""
        if version_id is not None:
            return self._choose_exact(project_id, version_id, requester, reason)
        assert project_id is not None
        if project_id in self.chosen:
            self._requested_by(project_id, requester)
            return None
        if project_id in self.exact:
            pin = self.exact[project_id]
            if pin.version_id in self.catalog.gone:
                self.problems.append(Problem(kind="unresolvable", project_id=project_id,
                    requested_by=_asked(requester), pinned=ADMIN in pin.requested_by, detail=(
                    f"{pin.version_id}, fijada por {', '.join(pin.requested_by)}, ya no está en Modrinth")))
                return None
            candidate = self.catalog.by_id.get(pin.version_id)
            if candidate is None:
                self.needs_versions.add(pin.version_id)
                return None
            if project_id not in self.catalog.types:
                self.needs_types.add(project_id)
                return None
            if not self.request.target.fits(candidate, self.catalog.types[project_id]):
                self.problems.append(Problem(kind="pin_mismatch", project_id=project_id,
                    requested_by=_asked(requester), pinned=ADMIN in pin.requested_by, detail=(
                    f"{pin.version_id}, fijada por {', '.join(pin.requested_by)}, no es para "
                    f"{self.request.target.game_version} con {self.request.target.loader}")))
                return None
            return self._take(candidate, reason, requester, exact=True)
        if project_id not in self.catalog.by_project or project_id not in self.catalog.types:
            self.needs_projects.add(project_id)
            return None
        project_type = self.catalog.types[project_id]
        if project_type not in SUPPORTED_TYPES:
            self.problems.append(Problem(kind="unresolvable", project_id=project_id,
                requested_by=_asked(requester), detail=(
                f"un proyecto de tipo {project_type} no entra en un modpack; pedido por {requester}")))
            return None
        target = self.request.target
        if (target.loaders_with_bridges(project_type) != target.loaders(project_type)
                and project_id not in self.catalog.bridged_loaded
                and not any(target.allows(c, project_type) and target.via(c, project_type) is None
                            for c in self.catalog.by_project[project_id])):
            self.needs_bridged.add(project_id)
            return None
        fitting_floors: list[Candidate] = []
        missing_floors = False
        for floor_id in self.request.floors.get(project_id, []):
            if floor_id in self.catalog.gone:
                continue
            known = self.catalog.by_id.get(floor_id)
            if known is None:
                self.needs_versions.add(floor_id)
                missing_floors = True
            elif self.request.target.fits(known, project_type):
                fitting_floors.append(known)
            elif (off := self._bridge_off(known, project_type)) is not None:
                # Installed through a bridge the pack turned off: the resolution never changes a mod's
                # way by itself (d-7985ee-7e602f), nor takes an older native one (final review).
                self.problems.append(Problem(kind="via_change", project_id=project_id, requested_by=_asked(requester),
                    detail=(f"{project_id} está instalado vía {off.label}, que el pack apagó: reactiva el puente "
                            "o quita el mod")))
                return None
        if missing_floors:
            return None
        floor = max(fitting_floors, key=lambda c: _as_utc(c.date_published)) if fitting_floors else None
        allowed = [c for c in self.catalog.by_project[project_id] if self.request.target.allows(c, project_type)]
        if project_id in self.request.native_only:
            allowed = [c for c in allowed if self.request.target.via(c, project_type) is None]
        if not allowed:
            allowed = self._bridge_channel(project_id, project_type)
        if floor is not None:
            return self._take_from_floor(project_id, project_type, floor, allowed, reason, requester)
        if not allowed and project_id in self.request.channel_exceptions:
            allowed = self._excepted(project_id, project_type)
        if not allowed:
            fitting = [c for c in self.catalog.by_project[project_id]
                       if self.request.target.fits(c, project_type)]
            best_fit = min(fitting, key=lambda c: self._rank(c, project_type)) if fitting else None
            alternative = None if best_fit is None else Alternative(
                version_id=best_fit.version_id, version_number=best_fit.version_number,
                channel=best_fit.version_type, published=best_fit.date_published)
            self.problems.append(Problem(
                kind="unresolvable", project_id=project_id, requested_by=_asked(requester),
                alternative=alternative, detail=(
                    f"sin versión para {self.request.target.game_version} con {self.request.target.loader} "
                    f"en los canales {', '.join(sorted(self.request.target.channels))}; pedido por {requester}")))
            return None
        best = min(allowed, key=lambda c: self._rank(c, project_type))
        if self.request.target.via(best, project_type) is not None and self._native_parent(requester):
            self.problems.append(Problem(kind="bridge_dependency", project_id=project_id, requested_by=_asked(requester),
                detail=f"{project_id} solo existe para {', '.join(sorted(best.loaders))}; la pide {requester}"))
            return None
        return self._take(best, reason, requester, exact=False)

    def _take_from_floor(self, project_id: str, project_type: str, floor: Candidate, allowed: list[Candidate],
                         reason: str, requester: str) -> Optional[Candidate]:
        """Never back: the best of the versions at or after the floor, the pack's channels first and
        then its exception's; with none, the floor itself, whatever its channel."""
        def newer(found: list[Candidate]) -> list[Candidate]:
            return [c for c in found if _as_utc(c.date_published) >= _as_utc(floor.date_published)]

        target = self.request.target
        native_only = project_id in self.request.native_only
        floor_via = None if native_only else target.via(floor, project_type)

        def same_way(found: list[Candidate]) -> list[Candidate]:
            return [c for c in found if target.via(c, project_type) == floor_via]

        pool = same_way(newer(allowed))
        if not pool and project_id in self.request.channel_exceptions:
            pool = same_way(newer(self._excepted(project_id, project_type)))
        if native_only and not pool:
            # Never back (d-7985ee-6dc34c): with only older native versions it keeps the installed one.
            older = bool(same_way(allowed))
            self.problems.append(Problem(kind="via_change", project_id=project_id, requested_by=_asked(requester),
                detail=(f"{project_id} pasó a nativa, pero su versión nativa es anterior a la instalada" if older
                        else f"{project_id} pasó a nativa, pero no tiene versión nativa en los canales del pack")))
        if floor_via is not None:
            natives = [c for c in allowed if target.via(c, project_type) is None]
            if natives:
                native = min(natives, key=lambda c: self._rank(c, project_type))
                caught_up = _as_utc(native.date_published) >= _as_utc(floor.date_published)
                (self.native_available if caught_up else self.native_behind)[project_id] = native.version_id
        best = min(pool, key=lambda c: self._rank(c, project_type)) if pool else floor
        if not self.request.target.allows(best, project_type):
            self.excepted.add(project_id)
        else:
            self.excepted.discard(project_id)
        return self._take(best, reason, requester, exact=False)

    def _excepted(self, project_id: str, project_type: str) -> list[Candidate]:
        """The fitting versions the project's channel exception accepts: its channel and the more
        stable ones. Only asked when none fits in the pack's channels."""
        limit = CHANNEL_RANK.get(self.request.channel_exceptions[project_id], -1)
        accepted = [c for c in self.catalog.by_project[project_id] if self.request.target.fits(c, project_type)
                    and CHANNEL_RANK.get(c.version_type, 99) <= limit]
        if accepted:
            self.excepted.add(project_id)
        return accepted

    def _choose_exact(self, project_id: Optional[str], version_id: str,
                      requester: str, reason: str) -> Optional[Candidate]:
        if version_id in self.catalog.gone:
            self.problems.append(Problem(kind="unresolvable", project_id=project_id,
                requested_by=_asked(requester), pinned=False, detail=(
                f"{requester} pide {version_id}, que Modrinth ya no tiene")))
            return None
        candidate = self.catalog.by_id.get(version_id)
        if candidate is None:
            self.needs_versions.add(version_id)
            return None
        project_id = candidate.project_id
        if project_id not in self.catalog.types:
            self.needs_types.add(project_id)
            return None
        if not self.request.target.fits(candidate, self.catalog.types[project_id]):
            self.problems.append(Problem(kind="pin_mismatch", project_id=project_id,
                requested_by=_asked(requester), pinned=False, detail=(
                f"{requester} pide {version_id}, que no es para "
                f"{self.request.target.game_version} con {self.request.target.loader}")))
            return None
        pin = self.exact.get(project_id)
        if pin is not None and pin.version_id != version_id:
            self.problems.append(Problem(kind="pin_conflict", project_id=project_id,
                requested_by=_asked(requester), pinned=ADMIN in pin.requested_by, detail=(
                f"{', '.join(pin.requested_by)} quiere {pin.version_id}; {requester} quiere {version_id}")))
            return None
        chosen = self.chosen.get(project_id)
        if chosen is not None:
            if chosen.candidate.version_id == version_id:
                self._requested_by(project_id, requester)
                if pin is not None and requester not in pin.requested_by:
                    pin.requested_by.append(requester)
                return None
            # Chosen freely, now required exactly: start over with this version fixed.
            raise _Restart(project_id, _Exact(version_id, [requester]))
        self.exact[project_id] = _Exact(version_id, [requester])
        return self._take(candidate, reason, requester, exact=True)

    def _rank(self, candidate: Candidate, project_type: str) -> tuple[bool, int, float]:
        """Native first (d-7985ee-09c74f), then the most stable channel, then the newest."""
        return (self.request.target.via(candidate, project_type) is not None, *_preference(candidate))

    def _take(self, candidate: Candidate, reason: str, requester: str, exact: bool) -> Candidate:
        self.chosen[candidate.project_id] = _Chosen(
            candidate, reason, [] if requester == ADMIN else [requester], exact)
        if candidate.file is None:
            self.problems.append(Problem(kind="no_file", project_id=candidate.project_id,
                                         requested_by=_asked(requester),
                                         detail=f"{candidate.version_id} no tiene archivo"))
        return candidate

    def _native_parent(self, requester: str) -> bool:
        """Whether the requirement comes from a mod chosen natively (not the admin, not a bridged mod)."""
        parent = self.chosen.get(requester)
        return parent is not None and self.request.target.via(
            parent.candidate, self.catalog.types.get(requester, "mod")) is None

    def _bridge_off(self, candidate: Candidate, project_type: str) -> Optional[Bridge]:
        """The bridge of this loader and version, not turned on, that a version which does not fit
        natively would run through: how a mod installed through a bridge is told apart once the pack
        turns it off. None for a mod's other versions, or any other type."""
        target = self.request.target
        if project_type != "mod" or target.game_version not in candidate.game_versions \
                or target.loaders(project_type) & set(candidate.loaders):
            return None
        return next((b for b in bridges_for(target.loader, target.game_version)
                     if b.key not in target.bridges and b.guest in candidate.loaders), None)

    def _bridge_channel(self, project_id: str, project_type: str) -> list[Candidate]:
        """A bridge's own mod takes the channel the bridge table accepts for it (Connector is only
        ever beta), whatever the pack's channels."""
        target = self.request.target
        for active in target._active_bridges():
            for companion, channel in companions(active, target.game_version):
                if companion == project_id:
                    limit = CHANNEL_RANK.get(channel, 0)
                    return [c for c in self.catalog.by_project[project_id] if target.fits(c, project_type)
                            and CHANNEL_RANK.get(c.version_type, 99) <= limit]
        return []

    def _bridge_problems(self) -> None:
        """What the bridges in use need and the resolution never adds itself (d-7985ee-80ee9a): each
        companion missing, and Fabric API next to Connector."""
        target = self.request.target
        used = {target.via(c.candidate, self.catalog.types.get(p, "mod")) for p, c in self.chosen.items()} - {None}
        for key in sorted(k for k in used if k is not None):
            found = find_bridge(key, target.loader, target.game_version)
            if found is None:
                continue
            for companion, _ in companions(found, target.game_version):
                if companion not in self.chosen:
                    self.problems.append(Problem(kind="bridge_missing", project_id=companion,
                        detail=f"{found.label} necesita {companion} para correr los mods de {found.guest}"))
        if "connector" in target.bridges and FABRIC_API in self.chosen and find_bridge(
                "connector", target.loader, target.game_version) is not None:
            self.problems.append(Problem(kind="bridge_conflict", project_id=FABRIC_API,
                detail="Fabric API no convive con Forgified Fabric API, que Connector usa en su lugar"))

    def _only_bridged(self, project_id: str) -> bool:
        """Whether every known version of the project that fits runs only through a bridge."""
        kind = self.catalog.types.get(project_id, "mod")
        fitting = [c for c in self.catalog.by_project.get(project_id, []) if self.request.target.fits(c, kind)]
        return bool(fitting) and all(self.request.target.via(c, kind) is not None for c in fitting)

    def _requested_by(self, project_id: str, requester: str) -> None:
        requested_by = self.chosen[project_id].requested_by
        if requester != ADMIN and requester not in requested_by:
            requested_by.append(requester)

    def _dependencies(self, candidate: Candidate) -> list[tuple[Optional[str], Optional[str], str, str]]:
        work: list[tuple[Optional[str], Optional[str], str, str]] = []
        parent = candidate.project_id
        for dependency in candidate.dependencies:
            kind = dependency.dependency_type
            if kind == "incompatible":
                self.incompatible.append((parent, dependency))
            elif kind == "optional" and dependency.project_id:
                self.suggested.setdefault(dependency.project_id, []).append(parent)
            elif kind == "required":
                work += self._required(parent, dependency)
            # embedded: shipped inside the parent's jar, never downloaded.
        return work

    def _required(self, parent: str, dependency: CandidateDependency) -> list[tuple[Optional[str], Optional[str], str, str]]:
        project_id, version_id = dependency.project_id, dependency.version_id
        if project_id is None and version_id is not None:
            known = self.catalog.by_id.get(version_id)
            project_id = known.project_id if known else None
        if project_id is not None and project_id in self.request.replacements:
            substitute = self.request.replacements[project_id]
            if substitute is None:
                return []
            return [(substitute, None, parent, f"reemplaza a {project_id}, requerida por {parent}")]
        for active in self.request.target._active_bridges():
            if project_id is not None and project_id in REPLACES.get(active.key, {}):
                project_id, version_id = REPLACES[active.key][project_id], None
        if project_id is None and version_id is None:
            self.problems.append(Problem(kind="unresolvable", project_id=None, requested_by=_asked(parent), detail=(
                f"{parent} requiere una dependencia sin proyecto ni versión ({dependency.file_name})")))
            return []
        return [(project_id, version_id, parent, f"requerida por {parent}")]

    def _check_incompatible(self) -> None:
        for parent, dependency in self.incompatible:
            target = dependency.project_id
            if target is None and dependency.version_id is not None:
                known = self.catalog.by_id.get(dependency.version_id)
                target = known.project_id if known else None
            chosen = self.chosen.get(target) if target else None
            if parent not in self.chosen or chosen is None:
                continue
            if dependency.version_id is None or chosen.candidate.version_id == dependency.version_id:
                self.problems.append(Problem(kind="incompatible", project_id=target,
                                             conflict_with=parent,
                                             detail=f"{parent} es incompatible con {target}"))

    def _lock(self) -> Lock:
        decisions = self.request.server_decisions
        entries = []
        for project_id, chosen in self.chosen.items():
            server = server_inclusion(chosen.candidate.environment, self.catalog.types.get(project_id, "mod"))
            if server == "admin" and project_id in decisions:
                server = "include" if decisions[project_id] else "exclude"
            entries.append(LockEntry(
                project_id=project_id, origin=self.request.origins.get(project_id) if project_id in self.request.projects else None,
                version_id=chosen.candidate.version_id, project_type=self.catalog.types.get(project_id, "mod"),
                version_number=chosen.candidate.version_number, file=chosen.candidate.file,
                environment=chosen.candidate.environment, server=server, reason=chosen.reason,
                requested_by=chosen.requested_by,
                via=self.request.target.via(chosen.candidate, self.catalog.types.get(project_id, "mod"))))
        for project_id, chosen in self.chosen.items():
            if self.catalog.types.get(project_id) != "shader":
                continue
            loaders = [l for l in chosen.candidate.loaders if l in SHADER_LOADER_MODS]
            if loaders and not any(SHADER_LOADER_MODS[l] in self.chosen for l in loaders):
                self.suggested.setdefault(SHADER_LOADER_MODS[loaders[0]], []).append(project_id)
        self._bridge_problems()
        suggestions = [Suggestion(project_id=p, requested_by=r) for p, r in self.suggested.items()
                       if p not in self.chosen and not self._only_bridged(p)]
        return Lock(entries=entries, problems=self.problems, suggestions=suggestions,
                    needs_projects=self.needs_projects, needs_types=self.needs_types,
                    needs_versions=self.needs_versions, needs_bridged=self.needs_bridged,
                    native_available=self.native_available, native_behind=self.native_behind,
                    excepted=[p for p in self.chosen if p in self.excepted])


def resolve(request: ResolveRequest, catalog: Catalog) -> Lock:
    """
    Resolve a modpack against a catalog.

    Greedy, as the research proposes: every requirement takes its best candidate, an exact
    requirement takes its version, and two exact requirements that differ are a problem in the
    lock (d-7985ee-20b06b). A free choice that meets an exact requirement later starts the pass
    over with that version fixed; that only ever fixes versions, so it ends.

    :param request: What the admin asked for: target, projects, pins, replacements, decisions.
    :param catalog: The versions known so far.
    :return: The lock. If it is not `complete`, fetch what it needs and call again.
    """
    exact = {project: _Exact(version, [ADMIN]) for project, version in request.pins.items()}
    for _ in range(len(catalog.by_id) + 1):
        try:
            return _Run(request, catalog, exact).run()
        except _Restart as restart:
            exact[restart.project_id] = restart.exact
    raise RuntimeError("resolution did not converge")
