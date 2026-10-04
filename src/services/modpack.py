import asyncio
import logging
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from pydantic import BaseModel
from src.database import ModpackDatabase, ModrinthDatabase, PterodactylDatabase
from src.library.api.client.loaders import LoaderMetaAPI
from src.library.api.client.modrinth import ModrinthAPI
from src.library.changes import ChangeSet, diff_entries
from src.library.mrpack import mrpack_index, write_mrpack
from src.services.jobs import NotificationSink
from src.library.resolution import Candidate, Catalog, Lock, ResolveRequest, Target, merge_replacements, request_digest, resolve
from src.model.modpack import AvailableUpdate, UpdateCheck, ModpackBuild, ModpackBundle, ModpackBundlePatch, ModpackReplacement, ModpackResolution, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.model.audit import AuditTarget
from src.model.users import Actor
from src.services.audit import AuditSink
from src.services.modrinth import FILL_DEADLINE, fill_catalog
from src.services.pterodactyl import SCAN_DIRECTORY
from src.exceptions import *

# Each round fetches what the lock needs; a dependency chain deeper than this is reported as an
# incomplete lock instead of fetching forever.
RESOLVE_ROUNDS = 32
# What a Modrinth project id looks like; anything else is not a key to store.
MODRINTH_ID = re.compile(r"[A-Za-z0-9_-]+")
# One request decides at most this many mods: a bigger one is a mistake or abuse.
MAX_DECISIONS = 500

def candidate(version: ModrinthVersion) -> Candidate:
    return Candidate.from_modrinth({**version.model_dump(mode="json"), "id": version.version_id})

logger = logging.getLogger("modpack.changes")
# How many changelogs are asked of Modrinth at once.
CHANGELOG_CONCURRENCY = 8
# How long a preview waits for changelogs in all (N-R2): Modrinth rate-limiting may back off for a
# minute per request, and a page about a game server must not wait for it.
CHANGELOG_DEADLINE = 10.0


class ChangePreview(BaseModel):
    """What changes between two locks, with the changelog of each version that arrives."""
    changes: ChangeSet
    # Version id -> Markdown, or None if Modrinth has no text for it. Versions in `failed` are absent.
    changelogs: dict[str, Optional[str]]
    # Versions whose changelog could not be read (Modrinth down, or it does not know the version).
    failed: set[str]
    # Versions whose changelog was not fetched before the deadline: asking again may work.
    timed_out: set[str] = set()
    # Version id -> version number, for the ids in `changes`; an id without one is shown as itself.
    numbers: dict[str, str] = {}


@dataclass(frozen=True)
class ModRow:
    """One mod of a pack's list, with what its card shows (spec §1). Fields from the catalog fall
    back to empty when Modrinth does not have the project; the support flags are then None."""
    project_id: str
    title: str
    icon_url: Optional[str]
    description: str
    downloads: int
    categories: list[str]
    loaders: list[str]
    game_versions: list[str]
    # Whether the project runs on the pack's loader and Minecraft version; None if unknown.
    supports_loader: Optional[bool]
    supports_version: Optional[bool]
    # "admin" or "pack"; None for a dependency.
    origin: Optional[str]
    # Titles of the projects that asked for it (the id when the catalog lacks one).
    required_by: list[str]
    # The entry's `server` ("include", "exclude", "admin"); None without a saved resolution.
    side: Optional[str]
    # version_number in the latest build, and the newer one the last check found (never for a pin).
    build_version: Optional[str]
    newer_version: Optional[str]
    pinned: bool
    source_url: Optional[str] = None
    issues_url: Optional[str] = None


@dataclass
class ModList:
    rows: list[ModRow]
    resolution: Optional[ModpackResolution]
    stale: bool
    # Rows still without an icon after filling the catalog.
    icons_missing: int
    # A title for every row and every project the resolution's suggestions and problems name (the
    # id when the catalog lacks it), from the same single fill: a page never fills twice.
    titles: dict[str, str] = field(default_factory=dict)


@dataclass
class VersionOption:
    version_id: str
    version_number: str
    channel: str
    published: datetime


