"""The origins of a pin (O2, the minimal base of O8; docs/research/2026-10-21-observaciones-ronda-3.md).

A mod's pin keeps where each candidate version came from (`PinRecord`): the imported pack's own
version, the admin's, what resolving freely takes, a scanned server's. One is in use. The bundle's
`pins` map still holds the version in use, so the resolver, the builds, the update check and the
/v1 API read pins as before; this module reads both together.

Pure rules, no database: how a pin reads with its origins (`pin_view`), what the resolver gets from
them (`resolution_inputs`), whether a version passes the pack's validation (`version_problem`),
whether importing a pack puts its version in use (`import_selects`), and what a scanned linked server
offers (`server_origins`).
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.library.bridges import bridge as find_bridge
from src.library.resolution import CHANNEL_RANK, OUTSIDE_TAGS, Candidate, Target
from src.model.modpack import ModpackBundle, PinOrigin, PinRecord, PinSource

__all__ = ["ORIGIN_LABEL", "OUTSIDE_TAGS", "PinView", "import_selects", "pin_view", "resolution_inputs",
           "pack_target", "server_origins", "server_origins_dropped", "version_problem"]

# How the panel names each origin.
ORIGIN_LABEL = {PinSource.pack: "versión del pack", PinSource.admin: "elegida a mano",
                PinSource.resolution: "resolución", PinSource.server: "servidor"}
# The origins whose version the resolver takes although Modrinth does not mark it for the pack.
UNCHECKED_SOURCES = (PinSource.pack, PinSource.server)


@dataclass(frozen=True)
class PinView:
    """A mod's pin as the panel shows it: its origins, and the one in use (by its key: a source has
    one origin per server)."""
    origins: list[PinOrigin]
    selected: PinSource
    selected_key: Optional[str] = None

    def __post_init__(self) -> None:
        if self.selected_key is None:
            object.__setattr__(self, "selected_key", self.selected.value)

    def origin(self, source: PinSource) -> Optional[PinOrigin]:
        return next((o for o in self.origins if o.source == source), None)

    def by_key(self, key: str) -> Optional[PinOrigin]:
        return next((o for o in self.origins if o.key == key), None)


def pack_target(bundle: ModpackBundle) -> Target:
    """What a pack resolves for: its Minecraft version, loader, channels and the bridges it has on."""
    loader = str(bundle.loader.value)
    return Target(game_version=bundle.version, loader=loader, channels=frozenset(bundle.channels),
                  bridges=frozenset(k for k in bundle.secondary_loaders if find_bridge(k, loader, bundle.version)))


def version_problem(version: Candidate, target: Target, exception: Optional[str] = None,
                    project_type: str = "mod") -> Optional[str]:
    """Why a version does not pass the pack's validation, the checks the resolver makes (listed, for
    the pack's Minecraft version and a loader it runs, in its channels or the mod's exception), in
    the panel's words; None when it passes."""
    if version.status != "listed":
        return "no está publicada en Modrinth"
    if target.game_version not in version.game_versions:
        return f"Modrinth no la marca para {target.game_version}"
    if not target.loaders_with_bridges(project_type) & set(version.loaders):
        return f"Modrinth no la marca para {target.loader}"
    channels = set(target.channels)
    if exception is not None:
        channels |= {c for c, rank in CHANNEL_RANK.items() if rank <= CHANNEL_RANK.get(exception, -1)}
    if version.version_type not in channels:
        return f"es {version.version_type}, fuera de los canales del pack"
    return None


def pin_view(bundle: ModpackBundle, project_id: str, resolved: Optional[str] = None) -> Optional[PinView]:
    """A mod's pin with its origins; None when it has neither a pin nor origins.

    The version pinned is what is in use: a pin with no record (stored before O2), or one changed
    where the origins were not (a PATCH of /v1), reads as the admin's. Without a pin, the resolution
    is in use, with `resolved` (the version the last resolution took) as its version."""
    record = bundle.pin_origins.get(project_id)
    pinned = bundle.pins.get(project_id)
    if record is None and pinned is None:
        return None
    origins = [o.model_copy() for o in record.origins] if record is not None else []
    if pinned is not None:
        source = record.selected if record is not None else None
        chosen = record.in_use() if record is not None else None
        if source is not None and source != PinSource.resolution and chosen is not None and chosen.version_id == pinned:
            return PinView(origins=origins, selected=source, selected_key=chosen.key)
        _put(origins, PinOrigin(source=PinSource.admin, version_id=pinned))
        return PinView(origins=origins, selected=PinSource.admin)
    if resolved is not None:
        _put(origins, PinOrigin(source=PinSource.resolution, version_id=resolved, valid=True))
    return PinView(origins=origins, selected=PinSource.resolution)


