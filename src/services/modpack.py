import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional, Sequence
from pydantic import BaseModel
from src.database import ModpackDatabase, ModrinthDatabase, PterodactylDatabase
from src.library.api.client.loaders import LoaderMetaAPI
from src.library.api.client.modrinth import ModrinthAPI
from src.library.changes import ChangeSet, diff_entries
from src.library.mrpack import mrpack_index, write_mrpack
from src.services.jobs import NotificationSink
from src.library.resolution import CHANNEL_RANK, EXCEPTION_CHANNELS, Candidate, Catalog, Lock, ResolveRequest, Target, merge_replacements, request_digest, resolve
from src.model.modpack import BUNDLE_KEYED_FIELDS, AvailableUpdate, UpdateCheck, ModpackBuild, ModpackBundle, ModpackBundlePatch, ModpackReplacement, ModpackResolution, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthProject, ModrinthVersion, ModrinthVersionList
from src.model.modrinth.version import ModrinthDependencyType
from src.model.pterodactyl import PterodactylServer
from src.model.audit import AuditTarget
from src.model.users import Actor
from src.services.audit import AuditSink
from src.services.modrinth import FILL_DEADLINE, fill_catalog
from src.services.pterodactyl import SCAN_DIRECTORY
from src.exceptions import *

# Each round fetches what the lock needs; a dependency chain deeper than this is reported as an
# incomplete lock instead of fetching forever.
RESOLVE_ROUNDS = 32
# A pack's Modrinth information is checked at most this often (E1); a build always checks (E2).
CATALOG_WINDOW = timedelta(minutes=5)
# How many ids go in one Modrinth request.
ID_CHUNK = 100
# What a Modrinth project id looks like; anything else is not a key to store.
MODRINTH_ID = re.compile(r"[A-Za-z0-9_-]+")
# One request decides at most this many mods: a bigger one is a mistake or abuse.
MAX_DECISIONS = 500
# Fields of a bundle that a patch may leave out but never set to null: the bundle would not load.
BUNDLE_NOT_NULL = ("name", "description", "version", "loader", "channels", *BUNDLE_KEYED_FIELDS)

def _chunks(ids: Sequence[str]) -> list[list[str]]:
    return [list(ids[i:i + ID_CHUNK]) for i in range(0, len(ids), ID_CHUNK)]

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
    # The ids of the projects in `required_by`, in its order: each name opens its card.
    requested_by: list[str] = field(default_factory=list)
    # "beta" or "alpha" when the catalog's versions for the pack's Minecraft version and loader are
    # all outside the pack's channels: the most stable of them (see `outside_channel`).
    version_channel: Optional[str] = None


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
    # The pinned projects no row shows (they left the pack and are not a dependency), in pin order:
    # listed apart so their pin can still be removed. `titles` names them.
    orphan_pins: list[str] = field(default_factory=list)
    # The card of each project a problem is about that no row shows (an unresolvable dependency),
    # from the same fill: "Con problemas" shows every problem with its mod's card.
    problem_cards: dict[str, ModRow] = field(default_factory=dict)


@dataclass
class VersionOption:
    version_id: str
    version_number: str
    channel: str
    published: datetime


# How many of a project's catalog versions its page shows; "Cargar todas" asks Modrinth for the rest.
MOD_PAGE_VERSIONS = 50


@dataclass(frozen=True)
class ModUse:
    """A pack that uses a mod (spec §2, "dónde se usa"): as one of its projects (`origin` "admin" or
    "pack") or as a dependency its saved resolution brought in (`origin` None, `dependency_of` the
    titles of who asked for it), with the recommended and latest builds that include the mod, as
    (build number, version number); None when that build does not include it or there is none."""
    bundle_id: str
    bundle_name: str
    origin: Optional[str]
    dependency_of: list[str]
    recommended: Optional[tuple[int, str]]
    latest: Optional[tuple[int, str]]
    # The ids of the projects in `dependency_of`, in its order: each name opens its card.
    requested_by: list[str] = field(default_factory=list)


# What a bundle id looks like (a MongoDB ObjectId): anything else names no pack.
BUNDLE_ID = re.compile(r"[0-9a-fA-F]{24}")