class ModpackService():
    def __init__(self,
            modpackDB: ModpackDatabase,
            modrinthDB: ModrinthDatabase,
            modrinthAPI: ModrinthAPI,
            loaderAPI: LoaderMetaAPI,
            audit: AuditSink,
            notifier: Optional[NotificationSink] = None,
            pterodactylDB: Optional[PterodactylDatabase] = None,
            changelog_deadline: float = CHANGELOG_DEADLINE,
            icon_deadline: float = FILL_DEADLINE,
            ) -> None:
        self.pterodactylDB = pterodactylDB or PterodactylDatabase()
        self.changelog_deadline = changelog_deadline
        self.icon_deadline = icon_deadline
        self.modpackDB = modpackDB
        self.modrinthDB = modrinthDB
        self.modrinthAPI = modrinthAPI
        self.loaderAPI = loaderAPI
        self.audit = audit
        self.notifier = notifier

    async def bundle_find(self) -> list[ModpackBundle]:
        return await self.modpackDB.bundle_find().to_list()

    async def bundle_create(self, bundle: ModpackBundle, actor: Actor) -> ModpackBundle:
        created = await self.modpackDB.bundle_insert(bundle=bundle)
        await self.audit.record(actor, "bundle.create", _bundle(str(created.id)))
        return created

    async def bundle_get(self, bundle_id: str) -> ModpackBundle:
        return await self.modpackDB.bundle_get(bundle_id=bundle_id)

    async def bundle_update(self, bundle_id: str, patch: ModpackBundlePatch, actor: Actor) -> ModpackBundle:
        updated = await self.modpackDB.bundle_update(bundle_id=bundle_id, patch=patch)
        await self.audit.record(actor, "bundle.update", _bundle(bundle_id))
        return updated

    async def decision_set(self, bundle_id: str, project_id: str, server: bool, actor: Actor) -> ModpackBundle:
        """Decide whether one mod goes on the server; only that decision changes, atomically.
        422 for an id that is not a Modrinth project id: it would become a nested key."""
        if not MODRINTH_ID.fullmatch(project_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{project_id}' no es un id de proyecto de Modrinth")
        updated = await self.modpackDB.bundle_decision_set(bundle_id, project_id, server)
        await self.audit.record(actor, "bundle.decision", _bundle(bundle_id),
                                {"project_id": project_id, "server": server})
        return updated

    async def decisions_set(self, bundle_id: str, decisions: dict[str, bool], actor: Actor) -> ModpackBundle:
        """Decide several mods at once, in one atomic write. 422 if there is none or any id is not a
        Modrinth project id: every id is checked first, so nothing is written for a bad batch."""
        if not decisions:
            raise HTTP_422_UNPROCESSABLE_ENTITY("No hay ninguna decisión que guardar")
        if len(decisions) > MAX_DECISIONS:
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"Demasiadas decisiones de una vez (máximo {MAX_DECISIONS})")
        for project_id in decisions:
            if not MODRINTH_ID.fullmatch(project_id):
                raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{project_id}' no es un id de proyecto de Modrinth")
        updated = await self.modpackDB.bundle_decisions_set(bundle_id, decisions)
        await self.audit.record(actor, "bundle.decision", _bundle(bundle_id), {"decisions": decisions})
        return updated

    async def pin_set(self, bundle_id: str, project_id: str, version_id: str, actor: Actor) -> ModpackBundle:
        """Pin one mod of the bundle to a version, atomically; only that pin changes.

        422 for an id that is not a Modrinth id (it would become a nested key), a project the
        bundle does not have, or a version that is not of that project: the catalog is asked first,
        Modrinth if the catalog does not have it. Nothing is written for a refused pin.
        """
        for value in (project_id, version_id):
            if not MODRINTH_ID.fullmatch(value):
                raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{value}' no es un id de Modrinth")
        bundle = await self.modpackDB.bundle_get(bundle_id)
        if project_id not in {p.project_id for p in bundle.projects}:
            raise HTTP_422_UNPROCESSABLE_ENTITY("Ese proyecto no está en el modpack")
        if await self._version_project(version_id) != project_id:
            raise HTTP_422_UNPROCESSABLE_ENTITY("La versión no es de ese proyecto")
        updated = await self.modpackDB.bundle_pin_set(bundle_id, project_id, version_id)
        await self.audit.record(actor, "bundle.pin", _bundle(bundle_id),
                                {"project_id": project_id, "version_id": version_id})
        return updated

    async def pin_unset(self, bundle_id: str, project_id: str, actor: Actor) -> ModpackBundle:
        """Remove the pin of one mod; only that pin changes. 422 for an id that is not a Modrinth id."""
        if not MODRINTH_ID.fullmatch(project_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{project_id}' no es un id de Modrinth")
        updated = await self.modpackDB.bundle_pin_unset(bundle_id, project_id)
        await self.audit.record(actor, "bundle.pin", _bundle(bundle_id),
                                {"project_id": project_id, "version_id": None})
        return updated

    async def _version_project(self, version_id: str) -> Optional[str]:
        """The project a version belongs to: from the catalog, else from Modrinth (which does not
        know it: None)."""
        stored = await self.modrinthDB.version_find_by_version_id(version_id)
        if stored is not None:
            return stored.project_id
        try:
            return (await self.modrinthAPI.version_get(version_id)).project_id
        except HTTPException as error:
            if error.status_code == 404:
                return None
            raise

    # Content (i-7985ee-063a3a).

    async def project_find(self, bundle_id: str) -> list[PackProject]:
        return (await self.modpackDB.bundle_get(bundle_id)).projects

    async def project_add(self, bundle_id: str, refs: list[str], origin: ProjectOrigin, actor: Actor) -> list[PackProject]:
        """
        Add Modrinth projects to a bundle, by slug or id, marked with their origin.

        All or nothing: a reference Modrinth does not know refuses the whole request.

        :param refs: Modrinth slugs or project ids.
        :param origin: Whether the pack brings them or the admin adds them.
        :return: The bundle's projects afterwards.
        """
        await self.modpackDB.bundle_get(bundle_id)
        projects = await self._modrinth_projects(refs)
        added = await self.modpackDB.bundle_projects_add(
            bundle_id, [PackProject(project_id=p.project_id, origin=origin) for p in projects])
        await self.audit.record(actor, "bundle.project.add", _bundle(bundle_id),
                                {"refs": list(refs), "origin": origin.value})
        return added

    async def pack_replace(self, bundle_id: str, refs: list[str], actor: Actor) -> list[PackProject]:
        """
        Replace what the pack brings with `refs`; what the admin added stays (d-7985ee-b15dd4).

        :return: The bundle's projects afterwards.
        """
        await self.modpackDB.bundle_get(bundle_id)
        projects = await self._modrinth_projects(refs)
        replaced = await self.modpackDB.bundle_pack_replace(
            bundle_id, [PackProject(project_id=p.project_id, origin=ProjectOrigin.pack) for p in projects])
        await self.audit.record(actor, "bundle.project.replace", _bundle(bundle_id), {"refs": list(refs)})
        return replaced

    async def project_remove(self, bundle_id: str, project_id: str, actor: Actor) -> list[PackProject]:
        remaining = await self.modpackDB.bundle_project_remove(bundle_id, project_id)
        await self.audit.record(actor, "bundle.project.remove", _bundle(bundle_id), {"project_id": project_id})
        return remaining

    async def _modrinth_projects(self, refs: list[str]) -> list[ModrinthProject]:
        """The Modrinth projects these slugs or ids name, stored in the catalog; 404 naming any
        that Modrinth does not know."""
        found = list(await self.modrinthAPI.project_get_many(list(refs))) if refs else []
        known = {key for p in found for key in (p.project_id, p.slug)}
        missing = [ref for ref in refs if ref not in known]
        if missing:
            raise HTTP_404_NOT_FOUND(f"Modrinth has no project {', '.join(missing)}")
        return await self.modrinthDB.project_upsert_many(found)

    async def mod_list(self, bundle_id: str, check: Optional[UpdateCheck]) -> ModList:
        """
        The pack's mods as one list: its projects in order, then the dependencies of the latest
        saved resolution by title, each with its card data. Without a resolution, only the pack's
        projects. The catalog is filled first, in one call within `icon_deadline` (best effort), for
        the missing icons and for the projects the resolution's suggestions and problems name: a
        Modrinth that is down gives the list without them, never an error. Writes the catalog.

        :param check: The latest update check, for the newer versions; None shows none.
        """
        bundle = await self.modpackDB.bundle_get(bundle_id)
        saved = await self.resolution(bundle_id)
        resolution, stale = saved if saved else (None, False)
        entries = {e.project_id: e for e in resolution.lock.entries} if resolution else {}
        in_build: dict[str, str] = {}
        if bundle.latest_build is not None:
            build = await self.modpackDB.build_get(bundle_id, bundle.latest_build)
            in_build = {e.project_id: e.version_number for e in build.lock.entries}
        pack = {p.project_id: p.origin.value for p in bundle.projects}
        dependencies = [i for i in entries if i not in pack]
        requesters = {r for e in entries.values() for r in e.requested_by}
        named: list[str] = []
        if resolution is not None:
            named = [x.project_id for x in resolution.lock.suggestions]
            named += [r for x in resolution.lock.suggestions for r in x.requested_by]
            named += [x.project_id for x in resolution.lock.problems if x.project_id]
        catalog = await fill_catalog(self.modrinthDB, self.modrinthAPI,
                                     [*pack, *dependencies, *sorted(requesters), *named], self.icon_deadline)
        newer = {u.project_id: u.to_version for u in check.updates if not u.pinned} if check else {}
        target = Target(game_version=bundle.version, loader=str(bundle.loader.value), channels=frozenset(bundle.channels))

        def row(project_id: str, origin: Optional[str]) -> ModRow:
            found, entry = catalog.get(project_id), entries.get(project_id)
            pinned = project_id in bundle.pins
            supports_loader = supports_version = None
            if found is not None:
                kind = entry.project_type if entry else found.project_type.value
                supports_loader = bool(target.loaders(kind) & set(found.loaders))
                supports_version = bundle.version in found.game_versions
            return ModRow(
                project_id=project_id, title=found.title if found else project_id,
                icon_url=found.icon_url if found else None, description=found.description if found else "",
                downloads=found.downloads if found else 0, categories=list(found.categories) if found else [],
                loaders=list(found.loaders) if found else [], game_versions=list(found.game_versions) if found else [],
                supports_loader=supports_loader, supports_version=supports_version, origin=origin,
                required_by=[catalog[r].title if r in catalog else r for r in (entry.requested_by if entry else [])],
                side=entry.server if entry else None, build_version=in_build.get(project_id),
                newer_version=None if pinned else newer.get(project_id), pinned=pinned,
                source_url=found.source_url if found else None, issues_url=found.issues_url if found else None)

        rows = [row(i, origin) for i, origin in pack.items()]
        rows += sorted((row(i, None) for i in dependencies), key=lambda r: r.title)
        titles = {i: catalog[i].title if i in catalog else i for i in named}
        titles.update({r.project_id: r.title for r in rows})
        return ModList(rows=rows, resolution=resolution, stale=stale,
                       icons_missing=sum(1 for r in rows if r.icon_url is None), titles=titles)

    async def project_versions(self, bundle_id: str, project_id: str) -> list[VersionOption]:
        """
        The versions of one project the admin can pin it to: those for the pack's Minecraft version
        and the loaders its resolution accepts for the project's type, in the channels the pack
        allows, newest first. Asked of Modrinth and stored in the catalog. 422 for an id that is
        not a Modrinth id.
        """
        if not MODRINTH_ID.fullmatch(project_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{project_id}' no es un id de Modrinth")
        bundle = await self.modpackDB.bundle_get(bundle_id)
        stored = await self.modrinthDB.project_find_by_modrinth_id([project_id])
        kind = stored[0].project_type.value if stored else "mod"
        target = Target(game_version=bundle.version, loader=str(bundle.loader.value), channels=frozenset(bundle.channels))
        found = list(await self.modrinthAPI.project_versions(project_id, sorted(target.loaders(kind)), [bundle.version]))
        if found:
            await self.modrinthDB.version_upsert_many(found)
        allowed = [v for v in found if v.version_type.value in bundle.channels and v.status.value == "listed"]
        allowed.sort(key=lambda v: v.date_published, reverse=True)
        return [VersionOption(version_id=v.version_id, version_number=v.version_number,
                              channel=v.version_type.value, published=v.date_published) for v in allowed]

    # Resolution (i-7985ee-7f735a).

    async def resolve(self, bundle_id: str) -> Lock:
        """
        Resolve a bundle into a lock, fetching from Modrinth what it needs.

        The latest resolution is saved to show it, never as the input of a build: build_create
        resolves again and freezes that lock. Every project and version fetched is stored in the
        catalog.

        :param bundle_id: The ID of the bundle.
        :return: The lock; `complete` is False only if the dependency chain ran past RESOLVE_ROUNDS.
        """
        bundle = await self.modpackDB.bundle_get(bundle_id)
        request = await self._request(bundle)
        lock = await self._resolve(request)
        await self._resolution_save(bundle, request, lock)
        return lock

    async def resolution(self, bundle_id: str) -> Optional[tuple[ModpackResolution, bool]]:
        """
        The bundle's latest saved resolution and whether it is stale, or None if there is none.

        Stale: the bundle's request now has another digest than the one that produced it. For
        display only: no build reads it.
        """
        bundle = await self.modpackDB.bundle_get(bundle_id)
        saved = await self.modpackDB.resolution_get(bundle.id)  # type: ignore[arg-type]
        if saved is None:
            return None
        return saved, saved.request_digest != request_digest(await self._request(bundle))

    async def _resolution_save(self, bundle: ModpackBundle, request: ResolveRequest, lock: Lock) -> None:
        await self.modpackDB.resolution_save(ModpackResolution(
            bundle_id=bundle.id, resolved_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
            request_digest=request_digest(request), lock=lock))

    async def _request(self, bundle: ModpackBundle) -> ResolveRequest:
        global_rules = {r.project_id: r.replacement for r in await self.modpackDB.replacement_find()}
        return ResolveRequest(
            target=Target(game_version=bundle.version, loader=str(bundle.loader.value),
                          channels=frozenset(bundle.channels)),
            projects=[p.project_id for p in bundle.projects],
            origins={p.project_id: p.origin.value for p in bundle.projects},
            pins=bundle.pins, server_decisions=bundle.server_decisions,
            replacements=merge_replacements(global_rules, bundle.replacements))

    async def _resolve(self, request: ResolveRequest) -> Lock:
        catalog = Catalog()
        for _ in range(RESOLVE_ROUNDS):
            lock = resolve(request, catalog)
            if lock.complete:
                return lock
            fetched: list[ModrinthVersion] = []
            await self._fetch_types(catalog, sorted(lock.needs_projects | lock.needs_types))
            for project_id in sorted(lock.needs_projects):
                loaders = sorted(request.target.loaders(catalog.types.get(project_id, "mod")))
                fetched += await self.modrinthAPI.project_versions(project_id, loaders, [request.target.game_version])
                catalog.by_project.setdefault(project_id, [])
            if lock.needs_versions:
                asked = sorted(lock.needs_versions)
                versions = list(await self.modrinthAPI.version_get_many(asked))
                catalog.gone |= set(asked) - {v.version_id for v in versions}
                fetched += versions
            for version in fetched:
                catalog.add(candidate(version))
            if fetched:
                await self.modrinthDB.version_upsert_many(fetched)
        return lock

    async def _fetch_types(self, catalog: Catalog, project_ids: list[str]) -> None:
        """Fetch the projects whose type resolution needs, and store them in the catalog. One
        Modrinth does not return is typed unknown, which resolution reports."""
        if not project_ids:
            return
        projects = list(await self.modrinthAPI.project_get_many(project_ids))
        for project in projects:
            catalog.types[project.project_id] = project.project_type.value
        for project_id in set(project_ids) - {p.project_id for p in projects}:
            catalog.types[project_id] = "unknown"
        if projects:
            await self.modrinthDB.project_upsert_many(projects)

    # Builds (i-7985ee-098611).

    async def build_create(self, bundle_id: str, actor: Actor) -> ModpackBuild:
        """
        Resolve the bundle and freeze the lock as its next build; latest moves to it.

        Refused with 409 when the lock is not complete, has problems, or has mods whose place on
        the server waits for the admin: a build is something that can be deployed as it is.

        :return: The new build.
        """
        bundle = await self.modpackDB.bundle_get(bundle_id)
        request = await self._request(bundle)
        lock = await self._resolve(request)
        await self._resolution_save(bundle, request, lock)
        if not lock.complete or lock.problems or lock.undecided:
            reasons = [p.detail for p in lock.problems]
            reasons += [f"{e.project_id}: falta decidir si va en el servidor" for e in lock.undecided]
            if not lock.complete:
                reasons.append("la resolución no terminó")
            raise BuildBlocked("No se puede congelar este lock: " + "; ".join(reasons), lock,
                               only_undecided=lock.complete and not lock.problems and bool(lock.undecided))
        loader_version = bundle.loader_version or await self.loaderAPI.loader_version(
            str(bundle.loader.value), bundle.version)
        builds = await self.modpackDB.build_find(bundle_id)
        number = (builds[0].number if builds else 0) + 1
        build = await self.modpackDB.build_insert(ModpackBuild(
            bundle_id=bundle.id, number=number, created_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
            loader_version=loader_version, local_files=bundle.local_files, request=request, lock=lock))
        await self.modpackDB.bundle_latest_build(bundle_id, number)
        await self.audit.record(actor, "build.create", _bundle(bundle_id), {"number": number})
        if self.notifier is not None:
            await self.notifier.notify(f"📦 {bundle.name}: build {number} creado, {len(lock.entries)} archivos.")
        return build

    async def build_find(self, bundle_id: str) -> list[ModpackBuild]:
        return await self.modpackDB.build_find(bundle_id)

    async def latest_builds(self) -> list[tuple[ModpackBundle, Optional[ModpackBuild]]]:
        """Every bundle with its newest build, or None if it has none yet."""
        bundles = await self.modpackDB.bundle_find().to_list()
        latest = await self.modpackDB.build_find_latest(bundles)
        return [(bundle, latest.get(bundle.id) if bundle.id is not None else None) for bundle in bundles]

    async def build_get(self, bundle_id: str, number: int) -> ModpackBuild:
        return await self.modpackDB.build_get(bundle_id, number)

    async def build_mrpack(self, bundle_id: str, number: int) -> tuple[str, bytes]:
        """
        A build as a .mrpack for players (i-7985ee-7352b3): the same files the server gets, by
        their Modrinth URL, validated against the format before it is returned.

        :return: A file name and the archive. 409 if the build has no loader version, or if the
            index would break the format.
        """
        bundle = await self.modpackDB.bundle_get(bundle_id)
        build = await self.modpackDB.build_get(bundle_id, number)
        if build.loader_version is None:
            raise HTTP_409_CONFLICT(f"Build {number} has no {build.request.target.loader} version: set the "
                                    "bundle's loader_version and create a new build")
        index = mrpack_index(name=bundle.name, version_id=str(number), summary=bundle.description,
                             game_version=build.request.target.game_version, loader=build.request.target.loader,
                             loader_version=build.loader_version, entries=build.lock.entries)
        try:
            data = write_mrpack(index)
        except ValueError as error:
            raise HTTP_409_CONFLICT(f"Build {number} does not make a valid .mrpack: {error}") from error
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", bundle.name).strip("-") or "modpack"
        return f"{safe}-{number}.mrpack", data

    async def build_recommend(self, bundle_id: str, number: int, actor: Actor) -> ModpackBundle:
        """Point recommended at an existing build; going back is pointing at an older one."""
        await self.modpackDB.build_get(bundle_id, number)
        bundle = await self.modpackDB.bundle_recommend_build(bundle_id, number)
        await self.audit.record(actor, "build.recommend", _bundle(bundle_id), {"number": number})
        return bundle

    # Changes between locks (N3).

    async def change_preview(self, before: Lock, after: Lock) -> ChangePreview:
        """
        What changes from one lock to another, with the changelogs of the versions that arrive.

        A changelog comes from the catalog, or else from Modrinth, and is then kept in the catalog.
        A read that fails marks its version in `failed`: the preview never raises for it. Like
        `resolve`, it writes only to the catalog of Modrinth data.
        """
        return await self._preview({e.project_id: e.version_id for e in before.entries},
                                   {e.project_id: e.version_id for e in after.entries},
                                   {e.version_id: e.version_number for e in [*before.entries, *after.entries]})

    async def _preview(self, before: dict[str, str], after: dict[str, str],
                       numbers: Optional[dict[str, str]] = None) -> ChangePreview:
        """`change_preview` on maps of project id to version id; `numbers` are the version numbers
        already known, the others are read from the catalog (never from Modrinth)."""
        changes = diff_entries(before, after)
        shown = {v for c in [*changes.added, *changes.removed, *changes.changed] for v in (c.from_version, c.to_version) if v}
        known = dict(numbers or {})
        for version_id in sorted(shown - known.keys()):
            stored = await self.modrinthDB.version_find_by_version_id(version_id)
            if stored is not None:
                known[version_id] = stored.version_number
        known = {v: n for v, n in known.items() if v in shown}
        arriving = sorted({c.to_version for c in changes.added + changes.changed if c.to_version})
        gate = asyncio.Semaphore(CHANGELOG_CONCURRENCY)

        async def fetch(version_id: str) -> tuple[str, Optional[str], bool]:
            async with gate:
                try:
                    return version_id, await self._changelog(version_id), False
                except Exception:
                    logger.warning("changelog of %s not available", version_id, exc_info=True)
                    return version_id, None, True

        tasks = {v: asyncio.create_task(fetch(v)) for v in arriving}
        done, pending = await asyncio.wait(tasks.values(), timeout=self.changelog_deadline) if tasks else (set(), set())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        changelogs: dict[str, Optional[str]] = {}
        failed: set[str] = set()
        timed_out: set[str] = set()
        for version_id, task in tasks.items():
            if task not in done:
                timed_out.add(version_id)
            elif task.cancelled() or task.result()[2]:
                failed.add(version_id)
            else:
                changelogs[version_id] = task.result()[1]
        return ChangePreview(changes=changes, changelogs=changelogs, failed=failed, timed_out=timed_out,
                             numbers=known)

    async def _changelog(self, version_id: str) -> Optional[str]:
        """The changelog of a version, None if it has none. The catalog first; else Modrinth, and
        the version is stored with it (an empty text, so a version without one is not asked again)."""
        stored = await self.modrinthDB.version_find_by_version_id(version_id)
        if stored is not None and stored.changelog is not None:
            return stored.changelog or None
        version = await self.modrinthAPI.version_get(version_id)
        version.changelog = version.changelog or ""
        await self.modrinthDB.version_upsert_many([version])
        return version.changelog or None

    async def compare_builds(self, bundle_id: str, from_number: int, to_number: int) -> ChangePreview:
        """The change from one build of the bundle to another; 404 if either does not exist."""
        before = await self.modpackDB.build_get(bundle_id, from_number)
        after = before if to_number == from_number else await self.modpackDB.build_get(bundle_id, to_number)
        return await self.change_preview(before.lock, after.lock)

    async def update_preview(self, updates: list[AvailableUpdate]) -> ChangePreview:
        """What the updates of a check change, with the changelogs of the versions that arrive. An
        update stored without version ids (before they were kept) is left out. A read."""
        usable = [u for u in updates if u.from_version_id and u.to_version_id]
        numbers = {i: n for u in usable for i, n in ((u.from_version_id, u.from_version), (u.to_version_id, u.to_version))}
        return await self._preview({u.project_id: u.from_version_id for u in usable},
                                   {u.project_id: u.to_version_id for u in usable}, numbers)

    async def server_change_preview(self, server_id: str, build_number: int) -> ChangePreview:
        """
        What updating a server to a build would change: what its latest scan identified, against
        what the build puts on the server (the entries that go there, not the client-only ones).
        409 if the server has no bundle or no scan; 404 if the build does not exist. A read.
        """
        server = await self.pterodactylDB.server_get(server_id)
        if server.bundle_id is None:
            raise HTTP_409_CONFLICT(f"Server '{server_id}' is not linked to a bundle")
        build = await self.modpackDB.build_get(server.bundle_id, build_number)
        scan = await self.pterodactylDB.scan_latest(server_id, SCAN_DIRECTORY)
        if scan is None:
            raise HTTP_409_CONFLICT("Escanea el servidor antes de comparar")
        before = {f.project_id: f.version_id for f in scan.files
                  if f.status == "identified" and f.project_id and f.version_id}
        after = {e.project_id: e.version_id for e in build.lock.entries if e.server == "include"}
        return await self._preview(before, after, {e.version_id: e.version_number for e in build.lock.entries})

    # Global replacement rules (d-7985ee-86fffa).

    async def replacement_find(self) -> list[ModpackReplacement]:
        return await self.modpackDB.replacement_find()

    async def replacement_set(self, project_id: str, replacement: Optional[str], actor: Actor) -> ModpackReplacement:
        rule = await self.modpackDB.replacement_set(ModpackReplacement(project_id=project_id, replacement=replacement))
        await self.audit.record(actor, "replacement.set", _replacement(project_id), {"replacement": replacement})
        return rule

    async def replacement_delete(self, project_id: str, actor: Actor) -> bool:
        if not await self.modpackDB.replacement_delete(project_id):
            raise HTTP_404_NOT_FOUND(f"No replacement rule for project '{project_id}'")
        await self.audit.record(actor, "replacement.delete", _replacement(project_id))
        return True

def _bundle(bundle_id: str) -> AuditTarget:
    return AuditTarget(kind="bundle", id=bundle_id)

def _replacement(project_id: str) -> AuditTarget:
    return AuditTarget(kind="replacement", id=project_id)