def _put(origins: list[PinOrigin], origin: PinOrigin) -> None:
    """Add `origin`, or replace the one of its source when its version differs."""
    for index, known in enumerate(origins):
        if known.source == origin.source:
            if known.version_id != origin.version_id:
                origins[index] = origin
            return
    origins.append(origin)


def resolution_inputs(bundle: ModpackBundle) -> tuple[set[str], set[str]]:
    """What the resolver gets from the origins: the pinned projects whose version in use is a pack's
    or a server's (taken even outside Modrinth's tags), and the pinned projects with an origin other
    than the admin, whose free choice it reports for their «resolución» origin."""
    unchecked: set[str] = set()
    free: set[str] = set()
    for project_id in bundle.pins:
        view = pin_view(bundle, project_id)
        if view is None:
            continue
        if view.selected in UNCHECKED_SOURCES:
            unchecked.add(project_id)
        if any(o.source not in (PinSource.admin, PinSource.resolution) for o in view.origins):
            free.add(project_id)
    return unchecked, free


def import_selects(current: Optional[PinRecord], downgrade: bool, pinned: bool = False) -> bool:
    """Whether importing a pack puts its version of a mod in use: only where nothing else was chosen
    (no pin, or the pack's own version in use) and never going back (d-7985ee-6dc34c: a step back
    is the person's choice, from the mod's card)."""
    if downgrade:
        return False
    if current is None:
        return not pinned
    return current.selected == PinSource.pack


def _this_server(origin: PinOrigin, server_id: str, server_name: str) -> bool:
    """Whether `origin` is the server's: by its id, or by its name for one stored without an id."""
    if origin.source != PinSource.server:
        return False
    return origin.server_id == server_id if origin.server_id is not None else origin.label == server_name


def server_origins(bundle: ModpackBundle, installed: Mapping[str, str], versions: Mapping[str, Candidate],
                   target: Target, server_id: str, server_name: str, at: datetime) -> dict[str, PinRecord]:
    """
    What scanning a linked server offers (owner, 2026-10-21): each pinned mod whose installed version
    differs from the pinned one gets (or updates) this server's «servidor» origin, labelled with its
    name and checked as the resolver checks the pack. One per linked server (owner, 2026-10-22): the
    others' stay, and this one goes when the server runs the pinned version again or no longer has
    the mod. Never put in use: the pin, and the origin in use, stay; the person picks it with «Usar
    esta», which asks before going back (d-7985ee-6dc34c, d-7985ee-56a529). Nothing for a mod
    without a pin, nor where nothing changes. This server's origin in use is left as it is: replacing
    its version would leave the pin reading as another's choice.

    :param installed: Project id → the version id the server runs (identified jars of the scan).
    :param versions: Version id → the version, from the catalog; one missing is offered unchecked.
    :return: Project id → its record to write, only for the mods that change.
    """
    found: dict[str, PinRecord] = {}
    for project_id, pinned in bundle.pins.items():
        record = bundle.pin_origins.get(project_id)
        if record is None:
            # A pin stored before O2 is the admin's: kept, and in use.
            record = PinRecord(origins=[PinOrigin(source=PinSource.admin, version_id=pinned)], selected=PinSource.admin)
        mine = next((o for o in record.origins if _this_server(o, server_id, server_name)), None)
        if mine is not None and record.in_use() is mine:
            continue
        running = installed.get(project_id)
        if running is None or running == pinned:
            if mine is not None:
                found[project_id] = record.model_copy(update={"origins": [o for o in record.origins if o is not mine]})
            continue
        if mine is not None and (mine.version_id, mine.server_id, mine.label) == (running, server_id, server_name):
            continue
        version = versions.get(running)
        problem = version_problem(version, target, bundle.channel_exceptions.get(project_id)) if version else None
        origin = PinOrigin(source=PinSource.server, version_id=running,
                           version_number=version.version_number if version else None,
                           valid=(problem is None) if version else None, problem=problem, label=server_name, at=at,
                           server_id=server_id)
        if mine is not None and mine.version_id == running:
            # Stored without its id: the same offer, now with it.
            origin = mine.model_copy(update={"server_id": server_id, "label": server_name})
        origins = [origin if o is mine else o for o in record.origins]
        if mine is None:
            origins.append(origin)
        found[project_id] = record.model_copy(update={"origins": origins})
    return found


def server_origins_dropped(bundle: ModpackBundle, server_id: str, server_name: str) -> dict[str, PinRecord]:
    """What unlinking a server takes from its old pack (owner, 2026-10-22): its «servidor» origins,
    except one in use, which is the pin itself and stays until the person changes it.

    :return: Project id → its record to write, only for the mods that change.
    """
    found: dict[str, PinRecord] = {}
    for project_id, record in bundle.pin_origins.items():
        used = record.in_use()
        kept = [o for o in record.origins if o is used or not _this_server(o, server_id, server_name)]
        if len(kept) != len(record.origins):
            found[project_id] = record.model_copy(update={"origins": kept})
    return found