@dataclass(frozen=True)
class PackMark:
    """The pack a mod's page was opened from (spec §2): its Minecraft version and loader, and whether
    the project, as the catalog has it, supports them."""
    bundle_id: str
    name: str
    game_version: str
    loader: str
    supports_version: bool
    supports_loader: bool
    version_channel: Optional[str] = None


@dataclass(frozen=True)
class ServerUse:
    """A server whose latest scan identified the mod; `version` is the version number, None when the
    catalog does not know the identified version."""
    server_id: str
    server_name: str
    version: Optional[str]
    scanned_at: datetime


@dataclass(frozen=True)
class VersionRow:
    version_id: str
    version_number: str
    channel: str
    published: datetime
    game_versions: list[str]
    loaders: list[str]


@dataclass
class ModPage:
    """What a mod's local page shows: the project, where it is used, and its versions from the
    catalog (newest first, at most MOD_PAGE_VERSIONS; `versions_partial` says they are only the ones
    fetched for the packs, not all of Modrinth's). `dependencies` are the projects its catalog versions
    require; `titles` names them and every other project the page names (the id when unknown)."""
    project: ModrinthProject
    uses: list[ModUse]
    servers: list[ServerUse]
    versions: list[VersionRow]
    versions_partial: bool
    titles: dict[str, str]
    dependencies: list[str] = field(default_factory=list)
    # The pack the page was opened from, with its version and loader marked; None without one.
    pack: Optional[PackMark] = None


@dataclass
class _ModLocal:
    """What MongoDB says about a project, read before asking Modrinth anything (mod_page)."""
    versions: list[ModrinthVersion]
    dependencies: list[str]
    bundles: list[ModpackBundle]
    # Bundle id -> the ids of the projects that asked for the mod, in the bundle's saved resolution.
    requested: dict[str, list[str]]
    # (bundle id, build number) -> version number of the mod in that build.
    in_builds: dict[tuple[str, int], str]
    # (server, scanned at, identified version id), by server name.
    servers: list[tuple[PterodactylServer, datetime, Optional[str]]]
    numbers: dict[str, str]


def _related(project_id: str, local: _ModLocal, named: Sequence[str]) -> list[str]:
    """The other projects a mod's page names: its dependencies, who asked for it and `named`."""
    requesters = (r for ids in local.requested.values() for r in ids)
    return [i for i in dict.fromkeys([*local.dependencies, *requesters, *named]) if i != project_id]


def _version_row(version: ModrinthVersion) -> VersionRow:
    return VersionRow(version_id=version.version_id, version_number=version.version_number,
                      channel=version.version_type.value, published=version.date_published,
                      game_versions=list(version.game_versions), loaders=list(version.loaders))


