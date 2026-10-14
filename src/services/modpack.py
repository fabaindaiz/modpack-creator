import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping
import math
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence
from beanie import PydanticObjectId
from pydantic import BaseModel
from src.database import ModpackDatabase, ModrinthDatabase, PterodactylDatabase
from src.library.api.client.loaders import LoaderMetaAPI
from src.library.api.client.modrinth import ID_CHUNK, ModrinthAPI
from src.library.changes import ChangeSet, VersionFacts, diff_entries, direction
from src.library.scan import Unexplained, compare, unexplained_for
from src.library.mrpack import mrpack_index, write_mrpack
from src.services.jobs import Handler, JobContext, JobFailed, NotificationSink
from src.database.jobs import JobDatabase
from src.model.jobs import Job
from src.library.bridges import BRIDGES, REPLACES, VIA_RISK, bridge as find_bridge, companions, guest_of
from src.library.resolution import CHANNEL_RANK, EXCEPTION_CHANNELS, Candidate, Catalog, Lock, LockEntry, ResolveRequest, Target, merge_replacements, request_digest, resolve
from src.model.modpack import BUNDLE_KEYED_FIELDS, AvailableUpdate, LoaderVersion, UpdateCheck, ModpackBuild, ModpackBundle, ModpackBundlePatch, ModpackReplacement, ModpackResolution, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthProject, ModrinthVersion, ModrinthVersionList, VersionBrief
from src.model.modrinth.version import ModrinthDependencyType
from src.model.pterodactyl import PterodactylServer
from src.model.audit import AuditTarget
from src.model.users import Actor
from src.services.audit import AuditSink
from src.services.modrinth import FILL_DEADLINE, fill_catalog
from src.library.utils import MODRINTH_ID, as_utc
from src.services.pterodactyl import SCAN_DIRECTORY
from src.exceptions import *

# Each round fetches what the lock needs; a dependency chain deeper than this is reported as an
# incomplete lock instead of fetching forever.
RESOLVE_ROUNDS = 32
# A pack's Modrinth information is checked at most this often (E1); a build always checks (E2).
CATALOG_WINDOW = timedelta(minutes=5)
# How often the manual "Actualizar desde Modrinth" may run for one pack (E3).
MANUAL_REFRESH_EVERY = timedelta(minutes=1)
# How long what Modrinth lacks (an unknown project, a removed version) is taken as lacking.
LACKS_RECHECK = timedelta(hours=24)
# How long after its build job ended a blocked build still opens the dialog.
BLOCKED_BUILD_RECENT = timedelta(minutes=1)
# What a Modrinth project id looks like; anything else is not a key to store.
# One request decides at most this many mods: a bigger one is a mistake or abuse.
MAX_DECISIONS = 500
# Fields of a bundle that a patch may leave out but never set to null: the bundle would not load.
BUNDLE_NOT_NULL = ("name", "description", "version", "loader", "channels", "secondary_loaders", *BUNDLE_KEYED_FIELDS)

# What a catalog job hears as it goes: what it processes, done and total.
Report = Callable[[str, int, int], Awaitable[None]]


class _NeedsModrinth(Exception):
    """Resolving from the catalog alone is not enough: something must come from Modrinth."""


def _refuse_blocked(lock: Lock) -> None:
    """BuildBlocked if the lock is not complete, has problems, or has mods whose place on the server
    waits for the admin: a build is something that can be deployed as it is."""
    if not lock.complete or lock.problems or lock.undecided:
        reasons = [p.detail for p in lock.problems]
        reasons += [f"{e.project_id}: falta decidir si va en el servidor" for e in lock.undecided]
        if not lock.complete:
            reasons.append("la resolución no terminó")
        raise BuildBlocked("No se puede congelar este lock: " + "; ".join(reasons), lock,
                           only_undecided=lock.complete and not lock.problems and bool(lock.undecided))


def _catalog_key(bundle_id: str) -> str:
    return f"catalog:{bundle_id}"


def candidate(version: ModrinthVersion) -> Candidate:
    return Candidate.from_modrinth({**version.model_dump(mode="json"), "id": version.version_id})

logger = logging.getLogger("modpack.changes")
# How long a preview waits for changelogs in all (N-R2): Modrinth rate-limiting may back off for a
# minute per request, and a page about a game server must not wait for it.
CHANGELOG_DEADLINE = 10.0
# How long a loader version from the loader's meta is kept (U4/M4; owner, 2026-10-09).
LOADER_VERSION_TTL = timedelta(hours=24)


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
    # Project id -> "down" or "branch" for the changed mods that go back or change branch (F27,
    # d-7985ee-322302); a mod that goes forward, or whose versions the catalog lacks, is absent.
    directions: dict[str, str] = {}
    # The changed mods whose new version is the pack's pin: a step back there is the pin's.
    pinned: set[str] = set()
    # Against a server only (F28, d-7985ee-83e764): the jars of its last scan, taken at `scanned_at`,
    # that the build does not explain, which stop a real update before it writes; None if none.
    unexplained: Optional[Unexplained] = None
    scanned_at: Optional[datetime] = None
    # Against a server only (batch C, C6): bridge label -> how many of the build's mods go on the
    # server through it, listed apart because only starting the server says whether they run.
    bridged: dict[str, int] = {}

    @property
    def downgrades(self) -> list[str]:
        return sorted(p for p, d in self.directions.items() if d == "down")


@dataclass(frozen=True)
class PinDowngrade:
    """A pin before the mod's floor (F27, d-7985ee-c62c61): the floor's version, the pin's, and what
    runs the floor ("el build 7", "el servidor «Survival»")."""
    project_id: str
    title: str
    floor_version: str
    floor_number: str
    pin_version: str
    pin_number: str
    used_by: list[str]

    @property
    def text(self) -> str:
        users = " y ".join(self.used_by)
        return (f"Baja de versión: {self.title} {self.floor_number} → {self.pin_number}. "
                f"{users[:1].upper() + users[1:]} {'usan' if len(self.used_by) > 1 else 'usa'} {self.floor_number}, "
                "publicada después. Bajar un mod puede romper mundos y configs.")


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
    # When its pin is before the mod's floor (F27, d-7985ee-f43772): the floor's version number.
    pin_floor: Optional[str] = None
    # What the row's pin select offers (A3, owner 2026-10-13): the catalog's versions for the pack's
    # target, newest first, at most PIN_OPTIONS and then the pinned one; None when the catalog has
    # no list for the project (the select points to the card).
    pin_options: Optional[list["VersionOption"]] = None
    # The pinned version's id, or None.
    pinned_version: Optional[str] = None
    # The bridge it runs through (batch C), None when native.
    via: Optional[str] = None
    # For a bridged mod, its native version: "alcanza" (caught up with the installed one: offered),
    # "detrás" (older), "otro canal" (only outside the pack's channels); None when there is none.
    native: Optional[str] = None
    # A bridged mod that goes on the server: «servidor: probar antes» (d-7985ee-98c52a).
    server_risky: bool = False


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
    # The mods moved to native that can still go back to the bridge: what is installed of them (the
    # build in force, the servers) still runs through it (review of batch C).
    via_back: set[str] = field(default_factory=set)


@dataclass
class VersionOption:
    version_id: str
    version_number: str
    channel: str
    published: datetime


# How many of a project's catalog versions its page shows; "Cargar todas" asks Modrinth for the rest.
MOD_PAGE_VERSIONS = 50
# How many versions a mod's row offers to pin, besides the pinned one (A3, owner 2026-10-13).
PIN_OPTIONS = 15
# A pinned version the catalog does not have still gets an option, with no publication date.
_UNKNOWN_DATE = datetime.fromtimestamp(0, timezone.utc)


