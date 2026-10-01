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
from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel

ADMIN = "admin"
CHANNEL_RANK = {"release": 0, "beta": 1, "alpha": 2}
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

    def loaders(self, project_type: str) -> set[str]:
        """The Modrinth loaders a project of this type may have to run in this pack."""
        return TYPE_LOADERS.get(project_type) or LOADER_ACCEPTS.get(self.loader, {self.loader})

    def fits(self, candidate: Candidate, project_type: str = "mod") -> bool:
        """Runs on this game version and loader, and is listed. Channels aside: a mod that pins
        an exact version of another gets that version, whatever its channel."""
        accepted = self.loaders(project_type)
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


class Problem(BaseModel):
    kind: Literal["unresolvable", "pin_mismatch", "pin_conflict", "incompatible", "no_file"]
    project_id: Optional[str]
    detail: str


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

    @property
    def complete(self) -> bool:
        """Nothing left to fetch: the lock says all there is to say about this catalog."""
        return not self.needs_projects and not self.needs_types and not self.needs_versions

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
                self.problems.append(Problem(kind="unresolvable", project_id=project_id, detail=(
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
                self.problems.append(Problem(kind="pin_mismatch", project_id=project_id, detail=(
                    f"{pin.version_id}, fijada por {', '.join(pin.requested_by)}, no es para "
                    f"{self.request.target.game_version} con {self.request.target.loader}")))
                return None
            return self._take(candidate, reason, requester, exact=True)
        if project_id not in self.catalog.by_project or project_id not in self.catalog.types:
            self.needs_projects.add(project_id)
            return None
        project_type = self.catalog.types[project_id]
        if project_type not in SUPPORTED_TYPES:
            self.problems.append(Problem(kind="unresolvable", project_id=project_id, detail=(
                f"un proyecto de tipo {project_type} no entra en un modpack; pedido por {requester}")))
            return None
        allowed = [c for c in self.catalog.by_project[project_id] if self.request.target.allows(c, project_type)]
        if not allowed:
            self.problems.append(Problem(kind="unresolvable", project_id=project_id, detail=(
                f"sin versión para {self.request.target.game_version} con {self.request.target.loader} "
                f"en los canales {', '.join(sorted(self.request.target.channels))}; pedido por {requester}")))
            return None
        best = min(allowed, key=lambda c: (CHANNEL_RANK.get(c.version_type, 99), -c.date_published.timestamp()))
        return self._take(best, reason, requester, exact=False)

    def _choose_exact(self, project_id: Optional[str], version_id: str,
                      requester: str, reason: str) -> Optional[Candidate]:
        if version_id in self.catalog.gone:
            self.problems.append(Problem(kind="unresolvable", project_id=project_id, detail=(
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
            self.problems.append(Problem(kind="pin_mismatch", project_id=project_id, detail=(
                f"{requester} pide {version_id}, que no es para "
                f"{self.request.target.game_version} con {self.request.target.loader}")))
            return None
        pin = self.exact.get(project_id)
        if pin is not None and pin.version_id != version_id:
            self.problems.append(Problem(kind="pin_conflict", project_id=project_id, detail=(
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

    def _take(self, candidate: Candidate, reason: str, requester: str, exact: bool) -> Candidate:
        self.chosen[candidate.project_id] = _Chosen(
            candidate, reason, [] if requester == ADMIN else [requester], exact)
        if candidate.file is None:
            self.problems.append(Problem(kind="no_file", project_id=candidate.project_id,
                                         detail=f"{candidate.version_id} no tiene archivo"))
        return candidate

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
        if project_id is None and version_id is None:
            self.problems.append(Problem(kind="unresolvable", project_id=None, detail=(
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
                requested_by=chosen.requested_by))
        for project_id, chosen in self.chosen.items():
            if self.catalog.types.get(project_id) != "shader":
                continue
            loaders = [l for l in chosen.candidate.loaders if l in SHADER_LOADER_MODS]
            if loaders and not any(SHADER_LOADER_MODS[l] in self.chosen for l in loaders):
                self.suggested.setdefault(SHADER_LOADER_MODS[loaders[0]], []).append(project_id)
        suggestions = [Suggestion(project_id=p, requested_by=r) for p, r in self.suggested.items()
                       if p not in self.chosen]
        return Lock(entries=entries, problems=self.problems, suggestions=suggestions,
                    needs_projects=self.needs_projects, needs_types=self.needs_types,
                    needs_versions=self.needs_versions)


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