def outside_channel(target: Target, project_type: str, versions: Sequence[ModrinthVersion]) -> Optional[str]:
    """The most stable channel among the versions that fit the pack (its Minecraft version and loader,
    listed) when none of them is in the pack's channels; None when one is, or when none fits: the
    catalog may simply not have them, and the card then keeps what the project says."""
    fitting = [v.version_type.value for v in versions if target.fits(candidate(v), project_type)]
    if not fitting or any(channel in target.channels for channel in fitting):
        return None
    return min(fitting, key=lambda channel: CHANNEL_RANK.get(channel, 99))


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
            clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
            ) -> None:
        self.clock = clock
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
        """Change the fields sent, and the maps only at the keys sent (see ModpackBundlePatch).

        422, with nothing written, for a null on a field the bundle needs (it would no longer
        load) or a map key that is not a Modrinth id (it would become a nested key).
        """
        sent = patch.model_dump(exclude_unset=True)
        for name in BUNDLE_NOT_NULL:
            if name in sent and sent[name] is None:
                raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{name}' no puede quedar vacío")
        for name in BUNDLE_KEYED_FIELDS:
            for key in sent.get(name) or {}:
                if not MODRINTH_ID.fullmatch(key):
                    raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{key}' no es un id de proyecto de Modrinth")
        updated = await self.modpackDB.bundle_update(bundle_id=bundle_id, patch=patch)
        await self.audit.record(actor, "bundle.update", _bundle(bundle_id))
        return updated

    async def bundle_replacement_delete(self, bundle_id: str, project_id: str, actor: Actor) -> ModpackBundle:
        """Remove the pack's own replacement rule for one mod; the global rule, if any, applies
        again. 422 for an id that is not a Modrinth id, 404 if the pack has no rule for it."""
        if not MODRINTH_ID.fullmatch(project_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{project_id}' no es un id de proyecto de Modrinth")
        updated = await self.modpackDB.bundle_replacement_unset(bundle_id, project_id)
        await self.audit.record(actor, "bundle.replacement.delete", _bundle(bundle_id), {"project_id": project_id})
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

        A dependency can be pinned too ("Usar la beta" on its card): a project the pack's last saved
        resolution names, as an entry or as a problem.

        422 for an id that is not a Modrinth id (it would become a nested key), a project that is
        neither the bundle's nor one its last resolution names, or a version that is not of that
        project: the catalog is asked first, Modrinth if the catalog does not have it. Nothing is
        written for a refused pin.
        """
        for value in (project_id, version_id):
            if not MODRINTH_ID.fullmatch(value):
                raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{value}' no es un id de Modrinth")
        bundle = await self.modpackDB.bundle_get(bundle_id)
        if project_id not in {p.project_id for p in bundle.projects} and not await self._resolution_names(bundle, project_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY("Ese proyecto no está en el modpack ni es una de sus dependencias")
        if await self._version_project(version_id) != project_id:
            raise HTTP_422_UNPROCESSABLE_ENTITY("La versión no es de ese proyecto")
        updated = await self.modpackDB.bundle_pin_set(bundle_id, project_id, version_id)
        await self.audit.record(actor, "bundle.pin", _bundle(bundle_id),
                                {"project_id": project_id, "version_id": version_id})
        return updated

    async def channel_exception_set(self, bundle_id: str, project_id: str, channel: str, actor: Actor) -> ModpackBundle:
        """Let one project also take versions of `channel` ("beta" or "alpha") while none of its
        versions fits in the pack's channels ("Usar la beta"); only that key changes, atomically.

        422 for an id that is not a Modrinth id, another channel, or a project that is neither the
        bundle's nor one its last resolution names, as pin_set. Nothing is written for a refusal.
        """
        if not MODRINTH_ID.fullmatch(project_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{project_id}' no es un id de Modrinth")
        if channel not in EXCEPTION_CHANNELS:
            raise HTTP_422_UNPROCESSABLE_ENTITY("Solo se puede aceptar beta o alpha para un mod")
        bundle = await self.modpackDB.bundle_get(bundle_id)
        if project_id not in {p.project_id for p in bundle.projects} and not await self._resolution_names(bundle, project_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY("Ese proyecto no está en el modpack ni es una de sus dependencias")
        updated = await self.modpackDB.bundle_channel_exception_set(bundle_id, project_id, channel)
        await self.audit.record(actor, "bundle.channel_exception", _bundle(bundle_id),
                                {"project_id": project_id, "channel": channel})
        return updated

    async def channel_exception_unset(self, bundle_id: str, project_id: str, actor: Actor) -> ModpackBundle:
        """Remove one project's channel exception. 422 for an id that is not a Modrinth id."""
        if not MODRINTH_ID.fullmatch(project_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{project_id}' no es un id de Modrinth")
        updated = await self.modpackDB.bundle_channel_exception_unset(bundle_id, project_id)
        await self.audit.record(actor, "bundle.channel_exception.delete", _bundle(bundle_id), {"project_id": project_id})
        return updated

    async def _resolution_names(self, bundle: ModpackBundle, project_id: str) -> bool:
        """Whether the bundle's last saved resolution names the project, as an entry or a problem."""
        saved = await self.modpackDB.resolution_get(bundle.id) if bundle.id is not None else None
        if saved is None:
            return False
        return any(e.project_id == project_id for e in saved.lock.entries) \
            or any(p.project_id == project_id for p in saved.lock.problems)

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
        saved resolution (its entries without an origin) by title, each with its card data. Without a
        resolution, only the pack's projects. The pins no row shows are listed apart. The catalog is
        filled first, in one call within `icon_deadline` (best effort), for the missing icons and for
        the projects the resolution's suggestions and problems (with who asked and the other side
        of an incompatibility) and those pins name; a problem's mod without a row gets its card
        from that fill too. A Modrinth that is down gives the list without them, never an error.
        Writes the catalog.

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
        # Only what the resolution brought in itself: an entry with an origin was asked for by the
        # pack, and one no longer in it was removed or replaced since (that does not resolve again).
        dependencies = [i for i, e in entries.items() if i not in pack and e.origin is None]
        orphan_pins = [i for i in bundle.pins if i not in pack and i not in dependencies]
        requesters = {r for e in entries.values() for r in e.requested_by}
        named: list[str] = list(orphan_pins)
        # The channel exceptions are listed by title too, with or without a row.
        named += [i for i in bundle.channel_exceptions if i not in pack and i not in dependencies]
        if resolution is not None:
            named += [x.project_id for x in resolution.lock.suggestions]
            named += [r for x in resolution.lock.suggestions for r in x.requested_by]
            named += [x.project_id for x in resolution.lock.problems if x.project_id]
            # Who asked for a problem's mod and the other side of an incompatibility: its text names them.
            named += [r for x in resolution.lock.problems for r in x.requested_by]
            named += [x.conflict_with for x in resolution.lock.problems if x.conflict_with]
        catalog = await fill_catalog(self.modrinthDB, self.modrinthAPI,
                                     [*pack, *dependencies, *sorted(requesters), *named], self.icon_deadline)
        # The newer versions are against the build the check looked at: only shown next to that
        # build's versions, the latest's (a check of the recommended build compares older ones).
        current = check is not None and check.build_number == bundle.latest_build
        newer = {u.project_id: u.to_version for u in check.updates if not u.pinned} if check and current else {}
        target = Target(game_version=bundle.version, loader=str(bundle.loader.value), channels=frozenset(bundle.channels))
        for_version: dict[str, list[ModrinthVersion]] = {}
        for found_version in await self.modrinthDB.version_find_for_game_version([*pack, *dependencies], bundle.version):
            for_version.setdefault(found_version.project_id, []).append(found_version)

        def row(project_id: str, origin: Optional[str]) -> ModRow:
            found, entry = catalog.get(project_id), entries.get(project_id)
            pinned = project_id in bundle.pins
            supports_loader = supports_version = version_channel = None
            if found is not None:
                kind = entry.project_type if entry else found.project_type.value
                supports_loader = bool(target.loaders(kind) & set(found.loaders))
                supports_version = bundle.version in found.game_versions
                version_channel = outside_channel(target, kind, for_version.get(project_id, []))
            return ModRow(
                project_id=project_id, title=found.title if found else project_id,
                icon_url=found.icon_url if found else None, description=found.description if found else "",
                downloads=found.downloads if found else 0, categories=list(found.categories) if found else [],
                loaders=list(found.loaders) if found else [], game_versions=list(found.game_versions) if found else [],
                supports_loader=supports_loader, supports_version=supports_version, origin=origin,
                required_by=[catalog[r].title if r in catalog else r for r in (entry.requested_by if entry else [])],
                requested_by=list(entry.requested_by) if entry else [],
                side=entry.server if entry else None, build_version=in_build.get(project_id),
                newer_version=None if pinned else newer.get(project_id), pinned=pinned,
                source_url=found.source_url if found else None, issues_url=found.issues_url if found else None,
                version_channel=version_channel)

        rows = [row(i, origin) for i, origin in pack.items()]
        rows += sorted((row(i, None) for i in dependencies), key=lambda r: r.title)
        titles = {i: catalog[i].title if i in catalog else i for i in named}
        titles.update({r.project_id: r.title for r in rows})
        shown = {r.project_id for r in rows}
        problem_cards = {x.project_id: row(x.project_id, None) for x in (resolution.lock.problems if resolution else [])
                         if x.project_id and x.project_id not in shown}
        return ModList(rows=rows, resolution=resolution, stale=stale,
                       icons_missing=sum(1 for r in rows if r.icon_url is None), titles=titles, orphan_pins=orphan_pins,
                       problem_cards=problem_cards)

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

    # A mod's local page (spec §2, "Ficha local de cada mod").

    async def mod_page(self, project_id: str, named: Sequence[str] = (), pack: Optional[str] = None) -> ModPage:
        """
        Everything a mod's page shows: the project, the packs that use it (as a project or as a
        dependency of their saved resolution, with their recommended and latest builds that include
        it), the servers whose latest scan identified it, and its versions from the catalog. Never
        waits for Modrinth for the versions. Opened from a pack (`pack`, its bundle id), the page
        marks the pack's Minecraft version and loader; an id that is not a bundle's is ignored.

        Modrinth is asked once, within `icon_deadline`, for every project the page names that the
        catalog lacks (or has without an icon): its dependencies, who asked for it and `named` (e.g.
        the mod the panel came from). A stored project is filled best effort: Modrinth failing leaves
        those names as ids. A project the catalog does not have is fetched with them in a call whose
        failure is raised (UpstreamError, TimeoutError): the panel shows it with a retry. Fetched by
        slug, its relatives are only known once Modrinth gives its id: they are filled after it, best
        effort, within what is left of the same deadline. Writes the catalog.

        :param project_id: A Modrinth project id, or a slug.
        :param named: More project ids whose titles the page shows.
        :param pack: The bundle id of the pack the page was opened from, if any.
        :return: The page; 404 if the id is not a Modrinth id or Modrinth does not know the project.
        """
        if not MODRINTH_ID.fullmatch(project_id):
            raise HTTP_404_NOT_FOUND(f"«{project_id}» no es un id de Modrinth")
        bundle = await self._pack(pack)
        stored = await self.modrinthDB.project_find_by_ref(project_id)
        key = stored.project_id if stored is not None else project_id
        local = await self._mod_local(key)
        others = _related(key, local, named)
        if stored is not None:
            catalog = await fill_catalog(self.modrinthDB, self.modrinthAPI, [key, *others], self.icon_deadline)
            project = catalog.get(key, stored)
        else:
            loop = asyncio.get_running_loop()
            started = loop.time()
            known = {p.project_id: p for p in await self.modrinthDB.project_find_by_modrinth_id(others)}
            missing = [i for i in others if i not in known or not known[i].icon_url]
            fetched = list(await asyncio.wait_for(self.modrinthAPI.project_get_many([key, *missing]),
                                                  self.icon_deadline))
            found = next((p for p in fetched if key in (p.project_id, p.slug)), None)
            if found is None:
                raise HTTP_404_NOT_FOUND(f"Modrinth no conoce el mod «{key}»")
            catalog = {**known, **{p.project_id: p for p in await self.modrinthDB.project_upsert_many(fetched)}}
            project = catalog[found.project_id]
            if found.project_id != key:
                # It was a slug: what MongoDB has about the project is under its id, and so are the
                # relatives the page names.
                key = found.project_id
                local = await self._mod_local(key)
                relatives = [i for i in _related(key, local, named) if i not in catalog]
                if relatives:
                    left = max(self.icon_deadline - (loop.time() - started), 0.0)
                    catalog.update(await fill_catalog(self.modrinthDB, self.modrinthAPI, relatives, left))
        for_version = (await self.modrinthDB.version_find_for_game_version([project.project_id], bundle.version)
                       if bundle is not None else [])
        return self._mod_page(project, local, catalog, named, bundle, for_version)

    async def _pack(self, bundle_id: Optional[str]) -> Optional[ModpackBundle]:
        """The bundle a mod's page was opened from; None when the id is not a bundle's."""
        if not bundle_id or not BUNDLE_ID.fullmatch(bundle_id):
            return None
        found = await self.modpackDB.bundle_find([bundle_id]).to_list()
        return found[0] if found else None

    async def _mod_local(self, project_id: str) -> _ModLocal:
        """The reads of mod_page, all on MongoDB: the catalog's versions and their required
        dependencies, the packs that use the project, their builds, and the servers' latest scans."""
        versions = await self.modrinthDB.version_find_by_project(project_id, MOD_PAGE_VERSIONS)
        dependencies = list(dict.fromkeys(
            d.project_id for v in versions for d in v.dependencies
            if d.dependency_type == ModrinthDependencyType.required and d.project_id and d.project_id != project_id))
        bundles = {str(b.id): b for b in await self.modpackDB.bundle_find_with_project(project_id)}
        requested: dict[str, list[str]] = {}
        for resolution in await self.modpackDB.resolution_find_with_dependency(project_id):
            owner = str(resolution.bundle_id)
            requested[owner] = [r for e in resolution.lock.entries if e.project_id == project_id and e.origin is None
                                for r in e.requested_by]
        extra = [i for i in requested if i not in bundles]
        if extra:
            bundles.update({str(b.id): b for b in await self.modpackDB.bundle_find(extra).to_list()})
        # As mod_list: a project of the pack is listed as such, never as a dependency too; and a
        # resolution whose bundle is gone names nobody.
        requested = {b: ids for b, ids in requested.items()
                     if b in bundles and all(p.project_id != project_id for p in bundles[b].projects)}
        wanted = [(b.id, n) for b in bundles.values() if b.id is not None
                  for n in (b.recommended_build, b.latest_build) if n is not None]
        in_builds = {(str(build.bundle_id), build.number): e.version_number
                     for build in await self.modpackDB.build_find_numbers(wanted)
                     for e in build.lock.entries if e.project_id == project_id}
        hits = await self.pterodactylDB.scan_latest_identifying(project_id, SCAN_DIRECTORY)
        by_id = {server.id: server for server in await self.pterodactylDB.server_find().to_list()} if hits else {}
        servers: list[tuple[PterodactylServer, datetime, Optional[str]]] = [
            (by_id[server_id], scanned_at, version_id) for server_id, scanned_at, version_id in hits if server_id in by_id]
        servers.sort(key=lambda s: (s[0].name.casefold(), str(s[0].id)))
        identified = [v for _, _, v in servers if v]
        numbers = {v.version_id: v.version_number for v in await self.modrinthDB.version_find_by_version_ids(identified)}
        return _ModLocal(versions=versions, dependencies=dependencies, bundles=list(bundles.values()),
                         requested=requested, in_builds=in_builds, servers=servers, numbers=numbers)

    @staticmethod
    def _mod_page(project: ModrinthProject, local: _ModLocal, catalog: dict[str, ModrinthProject],
                  named: Sequence[str], bundle: Optional[ModpackBundle] = None,
                  for_version: Sequence[ModrinthVersion] = ()) -> ModPage:
        def title(project_id: str) -> str:
            return catalog[project_id].title if project_id in catalog else project_id

        def built(bundle_id: str, number: Optional[int]) -> Optional[tuple[int, str]]:
            version = local.in_builds.get((bundle_id, number)) if number is not None else None
            return (number, version) if number is not None and version is not None else None

        uses: list[ModUse] = []
        for using in sorted(local.bundles, key=lambda b: (b.name.casefold(), str(b.id))):
            bundle_id = str(using.id)
            origin = next((p.origin.value for p in using.projects if p.project_id == project.project_id), None)
            uses.append(ModUse(bundle_id=bundle_id, bundle_name=using.name, origin=origin,
                               dependency_of=[title(r) for r in local.requested.get(bundle_id, [])],
                               recommended=built(bundle_id, using.recommended_build),
                               latest=built(bundle_id, using.latest_build),
                               requested_by=list(local.requested.get(bundle_id, []))))
        servers = [ServerUse(server_id=str(server.id), server_name=server.name,
                             version=local.numbers.get(version_id) if version_id else None, scanned_at=scanned_at)
                   for server, scanned_at, version_id in local.servers]
        mark = None
        if bundle is not None:
            target = Target(game_version=bundle.version, loader=str(bundle.loader.value), channels=frozenset(bundle.channels))
            mark = PackMark(bundle_id=str(bundle.id), name=bundle.name, game_version=bundle.version,
                            loader=str(bundle.loader.value), supports_version=bundle.version in project.game_versions,
                            supports_loader=bool(target.loaders(project.project_type.value) & set(project.loaders)),
                            version_channel=outside_channel(target, project.project_type.value, for_version))
        shown = [project.project_id, *local.dependencies, *named]
        return ModPage(project=project, uses=uses, servers=servers,
                       versions=[_version_row(v) for v in local.versions], versions_partial=True,
                       titles={i: title(i) for i in shown}, dependencies=local.dependencies, pack=mark)

    async def mod_versions_all(self, project_id: str) -> list[VersionRow]:
        """
        Every version Modrinth has of a project, newest first: asked without filters within
        `icon_deadline`, and stored in the catalog. Modrinth failing or not answering is raised
        (UpstreamError, TimeoutError). 404 for an id that is not a Modrinth id. Writes the catalog.
        """
        if not MODRINTH_ID.fullmatch(project_id):
            raise HTTP_404_NOT_FOUND(f"«{project_id}» no es un id de Modrinth")
        found = list(await asyncio.wait_for(self.modrinthAPI.project_versions(project_id), self.icon_deadline))
        if found:
            await self.modrinthDB.version_upsert_many(found)
        found.sort(key=lambda v: v.date_published, reverse=True)
        return [_version_row(v) for v in found]

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
        lock = await self._resolve_checked(bundle, request, check=self._stale(bundle))
        await self._resolution_save(bundle, request, lock)
        return lock

    def _stale(self, bundle: ModpackBundle) -> bool:
        """Whether the pack's Modrinth information is past the window (E1): never checked, or longer ago."""
        checked = bundle.modrinth_checked_at
        if checked is not None and checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        return checked is None or self.clock() - checked >= CATALOG_WINDOW

    async def _resolve_checked(self, bundle: ModpackBundle, request: ResolveRequest, check: bool) -> Lock:
        """Resolve; with `check`, what the catalog has of the pack is compared with Modrinth first
        and the pack is marked checked now."""
        prior: list[str] = []
        if check and bundle.id is not None:
            saved = await self.modpackDB.resolution_get(bundle.id)
            prior = list(dict.fromkeys([*request.projects, *(e.project_id for e in saved.lock.entries)] if saved
                                       else request.projects))
        lock = await self._resolve(request, check=check, prior=prior)
        if check:
            await self.modpackDB.bundle_checked_at_set(str(bundle.id), self.clock())
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
            pins=bundle.pins, server_decisions=bundle.server_decisions, channel_exceptions=bundle.channel_exceptions,
            replacements=merge_replacements(global_rules, bundle.replacements))

    async def _resolve(self, request: ResolveRequest, check: bool = False, prior: Sequence[str] = ()) -> Lock:
        """
        Resolve from the catalog, asking Modrinth only for what it lacks (spec 2026-10-09-economia-de-
        requests, F2). With `check`, every project the catalog already accounts for is compared
        with Modrinth before it is used: the `prior` ones at once, the rest as resolution reaches
        them, each batch in one request for the projects and one for their new versions. Whatever
        is fetched is stored as it arrives: a failure halfway keeps it.
        """
        catalog = Catalog()
        checked: set[str] = set()
        if check and prior:
            await self._check(list(prior), request.target.game_version, checked)
        lock = resolve(request, catalog)
        for _ in range(RESOLVE_ROUNDS):
            if lock.complete:
                return lock
            await self._load(catalog, lock, request, check, checked)
            lock = resolve(request, catalog)
        return lock

    async def _load(self, catalog: Catalog, lock: Lock, request: ResolveRequest, check: bool, checked: set[str]) -> None:
        """Put in `catalog` what `lock` needs: from MongoDB when the catalog accounts for it, else
        from Modrinth (each project's versions for the target, once; projects and versions by id,
        in batches)."""
        game_version = request.target.game_version
        wanted_types = sorted(lock.needs_projects | lock.needs_types)
        if wanted_types:
            stored = {p.project_id: p for p in await self.modrinthDB.project_find_by_modrinth_id(wanted_types)}
            missing = [i for i in wanted_types if i not in stored]
            fetched = {p.project_id: p for p in await self._projects_fetch(missing)} if missing else {}
            for project_id in wanted_types:
                found = fetched.get(project_id) or stored.get(project_id)
                catalog.types[project_id] = found.project_type.value if found else "unknown"
        if lock.needs_projects:
            needed = sorted(lock.needs_projects)
            if check:
                await self._check([i for i in needed if i not in checked], game_version, checked)
            records = {(r.project_id, tuple(r.loaders)): r
                       for r in await self.modrinthDB.version_lists_find(needed, game_version)}
            for project_id in needed:
                loaders = sorted(request.target.loaders(catalog.types.get(project_id, "mod")))
                record = records.get((project_id, tuple(loaders)))
                if record is not None:
                    versions = await self.modrinthDB.version_find_listed(project_id, record.version_ids)
                else:
                    versions = await self._versions_fetch(project_id, loaders, game_version)
                    checked.add(project_id)
                catalog.by_project.setdefault(project_id, [])
                for version in versions:
                    if game_version in version.game_versions and set(loaders) & set(version.loaders):
                        catalog.add(candidate(version))
        if lock.needs_versions:
            asked = sorted(lock.needs_versions)
            stored_versions = await self.modrinthDB.version_find_by_version_ids(asked)
            missing_ids = sorted(set(asked) - {v.version_id for v in stored_versions})
            fetched_versions: list[ModrinthVersion] = []
            for chunk in _chunks(missing_ids):
                fetched_versions += list(await self.modrinthAPI.version_get_many(chunk))
            if fetched_versions:
                await self.modrinthDB.version_upsert_many(fetched_versions)
            catalog.gone |= set(missing_ids) - {v.version_id for v in fetched_versions}
            for version in [*stored_versions, *fetched_versions]:
                catalog.add(candidate(version))

    async def _versions_fetch(self, project_id: str, loaders: list[str], game_version: str) -> list[ModrinthVersion]:
        """A project's versions for a target, from Modrinth (the first time only), stored at once with
        the list of ids the project names now."""
        versions = list(await self.modrinthAPI.project_versions(project_id, loaders, [game_version]))
        if versions:
            await self.modrinthDB.version_upsert_many(versions)
        stored = await self.modrinthDB.project_find_by_modrinth_id([project_id])
        listed = set(stored[0].versions) if stored else set()
        await self.modrinthDB.version_list_save(ModrinthVersionList(
            project_id=project_id, game_version=game_version, loaders=loaders,
            version_ids=sorted(listed | {v.version_id for v in versions}), fetched_at=self.clock()))
        return versions

    async def _projects_fetch(self, project_ids: list[str]) -> list[ModrinthProject]:
        """Projects from Modrinth, in batches, stored."""
        found: list[ModrinthProject] = []
        for chunk in _chunks(project_ids):
            found += list(await self.modrinthAPI.project_get_many(chunk))
        if found:
            await self.modrinthDB.project_upsert_many(found)
        return found

    async def _check(self, project_ids: list[str], game_version: str, checked: set[str]) -> None:
        """Compare what the catalog accounts for of these projects with what Modrinth lists now: one
        request for the projects (by batch), one for the versions it lists that the catalog lacks.
        A version it no longer lists stops being used. Projects without a list are left to fetch."""
        records = await self.modrinthDB.version_lists_find(project_ids, game_version)
        known = sorted({r.project_id for r in records})
        checked.update(project_ids)
        if not known:
            return
        current = {p.project_id: set(p.versions) for p in await self._projects_fetch(known)}
        new_ids = sorted({i for r in records for i in current.get(r.project_id, set()) - set(r.version_ids)})
        fetched: list[ModrinthVersion] = []
        for chunk in _chunks(new_ids):
            fetched += list(await self.modrinthAPI.version_get_many(chunk))
        if fetched:
            await self.modrinthDB.version_upsert_many(fetched)
        for record in records:
            listed = current.get(record.project_id, set())
            record.version_ids = sorted(listed)
            record.fetched_at = self.clock()
            await self.modrinthDB.version_list_save(record)

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
        lock = await self._resolve_checked(bundle, request, check=True)
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