def pin_choices(versions: Iterable[ModrinthVersion | VersionBrief], game_version: str, loaders: Collection[str],
                channels: Collection[str]) -> list[VersionOption]:
    """The versions a project can be pinned to: listed, for this Minecraft version and one of these
    loaders, in these channels, newest first."""
    allowed = [v for v in versions if game_version in v.game_versions and set(loaders) & set(v.loaders)
               and v.version_type.value in channels and v.status.value == "listed"]
    allowed.sort(key=lambda v: v.date_published, reverse=True)
    return [VersionOption(version_id=v.version_id, version_number=v.version_number,
                          channel=v.version_type.value, published=v.date_published) for v in allowed]


def exception_channels(channel: Optional[str]) -> set[str]:
    """The channels a project's exception adds: its own and the more stable ones (as the resolver)."""
    limit = CHANNEL_RANK.get(channel or "", -1)
    return {name for name, rank in CHANNEL_RANK.items() if rank <= limit}


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
    # True when the pack's resolution has a problem about the mod (B4): a dependency it could not
    # give a version, for one.
    problem: bool = False


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
    # The bundles whose saved resolution has a problem about the mod (B4).
    problem: set[str] = field(default_factory=set)


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
            jobDB: Optional[JobDatabase] = None,
            ) -> None:
        self.clock = clock
        self.jobDB = jobDB or JobDatabase()
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

    async def bundle_update(self, bundle_id: str, patch: ModpackBundlePatch, actor: Actor,
                            confirm_downgrade: bool = False) -> ModpackBundle:
        """Change the fields sent, and the maps only at the keys sent (see ModpackBundlePatch).

        422, with nothing written, for a null on a field the bundle needs (it would no longer
        load) or a map key that is not a Modrinth id (it would become a nested key). A pin before
        its mod's floor is a DowngradeRefused (409), as in pin_set, unless `confirm_downgrade`
        (d-7985ee-0b9ebb).
        """
        sent = patch.model_dump(exclude_unset=True)
        for name in BUNDLE_NOT_NULL:
            if name in sent and sent[name] is None:
                raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{name}' no puede quedar vacío")
        for name in BUNDLE_KEYED_FIELDS:
            for key in sent.get(name) or {}:
                if not MODRINTH_ID.fullmatch(key):
                    raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{key}' no es un id de proyecto de Modrinth")
        if sent.get("secondary_loaders"):
            # Only the bridges of the pack's loader (batch C, d-7985ee-a8d395); one that does not
            # cover its Minecraft version is kept and has no effect until it does.
            current = await self.modpackDB.bundle_get(bundle_id)
            loader = str((patch.loader or current.loader).value)
            known = {b.key for b in BRIDGES if b.host == loader}
            unknown = [k for k in sent["secondary_loaders"] if k not in known]
            if unknown:
                raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{unknown[0]}' no es un puente para {loader}")
        new_pins = {p: v for p, v in (sent.get("pins") or {}).items() if v is not None}
        if new_pins and not confirm_downgrade:
            downgrades = await self.pin_downgrades(await self.modpackDB.bundle_get(bundle_id), new_pins)
            if downgrades:
                first = downgrades[sorted(downgrades)[0]]
                raise DowngradeRefused(" ".join(d.text for _, d in sorted(downgrades.items())) +
                                       " Para fijarlas igual, repite con confirm_downgrade=true.", first)
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

    async def pin_set(self, bundle_id: str, project_id: str, version_id: str, actor: Actor,
                      confirm_downgrade: bool = False) -> ModpackBundle:
        """Pin one mod of the bundle to a version, atomically; only that pin changes.

        A dependency can be pinned too ("Usar la beta" on its card): a project the pack's last saved
        resolution names, as an entry or as a problem.

        422 for an id that is not a Modrinth id (it would become a nested key), a project that is
        neither the bundle's nor one its last resolution names, or a version that is not of that
        project: the catalog is asked first, Modrinth if the catalog does not have it. A version
        before the mod's floor is a DowngradeRefused (409) unless `confirm_downgrade` (F27,
        d-7985ee-c62c61); the event then says it goes back. A version of the other way (native for a
        mod installed through a bridge, or bridged for a native one) is a 422: changing a mod's way asks
        first (d-7985ee-7e602f). Nothing is written for a refused pin.
        """
        for value in (project_id, version_id):
            if not MODRINTH_ID.fullmatch(value):
                raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{value}' no es un id de Modrinth")
        bundle = await self.modpackDB.bundle_get(bundle_id)
        if project_id not in {p.project_id for p in bundle.projects} and not await self._resolution_names(bundle, project_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY("Ese proyecto no está en el modpack ni es una de sus dependencias")
        found = await self._version(version_id)
        if found is None or found.project_id != project_id:
            raise HTTP_422_UNPROCESSABLE_ENTITY("La versión no es de ese proyecto")
        saved = await self.modpackDB.resolution_get(bundle.id) if bundle.id is not None else None
        installed = next((e for e in saved.lock.entries if e.project_id == project_id), None) if saved else None
        if installed is not None:
            loader = str(bundle.loader.value)
            native = bool(set(found.loaders) & Target(game_version=bundle.version, loader=loader,
                                                      channels=frozenset(bundle.channels)).loaders(installed.project_type))
            bridged = not native and any(b.guest in found.loaders for b in BRIDGES if b.host == loader)
            if (installed.via is not None and native) or (installed.via is None and bridged):
                # A pin never changes a mod's way (final review, d-7985ee-7e602f): that asks first.
                raise HTTP_422_UNPROCESSABLE_ENTITY(
                    "Esa versión cambia la vía del mod (nativa o por el puente): eso se hace con «Pasar a nativa» "
                    "o «Volver al puente», que piden confirmación")
        downgrade = (await self.pin_downgrades(bundle, {project_id: version_id})).get(project_id)
        if downgrade is not None and not confirm_downgrade:
            raise DowngradeRefused(downgrade.text, downgrade)
        updated = await self.modpackDB.bundle_pin_set(bundle_id, project_id, version_id)
        details: dict[str, Any] = {"project_id": project_id, "version_id": version_id}
        if downgrade is not None:
            details["downgrade"] = {"from": downgrade.floor_number, "to": downgrade.pin_number}
        await self.audit.record(actor, "bundle.pin", _bundle(bundle_id), details)
        return updated

    async def via_set(self, bundle_id: str, project_id: str, native: bool, actor: Actor,
                      confirm: bool = False) -> ModpackBundle:
        """Move a mod to its native versions (`native`) or back to the bridge's (batch C,
        d-7985ee-8e5f9e). Changing a mod's way does not always work (its config or the world's data
        may not carry over): without `confirm` it is a ViaChangeRefused (409) that describes the
        change, and nothing is written (d-7985ee-7e602f). Back to the bridge is a 422 once the build in
        force or a server runs its native version (review of batch C). Audited as `bundle.via`."""
        if not MODRINTH_ID.fullmatch(project_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{project_id}' no es un id de Modrinth")
        bundle = await self.modpackDB.bundle_get(bundle_id)
        if not native and await self._installed_native(bundle, [project_id]):
            raise HTTP_422_UNPROCESSABLE_ENTITY(
                "Ese mod ya está instalado en su versión nativa: no vuelve al puente (sería otro cambio de vía)")
        if not confirm:
            stored = await self.modrinthDB.project_find_by_modrinth_id([project_id])
            title = stored[0].title if stored else project_id
            loader = str(bundle.loader.value)
            label = next((b.label for b in BRIDGES if b.host == loader and b.key in bundle.secondary_loaders), "el puente")
            way = (f"vía {label} → nativa ({loader})", f"nativa → vía {label}")[0 if native else 1]
            raise ViaChangeRefused(
                f"{title}: {way}. {VIA_RISK} Para hacerlo igual, repite con confirm_via=true.", project_id=project_id,
                title=title, to_native=native, change=f"{title}: {way}")
        updated = await self.modpackDB.bundle_native_only_set(bundle_id, project_id, native)
        await self.audit.record(actor, "bundle.via", _bundle(bundle_id),
                                {"project_id": project_id, "to": "nativa" if native else "puente"})
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
        found = await self._version(version_id)
        return found.project_id if found is not None else None

    async def _version(self, version_id: str) -> Optional[ModrinthVersion]:
        """A version from the catalog, else from Modrinth; None when Modrinth does not know it."""
        stored = await self.modrinthDB.version_find_by_version_id(version_id)
        if stored is not None:
            return stored
        try:
            return await self.modrinthAPI.version_get(version_id)
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
        bundle = await self.modpackDB.bundle_get(bundle_id)
        projects = await self._modrinth_projects(refs)
        for found in projects:
            await self._refuse_replaced(bundle, found)
        added = await self.modpackDB.bundle_projects_add(
            bundle_id, [PackProject(project_id=p.project_id, origin=origin) for p in projects])
        await self.audit.record(actor, "bundle.project.add", _bundle(bundle_id),
                                {"refs": list(refs), "origin": origin.value})
        return added

    async def _installed_native(self, bundle: ModpackBundle, project_ids: Iterable[str]) -> set[str]:
        """Of these projects, those that the build in force or a linked server already runs in a native
        version: going back to the bridge would be a change of way the resolution never makes (review
        of batch C). Only MongoDB is read; a version the catalog lacks counts as not native."""
        wanted = set(project_ids)
        if not wanted:
            return set()
        sources = {p: versions for p, versions in (await self._floor_sources(bundle)).items() if p in wanted}
        found = {v.version_id: v for v in await self.modrinthDB.version_find_by_version_ids(
            sorted({v for versions in sources.values() for v in versions}))}
        native = Target(game_version=bundle.version, loader=str(bundle.loader.value)).loaders("mod")
        return {p for p, versions in sources.items()
                if any(v in found and native & set(found[v].loaders) for v in versions)}

    async def _refuse_replaced(self, bundle: ModpackBundle, found: ModrinthProject) -> None:
        """A project an active bridge replaces (Fabric API with Connector, d-7985ee-d7fcae) would only
        end in a conflict: a 422 that names what goes in its place (review of batch C)."""
        loader = str(bundle.loader.value)
        for key in bundle.secondary_loaders:
            active = find_bridge(key, loader, bundle.version)
            replacement = REPLACES.get(key, {}).get(found.project_id) if active is not None else None
            if active is not None and replacement is not None:
                named = await self.modrinthDB.project_find_by_modrinth_id([replacement])
                title = named[0].title if named else (await self._modrinth_projects([replacement]))[0].title
                raise HTTP_422_UNPROCESSABLE_ENTITY(
                    f"Con {active.label}, {found.title} se reemplaza por {title}: agrega ese en su lugar")

    async def bridge_companions_missing(self, bundle_id: str, project_id: str) -> list[tuple[str, str]]:
        """For a project that only fits through an active bridge, the bridge's mods (the bridge and
        its companions, spec §1) the pack lacks as projects, as (project id, title), in the table's
        order: what the add asks to confirm before it enters (C4, d-7985ee-56a529). Empty when the
        project runs natively, is already in the pack, or the pack has no bridge on (then Modrinth is
        not asked at all). The project resolves alone, with the pack's target, floors and rules, so
        its way is the one the resolution would take (native first, C3). `project_id` may be a slug.
        Writes only the catalog (what it fetched); adds nothing."""
        bundle = await self.modpackDB.bundle_get(bundle_id)
        request = await self._request(bundle)
        if not request.target.bridges:
            return []
        stored = await self.modrinthDB.project_find_by_modrinth_id([project_id])
        found = stored[0] if stored else (await self._modrinth_projects([project_id]))[0]
        owned = {p.project_id for p in bundle.projects}
        if found.project_id in owned:
            return []
        await self._refuse_replaced(bundle, found)
        game_version = request.target.game_version
        # The bridge itself or one of its companions («Agregar el puente»): the rest of its mods.
        used = next((b for b in request.target._active_bridges()
                     if found.project_id in {c for c, _ in companions(b, game_version)}), None)
        if used is None:
            alone = request.model_copy(update={"projects": [found.project_id],
                                               "origins": {found.project_id: ProjectOrigin.admin.value}})
            lock = await self._resolve(alone)
            way = next((e.via for e in lock.entries if e.project_id == found.project_id), None)
            used = find_bridge(way, request.target.loader, game_version) if way else None
        if used is None:
            return []
        missing = [c for c, _ in companions(used, game_version) if c not in owned and c != found.project_id]
        known = {p.project_id: p.title for p in await self.modrinthDB.project_find_by_modrinth_id(missing)}
        lacking = [c for c in missing if c not in known]
        known |= {p.project_id: p.title for p in await self._projects_fetch(lacking)} if lacking else {}
        return [(c, known.get(c, c)) for c in missing]

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
        """Remove a project from the pack. Its pin, server decision and channel exception go with it,
        in the same write, unless the last saved resolution brings it as a dependency of another mod;
        without a saved resolution they stay, since nothing says nobody needs it (d-7985ee-881689).
        The event names what was cleared."""
        bundle = await self.modpackDB.bundle_get(bundle_id)
        clear = MODRINTH_ID.fullmatch(project_id) is not None and await self.project_dependents(bundle_id, project_id) == []
        cleared: dict[str, Any] = {}
        if clear:
            for name, values in (("pin", bundle.pins), ("server_decision", bundle.server_decisions),
                                 ("channel_exception", bundle.channel_exceptions)):
                if project_id in values:
                    cleared[name] = values[project_id]
        remaining = await self.modpackDB.bundle_project_remove(bundle_id, project_id, clear_keys=clear)
        await self.audit.record(actor, "bundle.project.remove", _bundle(bundle_id),
                                {"project_id": project_id, "cleared": cleared})
        return remaining

    async def bridge_idle(self, bundle_id: str) -> list[str]:
        """The bridge mods still in the pack when no mod runs through a bridge any more (d-7985ee-80ee9a),
        by title: what the panel suggests removing, never removes. A mod of the saved resolution counts
        while it is in the pack or a mod in it still brings it. Without a bridge on, or a resolution, none. A read."""
        bundle = await self.modpackDB.bundle_get(bundle_id)
        loader = str(bundle.loader.value)
        found = [b for key in bundle.secondary_loaders if (b := find_bridge(key, loader, bundle.version)) is not None]
        saved = await self.modpackDB.resolution_get(bundle.id) if found and bundle.id is not None else None
        if saved is None:
            return []
        pack = {p.project_id for p in bundle.projects}
        # A dependency counts while a mod still in the pack asks for it, directly or through another
        # one: the saved resolution is from before the removal (final review).
        alive = set(pack)
        grew = True
        while grew:
            more = {e.project_id for e in saved.lock.entries
                    if e.origin is None and e.project_id not in alive and alive & set(e.requested_by)}
            alive |= more
            grew = bool(more)
        if any(e.via for e in saved.lock.entries if e.project_id in alive):
            return []
        ids = [i for b in found for i, _ in companions(b, bundle.version) if i in pack]
        titles = {p.project_id: p.title for p in await self.modrinthDB.project_find_by_modrinth_id(ids)}
        return [titles.get(i, i) for i in dict.fromkeys(ids)]

    async def project_dependents(self, bundle_id: str, project_id: str) -> Optional[list[str]]:
        """The other mods that, in the last saved resolution, need this one: the requesters of its
        entry, or of its problem when it could not be resolved. None when nothing can say: without a
        saved resolution, or with one of the pack as it was before (its digest is not the current
        request's), since a mod added since may need it (review, 2026-10-12). A read."""
        bundle = await self.modpackDB.bundle_get(bundle_id)
        saved = await self.modpackDB.resolution_get(bundle.id) if bundle.id is not None else None
        if saved is None or saved.request_digest != request_digest(await self._request(bundle)):
            return None
        requesters = [*(r for e in saved.lock.entries if e.project_id == project_id for r in e.requested_by),
                      *(r for p in saved.lock.problems if p.project_id == project_id for r in p.requested_by)]
        return sorted({r for r in requesters if r != project_id})

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
        target = Target(game_version=bundle.version, loader=str(bundle.loader.value), channels=frozenset(bundle.channels),
                        bridges=frozenset(bundle.secondary_loaders))
        lock_saved = resolution.lock if resolution is not None else None
        for_version: dict[str, list[ModrinthVersion]] = {}
        for found_version in await self.modrinthDB.version_find_for_game_version([*pack, *dependencies], bundle.version):
            for_version.setdefault(found_version.project_id, []).append(found_version)
        # A pin can go back after it was set, when the floor rises; the page never asks Modrinth.
        pins_back = await self.pin_downgrades(bundle, bundle.pins, fetch=False) if bundle.pins else {}
        # What each row's pin select offers (A3): the catalog's list for each row's target and its
        # versions, as briefs, in two queries for the whole page, never Modrinth. Only the list of
        # the row's own loaders (final review, 2026-10-13: every list was read whole).
        def kind_of(project_id: str) -> str:
            found, entry = catalog.get(project_id), entries.get(project_id)
            return entry.project_type if entry else found.project_type.value if found else "mod"

        def way_loaders(project_id: str) -> list[str]:
            """The loaders of the row's own way: its bridge's guest for a mod that runs through one
            (final review: a native pin would change its way without asking, d-7985ee-7e602f)."""
            entry = entries.get(project_id)
            guest = guest_of(entry.via, str(bundle.loader.value)) if entry is not None and entry.via else None
            return [guest] if guest else sorted(target.loaders(kind_of(project_id)))

        listed: dict[str, ModrinthVersionList] = {}
        for record in await self.modrinthDB.version_lists_find([*pack, *dependencies], bundle.version):
            if record.loaders == way_loaders(record.project_id):
                listed[record.project_id] = record
        wanted = {i for record in listed.values() for i in record.version_ids}
        wanted |= {bundle.pins[i] for i in [*pack, *dependencies] if i in bundle.pins}
        known = {v.version_id: v for v in await self.modrinthDB.version_briefs(sorted(wanted))} if wanted else {}

        def pin_options(project_id: str) -> Optional[list[VersionOption]]:
            loaders = way_loaders(project_id)
            record = listed.get(project_id)
            if record is None:
                return None
            channels = set(bundle.channels) | exception_channels(bundle.channel_exceptions.get(project_id))
            options = pin_choices((known[i] for i in record.version_ids if i in known), bundle.version, loaders,
                                  channels)[:PIN_OPTIONS]
            pinned = bundle.pins.get(project_id)
            if pinned is not None and pinned not in {o.version_id for o in options}:
                stored = known.get(pinned)
                options.append(VersionOption(version_id=pinned, version_number=stored.version_number if stored else pinned,
                                             channel=stored.version_type.value if stored else "",
                                             published=stored.date_published if stored else _UNKNOWN_DATE))
            return options

        def native_state(project_id: str, entry: Optional[LockEntry]) -> Optional[str]:
            """For a bridged mod: whether a native version caught up, is behind, or exists only outside
            the pack's channels (batch C)."""
            if entry is None or entry.via is None:
                return None
            if lock_saved is not None and project_id in lock_saved.native_available:
                return "alcanza"
            if lock_saved is not None and project_id in lock_saved.native_behind:
                return "detrás"
            natives = [v for v in for_version.get(project_id, []) if target.loaders(entry.project_type) & set(v.loaders)]
            return "otro canal" if natives else None

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
                version_channel=version_channel,
                pin_floor=pins_back[project_id].floor_number if pinned and project_id in pins_back else None,
                pin_options=pin_options(project_id),
                pinned_version=bundle.pins.get(project_id),
                via=entry.via if entry else None,
                native=native_state(project_id, entry),
                server_risky=entry is not None and entry.via is not None and entry.server == "include")

        rows = [row(i, origin) for i, origin in pack.items()]
        rows += sorted((row(i, None) for i in dependencies), key=lambda r: r.title)
        titles = {i: catalog[i].title if i in catalog else i for i in named}
        titles.update({r.project_id: r.title for r in rows})
        shown = {r.project_id for r in rows}
        problem_cards = {x.project_id: row(x.project_id, None) for x in (resolution.lock.problems if resolution else [])
                         if x.project_id and x.project_id not in shown}
        return ModList(rows=rows, resolution=resolution, stale=stale,
                       icons_missing=sum(1 for r in rows if r.icon_url is None), titles=titles, orphan_pins=orphan_pins,
                       problem_cards=problem_cards,
                       via_back=set(bundle.native_only) - await self._installed_native(bundle, bundle.native_only))

    async def project_versions(self, bundle_id: str, project_id: str) -> list[VersionOption]:
        """
        The versions of one project the admin can pin it to: those for the pack's Minecraft version
        and the loaders its resolution accepts for the project's type, in the channels the pack (and its
        exception's) allows, newest first. From the catalog when it accounts for the project's versions (the
        resolution's checks keep them current); else asked of Modrinth once and stored with its
        list (spec 2026-10-09-economia-de-requests, F6). 422 for an id that is not a Modrinth id.
        """
        if not MODRINTH_ID.fullmatch(project_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{project_id}' no es un id de Modrinth")
        bundle = await self.modpackDB.bundle_get(bundle_id)
        stored = await self.modrinthDB.project_find_by_modrinth_id([project_id])
        kind = stored[0].project_type.value if stored else "mod"
        target = Target(game_version=bundle.version, loader=str(bundle.loader.value), channels=frozenset(bundle.channels))
        loaders = sorted(target.loaders(kind))
        record = next((r for r in await self.modrinthDB.version_lists_find([project_id], bundle.version)
                       if r.loaders == loaders), None)
        if record is not None:
            found = [v for v in await self.modrinthDB.version_find_listed(project_id, record.version_ids)
                     if bundle.version in v.game_versions and set(loaders) & set(v.loaders)]
        else:
            found = await self._versions_fetch(project_id, loaders, bundle.version)
        channels = set(bundle.channels) | exception_channels(bundle.channel_exceptions.get(project_id))
        return pin_choices(found, bundle.version, loaders, channels)

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
        problem: set[str] = set()
        for resolution in await self.modpackDB.resolution_find_with_dependency(project_id):
            owner = str(resolution.bundle_id)
            asked = [r for e in resolution.lock.entries if e.project_id == project_id and e.origin is None
                     for r in e.requested_by]
            about = [p for p in resolution.lock.problems if p.project_id == project_id]
            if about:
                problem.add(owner)
            requested[owner] = list(dict.fromkeys([*asked, *(r for p in about for r in p.requested_by)]))
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
                         requested=requested, in_builds=in_builds, servers=servers, numbers=numbers,
                         problem={b for b in problem if b in requested})

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
                               requested_by=list(local.requested.get(bundle_id, [])),
                               problem=bundle_id in local.problem))
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

    async def resolve(self, bundle_id: str, actor: Optional[Actor] = None) -> Lock:
        """
        Resolve a bundle into a lock from the catalog, never waiting for Modrinth (spec
        2026-10-09-economia-de-requests, E6–E7). When the catalog lacks something, a catalog job
        brings it and resolves the pack (CatalogPending names it; asking again names the same one).
        Past the window, the pack resolves from the catalog at once and the check is left in that job.

        The latest resolution is saved to show it, never as the input of a build: build_create
        resolves again and freezes that lock.

        :return: The lock; `complete` is False only if the dependency chain ran past RESOLVE_ROUNDS.
        """
        bundle = await self.modpackDB.bundle_get(bundle_id)
        request = await self._request(bundle)
        try:
            lock = await self._resolve(request, local_only=True)
        except _NeedsModrinth:
            raise CatalogPending(await self._catalog_job(bundle_id, actor, force=False))
        await self._resolution_save(bundle, request, lock)
        if self._stale(bundle):
            await self._catalog_job(bundle_id, actor, force=False)
        return lock

    async def resolve_fetching(self, bundle_id: str, report: Optional[Report] = None, force: bool = False) -> Lock:
        """Resolve asking Modrinth for what the catalog lacks, and checking it first if the pack is past
        the window (or `force`): the catalog job's work. `report` hears how far it is."""
        bundle = await self.modpackDB.bundle_get(bundle_id)
        request = await self._request(bundle)
        lock = await self._resolve_checked(bundle, request, check=force or self._stale(bundle), report=report)
        await self._resolution_save(bundle, request, lock)
        return lock

    async def catalog_refresh(self, bundle_id: str, actor: Actor) -> Job:
        """
        "Actualizar desde Modrinth" (E3): a catalog job that checks the pack now, inside the window
        too. If one is already queued or running, that one. Once a minute per pack: sooner is a
        QuotaExceeded that says when.
        """
        active = await self.jobDB.active_for(_catalog_key(bundle_id))
        if active is not None:
            if not active.payload.get("force") and await self.jobDB.force_if_queued(active.id):
                # A job a resolve left (not forced) and still queued: it now checks Modrinth too.
                active.payload["force"] = True
            return active
        bundle = await self.modpackDB.bundle_get(bundle_id)
        last = bundle.manual_refreshed_at
        if last is not None:
            last = as_utc(last)
            left = MANUAL_REFRESH_EVERY - (self.clock() - last)
            if left > timedelta(0):
                raise QuotaExceeded(f"Se puede volver a actualizar en {math.ceil(left.total_seconds())} s",
                                    left.total_seconds())
        await self.modpackDB.bundle_manual_refreshed_at_set(bundle_id, self.clock())
        job = await self._catalog_job(bundle_id, actor, force=True)
        await self.audit.record(actor, "bundle.catalog_refresh", _bundle(bundle_id), {"job_id": str(job.id)})
        return job

    async def _catalog_job(self, bundle_id: str, actor: Optional[Actor], force: bool) -> Job:
        """The pack's catalog job: a new one, or the one already queued or running (never two)."""
        key = _catalog_key(bundle_id)
        try:
            return await self.jobDB.enqueue("catalog_refresh", key=key, payload={"bundle_id": bundle_id, "force": force},
                                            requested_by=actor.ref() if actor is not None else None)
        except HTTP_409_CONFLICT:
            active = await self.jobDB.active_for(key)
            if active is None:
                raise
            return active

    async def active_jobs(self, bundle_id: str) -> list[Job]:
        """The pack's queued or running jobs: its catalog, its build and its update check (E8)."""
        return await self.jobDB.active_for_keys([_catalog_key(bundle_id), f"build:{bundle_id}", f"bundle:{bundle_id}"])

    def handlers(self) -> dict[str, Handler]:
        return {"catalog_refresh": self._catalog_run, "build_create": self._build_run}

    async def _catalog_run(self, job: Job, context: JobContext) -> None:
        bundle_id = job.payload["bundle_id"]

        async def run() -> dict[str, Any]:
            lock = await self.resolve_fetching(bundle_id, report=context.progress, force=bool(job.payload.get("force")))
            if job.payload.get("force"):
                # «Actualizar desde Modrinth» also asks the loader's meta again (U4/M4).
                bundle = await self.modpackDB.bundle_get(bundle_id)
                if bundle.loader_version is None:
                    try:
                        await self._loader_version(str(bundle.loader.value), bundle.version, force=True)
                    except Exception:
                        # The resolution is saved: a meta that is down only leaves the old one (or
                        # none, asked again at the next build), never a failed refresh.
                        logger.warning("loader meta for %s %s failed on refresh", bundle.loader.value,
                                       bundle.version, exc_info=True)
            await context.progress("Resolviendo el pack", 1, 1)
            return {"entries": len(lock.entries), "problems": len(lock.problems), "undecided": len(lock.undecided)}

        await context.step("resolve", run)

    async def _loader_version(self, loader: str, game_version: str, force: bool = False) -> Optional[str]:
        """The loader version for this Minecraft version (U4/M4): the kept one while it is younger than
        LOADER_VERSION_TTL, unless `force`; else the loader's meta, kept. If the meta fails, the
        kept one, however old; with none kept, the failure."""
        key = f"{loader}|{game_version}"
        kept = await self.modpackDB.loader_version_get(key)
        if kept is not None and not force and self.clock() - as_utc(kept.fetched_at) < LOADER_VERSION_TTL:
            return kept.version
        try:
            version = await self.loaderAPI.loader_version(loader, game_version)
        except Exception:
            if kept is None:
                raise
            logger.warning("loader meta for %s failed; using the kept %s", key, kept.version, exc_info=True)
            return kept.version
        await self.modpackDB.loader_version_save(LoaderVersion(key=key, version=version, fetched_at=self.clock()))
        return version

    def _stale(self, bundle: ModpackBundle) -> bool:
        """Whether the pack's Modrinth information is past the window (E1): never checked, or longer ago."""
        checked = bundle.modrinth_checked_at
        checked = as_utc(checked) if checked is not None else None
        return checked is None or self.clock() - checked >= CATALOG_WINDOW

    async def _resolve_checked(self, bundle: ModpackBundle, request: ResolveRequest, check: bool,
                               report: Optional[Report] = None) -> Lock:
        """Resolve; with `check`, what the catalog has of the pack is compared with Modrinth first
        and the pack is marked checked now."""
        prior: list[str] = []
        if check and bundle.id is not None:
            saved = await self.modpackDB.resolution_get(bundle.id)
            prior = list(dict.fromkeys([*request.projects, *(e.project_id for e in saved.lock.entries)] if saved
                                       else request.projects))
        lock = await self._resolve(request, check=check, prior=prior, report=report)
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

    async def resolution_many(self, bundle_ids: Sequence[str]) -> dict[str, ModpackResolution]:
        """
        The saved resolution of each bundle that has one, by bundle id, for the dashboard (B2): what
        it leaves undecided or unresolved. Read only, never resolved again here, and without the
        staleness of `resolution` (which would read every pack's request); an invalid id is skipped.
        """
        found: dict[str, ModpackResolution] = {}
        for bundle_id in bundle_ids:
            if not PydanticObjectId.is_valid(bundle_id):
                continue
            saved = await self.modpackDB.resolution_get(PydanticObjectId(bundle_id))
            if saved is not None:
                found[bundle_id] = saved
        return found

    async def _resolution_save(self, bundle: ModpackBundle, request: ResolveRequest, lock: Lock) -> None:
        await self.modpackDB.resolution_save(ModpackResolution(
            bundle_id=bundle.id, resolved_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
            request_digest=request_digest(request), lock=lock))

    async def _request(self, bundle: ModpackBundle) -> ResolveRequest:
        global_rules = {r.project_id: r.replacement for r in await self.modpackDB.replacement_find()}
        floors = await self._floors(bundle)
        return ResolveRequest(
            target=Target(game_version=bundle.version, loader=str(bundle.loader.value),
                          channels=frozenset(bundle.channels),
                          bridges=frozenset(k for k in bundle.secondary_loaders
                                            if find_bridge(k, str(bundle.loader.value), bundle.version))),
            native_only=set(bundle.native_only),
            projects=[p.project_id for p in bundle.projects],
            origins={p.project_id: p.origin.value for p in bundle.projects},
            pins=bundle.pins, server_decisions=bundle.server_decisions, channel_exceptions=bundle.channel_exceptions,
            replacements=merge_replacements(global_rules, bundle.replacements), floors=floors)

    async def _floors(self, bundle: ModpackBundle) -> dict[str, list[str]]:
        """A mod never goes back (owner, 2026-10-12, d-7985ee-6dc34c): each project's versions in the
        build in force and in the last scan of every server linked to the pack. Only MongoDB is
        read: which of them is the floor (the newest that fits the target) the resolver decides,
        fetching through its usual path any version the catalog lacks."""
        return {project: sorted(versions) for project, versions in (await self._floor_sources(bundle)).items()}

    async def _floor_sources(self, bundle: ModpackBundle) -> dict[str, dict[str, list[str]]]:
        """`_floors` with who runs each version: project id -> version id -> ["el build 7",
        "el servidor «Survival»"]. Only MongoDB is read."""
        seen: dict[str, dict[str, list[str]]] = {}
        in_force = bundle.recommended_build or bundle.latest_build
        if in_force is not None:
            for entry in (await self.modpackDB.build_get(str(bundle.id), in_force)).lock.entries:
                seen.setdefault(entry.project_id, {}).setdefault(entry.version_id, []).append(f"el build {in_force}")
        for server in await self.pterodactylDB.server_find_by_bundle(str(bundle.id)):
            scan = await self.pterodactylDB.scan_latest(str(server.id), SCAN_DIRECTORY)
            for file in scan.files if scan is not None else []:
                if file.project_id and file.version_id:
                    users = seen.setdefault(file.project_id, {}).setdefault(file.version_id, [])
                    if f"el servidor «{server.name}»" not in users:
                        users.append(f"el servidor «{server.name}»")
        return seen

    async def _versions_known(self, version_ids: Sequence[str]) -> dict[str, ModrinthVersion]:
        """These versions from the catalog, and the ones it lacks from Modrinth in one request (then
        kept). A version neither knows is absent."""
        found = {v.version_id: v for v in await self.modrinthDB.version_find_by_version_ids(sorted(set(version_ids)))}
        missing = sorted(set(version_ids) - found.keys())
        if missing:
            fetched = list(await self.modrinthAPI.version_get_many(missing))
            if fetched:
                await self.modrinthDB.version_upsert_many(fetched)
            found.update({v.version_id: v for v in fetched})
        return found

    async def pin_downgrades(self, bundle: ModpackBundle, pins: Mapping[str, str],
                             fetch: bool = True) -> dict[str, PinDowngrade]:
        """The pins (project id -> version id) before their mod's floor (F27, d-7985ee-322302): the
        floor is the newest of the versions the build in force and the linked servers run that is
        for the pack's Minecraft version, and a pin goes back when it was published before it (a pin
        for another Minecraft version or loader is a branch change, not a step back). With `fetch`,
        a version the catalog lacks is read from Modrinth; without it, that pin is left out."""
        sources = await self._floor_sources(bundle)
        wanted = {p: v for p, v in pins.items() if p in sources}
        if not wanted:
            return {}
        ids = [*wanted.values(), *(v for p in wanted for v in sources[p])]
        if fetch:
            versions = await self._versions_known(ids)
        else:
            versions = {v.version_id: v for v in await self.modrinthDB.version_find_by_version_ids(sorted(set(ids)))}
        projects = {p.project_id: p for p in await self.modrinthDB.project_find_by_modrinth_id(sorted(wanted))}
        titles = {i: p.title for i, p in projects.items()}
        target = Target(game_version=bundle.version, loader=str(bundle.loader.value), channels=frozenset(bundle.channels))

        def fits(found: ModrinthVersion, project_id: str) -> bool:
            """As the resolver's floor (Target.fits): this game version, a loader the pack runs for
            the project's type, and listed (review, 2026-10-12)."""
            kind = projects[project_id].project_type.value if project_id in projects else "mod"
            return (bundle.version in found.game_versions and bool(target.loaders(kind) & set(found.loaders))
                    and str(found.status.value) == "listed")

        def facts(found: ModrinthVersion) -> VersionFacts:
            return VersionFacts(published=found.date_published, game_versions=tuple(found.game_versions),
                                loaders=tuple(found.loaders))

        found_downgrades: dict[str, PinDowngrade] = {}
        for project_id, pin_id in wanted.items():
            pinned = versions.get(pin_id)
            floors = [versions[v] for v in sources[project_id] if v in versions and fits(versions[v], project_id)]
            if pinned is None or not floors:
                continue
            floor = max(floors, key=lambda v: as_utc(v.date_published))
            if floor.version_id != pin_id and direction(facts(floor), facts(pinned)) == "down":
                found_downgrades[project_id] = PinDowngrade(
                    project_id=project_id, title=titles.get(project_id, project_id), floor_version=floor.version_id,
                    floor_number=floor.version_number, pin_version=pin_id, pin_number=pinned.version_number,
                    used_by=sources[project_id][floor.version_id])
        return found_downgrades

    async def _resolve(self, request: ResolveRequest, check: bool = False, prior: Sequence[str] = (),
                       local_only: bool = False, report: Optional[Report] = None) -> Lock:
        """
        Resolve from the catalog, asking Modrinth only for what it lacks (spec 2026-10-09-economia-de-
        requests, F2). With `check`, every project the catalog already accounts for is compared
        with Modrinth before it is used: the `prior` ones at once, the rest as resolution reaches
        them, each batch in one request for the projects and one for their new versions. Whatever
        is fetched is stored as it arrives: a failure halfway keeps it. With `local_only`, needing
        Modrinth raises _NeedsModrinth instead. `report` hears what is processed and how far.
        """
        catalog = Catalog()
        checked: set[str] = set()
        if check and prior:
            if report is not None:
                await report("Revisando cambios en Modrinth", 0, 1)
            await self._check(list(prior), request.target.game_version, checked)
        lock = resolve(request, catalog)
        for _ in range(RESOLVE_ROUNDS):
            if lock.complete:
                return lock
            await self._load(catalog, lock, request, check, checked, local_only, report)
            lock = resolve(request, catalog)
        return lock

    async def _load(self, catalog: Catalog, lock: Lock, request: ResolveRequest, check: bool, checked: set[str],
                    local_only: bool = False, report: Optional[Report] = None) -> None:
        """Put in `catalog` what `lock` needs: from MongoDB when the catalog accounts for it, else
        from Modrinth (each project's versions for the target, once; projects and versions by id,
        in batches)."""
        game_version = request.target.game_version
        wanted_types = sorted(lock.needs_projects | lock.needs_types)
        if wanted_types:
            stored = {p.project_id: p for p in await self.modrinthDB.project_find_by_modrinth_id(wanted_types)}
            since = self.clock() - LACKS_RECHECK
            unknown = await self.modrinthDB.unknown_find([i for i in wanted_types if i not in stored], since)
            missing = [i for i in wanted_types if i not in stored and i not in unknown]
            if missing and local_only:
                raise _NeedsModrinth()
            fetched = {p.project_id: p for p in await self._projects_fetch(missing)} if missing else {}
            # What Modrinth does not know is remembered: a day without asking again (F6).
            await self.modrinthDB.unknown_add([i for i in missing if i not in fetched], self.clock())
            for project_id in wanted_types:
                found = fetched.get(project_id) or stored.get(project_id)
                catalog.types[project_id] = found.project_type.value if found else "unknown"
        if lock.needs_projects:
            needed = sorted(lock.needs_projects)
            if check:
                await self._check([i for i in needed if i not in checked], game_version, checked)
            records = {(r.project_id, tuple(r.loaders)): r
                       for r in await self.modrinthDB.version_lists_find(needed, game_version)}
            targets = {i: sorted(request.target.loaders(catalog.types.get(i, "mod"))) for i in needed}
            cold = [i for i in needed if (i, tuple(targets[i])) not in records]
            if cold and local_only:
                raise _NeedsModrinth()
            fetched_cold = 0
            # Every listed version the catalog accounts for, in one query for the round (code review,
            # 2026-10-09: it was one per project).
            listed_ids = sorted({i for p in needed if (p, tuple(targets[p])) in records
                                 for i in records[(p, tuple(targets[p]))].version_ids})
            listed: dict[str, list[ModrinthVersion]] = {}
            for version in await self.modrinthDB.version_find_by_version_ids(listed_ids) if listed_ids else []:
                listed.setdefault(version.project_id, []).append(version)
            for project_id in needed:
                loaders = targets[project_id]
                record = records.get((project_id, tuple(loaders)))
                if record is not None:
                    versions = listed.get(project_id, [])
                else:
                    if report is not None:
                        await report("Versiones de los mods", fetched_cold, len(cold))
                    versions = await self._versions_fetch(project_id, loaders, game_version)
                    fetched_cold += 1
                    checked.add(project_id)
                catalog.by_project.setdefault(project_id, [])
                for version in versions:
                    if game_version in version.game_versions and set(loaders) & set(version.loaders):
                        catalog.add(candidate(version))
            if cold and report is not None:
                await report("Versiones de los mods", fetched_cold, len(cold))
        if lock.needs_bridged:
            bridged = sorted(lock.needs_bridged)
            guests = sorted(request.target.loaders_with_bridges("mod") - request.target.loaders("mod"))
            bridged_lists = {r.project_id: r for r in await self.modrinthDB.version_lists_find(bridged, game_version)
                             if r.loaders == guests}
            if local_only and any(i not in bridged_lists for i in bridged):
                raise _NeedsModrinth()
            for project_id in bridged:
                bridged_list = bridged_lists.get(project_id)
                if bridged_list is not None:
                    versions = await self.modrinthDB.version_find_listed(project_id, bridged_list.version_ids)
                else:
                    versions = await self._versions_fetch(project_id, guests, game_version)
                    checked.add(project_id)
                catalog.bridged_loaded.add(project_id)
                for version in versions:
                    if game_version in version.game_versions and set(guests) & set(version.loaders):
                        catalog.add(candidate(version))
        if lock.needs_versions:
            asked = sorted(lock.needs_versions)
            stored_versions = await self.modrinthDB.version_find_by_version_ids(asked)
            gone = await self.modrinthDB.gone_find(asked, self.clock() - LACKS_RECHECK)
            catalog.gone |= gone
            missing_ids = sorted(set(asked) - {v.version_id for v in stored_versions} - gone)
            if missing_ids and local_only:
                raise _NeedsModrinth()
            fetched_versions = list(await self.modrinthAPI.version_get_many(missing_ids)) if missing_ids else []
            if fetched_versions:
                await self.modrinthDB.version_upsert_many(fetched_versions)
            not_returned = set(missing_ids) - {v.version_id for v in fetched_versions}
            catalog.gone |= not_returned
            await self.modrinthDB.gone_add(sorted(not_returned), self.clock())
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
        found = list(await self.modrinthAPI.project_get_many(project_ids)) if project_ids else []
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
        fetched = list(await self.modrinthAPI.version_get_many(new_ids)) if new_ids else []
        if fetched:
            await self.modrinthDB.version_upsert_many(fetched)
        for record in records:
            listed = current.get(record.project_id, set())
            record.version_ids = sorted(listed)
            record.fetched_at = self.clock()
            await self.modrinthDB.version_list_save(record)

    # Builds (i-7985ee-098611).

    async def build_request(self, bundle_id: str, actor: Actor) -> Job:
        """
        "Crear build" in the panel (E9): resolved from the catalog first, with no request, a pack
        that cannot be built is refused at once (BuildBlocked, as build_create). Otherwise, or when
        the catalog lacks something, the build job: it checks Modrinth (bringing what is missing),
        resolves and freezes (build_create). Asking again while it is queued or running gives that job.
        """
        bundle = await self.modpackDB.bundle_get(bundle_id)
        request = await self._request(bundle)
        try:
            lock = await self._resolve(request, local_only=True)
        except _NeedsModrinth:
            # The catalog lacks something: the build job brings it itself, one click (if that leaves
            # the build blocked, the dialog opens as the job ends).
            pass
        else:
            await self._resolution_save(bundle, request, lock)
            _refuse_blocked(lock)
        key = f"build:{bundle_id}"
        try:
            job = await self.jobDB.enqueue("build_create", key=key, payload={"bundle_id": bundle_id},
                                           requested_by=actor.ref())
        except HTTP_409_CONFLICT:
            active = await self.jobDB.active_for(key)
            if active is None:
                raise
            return active
        await self.audit.record(actor, "build.request", _bundle(bundle_id), {"job_id": str(job.id)})
        return job

    async def last_failed_job(self, bundle_id: str) -> Optional[Job]:
        """The pack's newest job of its catalog or its build if it failed just now (code review,
        2026-10-09: a failed job ended with no word); None otherwise."""
        found = [j for j in [await self.jobDB.last_for_key(_catalog_key(bundle_id)),
                             await self.jobDB.last_for_key(f"build:{bundle_id}")]
                 if j is not None and j.status == "failed" and j.finished_at is not None]
        if not found:
            return None
        newest = max(found, key=lambda j: as_utc(j.finished_at))  # type: ignore[arg-type]
        return newest if self.clock() - as_utc(newest.finished_at) <= BLOCKED_BUILD_RECENT else None  # type: ignore[arg-type]

    async def last_build_blocked(self, bundle_id: str) -> bool:
        """Whether the pack's last build job ended just now without a build, blocked by what its check found."""
        last = await self.jobDB.last_for_key(f"build:{bundle_id}")
        if last is None or last.active or last.finished_at is None:
            return False
        finished = as_utc(last.finished_at)
        # Only just now: an old blocked build does not reopen the dialog when another job ends.
        return self.clock() - finished <= BLOCKED_BUILD_RECENT and bool(last.checkpoint.get("build", {}).get("blocked"))

    async def _build_run(self, job: Job, context: JobContext) -> None:
        bundle_id = job.payload["bundle_id"]
        asked = job.requested_by
        if asked is None:
            raise JobFailed("Un build sin quién lo pidió no se crea: no quedaría en la auditoría")
        # Who asked, for the audit: the identity is iss + sub; a build needs no operator role.
        actor = Actor(iss=asked.iss, sub=asked.sub, name=asked.name, groups=frozenset(), is_operator=False)

        async def run() -> dict[str, Any]:
            try:
                build = await self.build_create(bundle_id, actor, report=context.progress, job_id=str(job.id))
            except BuildBlocked as blocked:
                return {"blocked": True, "problems": len(blocked.lock.problems), "undecided": len(blocked.lock.undecided)}
            return {"number": build.number}

        await context.step("build", run)

    async def build_create(self, bundle_id: str, actor: Actor, report: Optional[Report] = None,
                           job_id: Optional[str] = None) -> ModpackBuild:
        """
        Resolve the bundle and freeze the lock as its next build; latest moves to it.

        Refused with 409 when the lock is not complete, has problems, or has mods whose place on
        the server waits for the admin: a build is something that can be deployed as it is.

        :return: The new build.
        """
        if job_id is not None:
            # A retry of the job that already inserted its build (code review, 2026-10-09): that one,
            # with the pointer moved (idempotent), never a second build.
            done = next((b for b in await self.modpackDB.build_find(bundle_id) if b.job_id == job_id), None)
            if done is not None:
                await self.modpackDB.bundle_latest_build(bundle_id, done.number)
                return done
        bundle = await self.modpackDB.bundle_get(bundle_id)
        request = await self._request(bundle)
        lock = await self._resolve_checked(bundle, request, check=True, report=report)
        if await self._reread_versions([e.version_id for e in lock.entries]):
            lock = await self._resolve(request, report=report)
        await self._resolution_save(bundle, request, lock)
        _refuse_blocked(lock)
        if report is not None:
            await report("Congelando el build", 1, 1)
        loader_version = bundle.loader_version or await self._loader_version(str(bundle.loader.value), bundle.version)
        builds = await self.modpackDB.build_find(bundle_id)
        number = (builds[0].number if builds else 0) + 1
        build = await self.modpackDB.build_insert(ModpackBuild(
            bundle_id=bundle.id, number=number, created_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
            loader_version=loader_version, local_files=bundle.local_files, request=request, lock=lock, job_id=job_id))
        await self.modpackDB.bundle_latest_build(bundle_id, number)
        await self.audit.record(actor, "build.create", _bundle(bundle_id), {"number": number})
        if self.notifier is not None:
            await self.notifier.notify(f"📦 {bundle.name}: build {number} creado, {len(lock.entries)} archivos.")
        return build

    async def _reread_versions(self, version_ids: list[str]) -> bool:
        """M3: read the lock's versions again, a hundred a request, and store them. An author can edit
        a version in place (game versions, loaders, channel, dependencies, files) and the project's
        version list does not show it. True if any stored one changed in a way resolution reads."""
        if not version_ids:
            return False
        fields = {"game_versions", "loaders", "version_type", "status", "dependencies", "files"}
        stored = {v.version_id: v.model_dump(include=fields, mode="json")
                  for v in await self.modrinthDB.version_find_by_version_ids(version_ids)}
        fresh = await self.modrinthAPI.version_get_many(version_ids)
        if fresh:
            await self.modrinthDB.version_upsert_many(fresh)
        return any(stored.get(v.version_id) != v.model_dump(include=fields, mode="json") for v in fresh)

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
                       numbers: Optional[dict[str, str]] = None, pins: Optional[Mapping[str, str]] = None) -> ChangePreview:
        """`change_preview` on maps of project id to version id; `numbers` are the version numbers
        already known, the others are read from the catalog (never from Modrinth), as are the dates
        that tell a step back (F27). `pins` are the pack's, to tell a step back that is a pin's."""
        changes = diff_entries(before, after)
        shown = {v for c in [*changes.added, *changes.removed, *changes.changed] for v in (c.from_version, c.to_version) if v}
        stored_shown = {v.version_id: v for v in await self.modrinthDB.version_find_by_version_ids(sorted(shown))}
        known = {**{i: v.version_number for i, v in stored_shown.items()}, **(numbers or {})}
        known = {v: n for v, n in known.items() if v in shown}

        def facts(version_id: Optional[str]) -> Optional[VersionFacts]:
            found = stored_shown.get(version_id) if version_id else None
            return None if found is None else VersionFacts(
                published=found.date_published, game_versions=tuple(found.game_versions), loaders=tuple(found.loaders))

        directions: dict[str, str] = {c.project_id: d for c in changes.changed
                      if (d := direction(facts(c.from_version), facts(c.to_version))) in ("down", "branch")}
        pinned = {c.project_id for c in changes.changed if pins and pins.get(c.project_id) == c.to_version}
        arriving = sorted({c.to_version for c in changes.added + changes.changed if c.to_version})
        changelogs: dict[str, Optional[str]] = {}
        for version_id in arriving:
            stored = stored_shown.get(version_id)
            if stored is not None and stored.changelog is not None:
                changelogs[version_id] = stored.changelog or None
        missing = [v for v in arriving if v not in changelogs]
        failed: set[str] = set()
        timed_out: set[str] = set()

        async def fetch() -> None:
            # M6: a hundred versions a request, changelog included (labrinth's default), instead
            # of one request per version. A version without text is stored empty, never asked again.
            for start in range(0, len(missing), ID_CHUNK):
                chunk = missing[start:start + ID_CHUNK]
                try:
                    versions = await self.modrinthAPI.version_get_many(chunk)
                except Exception:
                    logger.warning("changelogs of %d versions not available", len(chunk), exc_info=True)
                    failed.update(chunk)
                    continue
                versions = [v.model_copy(update={"changelog": v.changelog or ""}) for v in versions]
                if versions:
                    await self.modrinthDB.version_upsert_many(versions)
                for version in versions:
                    changelogs[version.version_id] = version.changelog or None
                failed.update(v for v in chunk if v not in changelogs)

        if missing:
            try:
                await asyncio.wait_for(fetch(), timeout=self.changelog_deadline)
            except asyncio.TimeoutError:
                timed_out.update(v for v in missing if v not in changelogs and v not in failed)
        return ChangePreview(changes=changes, changelogs=changelogs, failed=failed, timed_out=timed_out,
                             numbers=known, directions=directions, pinned=pinned)

    async def compare_builds(self, bundle_id: str, from_number: int, to_number: int) -> ChangePreview:
        """The change from one build of the bundle to another; 404 if either does not exist."""
        before = await self.modpackDB.build_get(bundle_id, from_number)
        after = before if to_number == from_number else await self.modpackDB.build_get(bundle_id, to_number)
        bundle = await self.modpackDB.bundle_get(bundle_id)
        return await self._preview({e.project_id: e.version_id for e in before.lock.entries},
                                   {e.project_id: e.version_id for e in after.lock.entries},
                                   {e.version_id: e.version_number for e in [*before.lock.entries, *after.lock.entries]},
                                   pins=bundle.pins)

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
        bundle = await self.modpackDB.bundle_get(server.bundle_id)
        preview = await self._preview(before, after, {e.version_id: e.version_number for e in build.lock.entries},
                                      pins=bundle.pins)
        # What the real update's preflight would refuse (update.py), from this scan rather than a fresh one.
        jars = unexplained_for(compare(scan.files, build.lock.entries, build.local_files, bundle.claimed_files), bundle)
        if jars.in_pack or jars.foreign:
            preview.unexplained, preview.scanned_at = jars, scan.scanned_at
        labels = {b.key: b.label for b in BRIDGES}
        bridged: dict[str, int] = {}
        for entry in build.lock.entries:
            if entry.server == "include" and entry.via is not None:
                label = labels.get(entry.via, entry.via)
                bridged[label] = bridged.get(label, 0) + 1
        preview.bridged = bridged
        return preview

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
