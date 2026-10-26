from beanie import Document, Indexed, PydanticObjectId
from datetime import datetime
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel
from typing import Annotated, Optional
from src.library.datatypes import CustomEnum
from src.library.resolution import Lock, ResolveRequest
from src.library.scan import ClaimedFile, LocalFile
from src.model.utils import MinecraftLoaders

class ProjectOrigin(CustomEnum):
    """Where a project of the pack came from (i-7985ee-063a3a, d-7985ee-b15dd4). Both resolve the
    same way; replacing the pack's content keeps what the admin added."""
    pack = "pack"
    admin = "admin"

class PinSource(CustomEnum):
    """Where a pinned version came from (O2, the minimal base of O8): the imported pack's own version,
    the admin's choice, what resolving freely takes, or what a scanned server runs."""
    pack = "pack"
    admin = "admin"
    resolution = "resolution"
    server = "server"

class PinOrigin(BaseModel):
    """One candidate version of a pinned mod, with where it came from and whether it passes the
    pack's validation (its Minecraft version, loader and channels, as the resolver checks them)."""
    source: PinSource
    version_id: str
    version_number: Optional[str] = None
    # None when it was not checked (the version was not in the catalog).
    valid: Optional[bool] = None
    # Why it does not pass, in the panel's words («Modrinth no la marca para 1.20.1»).
    problem: Optional[str] = None
    # What named it: the imported pack and its version, a server.
    label: Optional[str] = None
    at: Optional[datetime] = None
    # The server whose scan offered it: one «servidor» origin per linked server (owner, 2026-10-22).
    # None for the other sources, and for a server's origin stored before (it is known by its label).
    server_id: Optional[str] = None

    @property
    def key(self) -> str:
        """What tells it from the others of the mod: its source, and a server's id."""
        return f"{self.source.value}:{self.server_id}" if self.server_id is not None else self.source.value

class PinRecord(BaseModel):
    """A mod's pin with its origins (O2): one per source, and the one the pack uses. `selected` is
    `resolution` (or None) when nothing is pinned: the resolver chooses freely. The bundle's `pins`
    keeps the version in use, so everything that reads pins reads it as before."""
    origins: list[PinOrigin] = Field(default_factory=list)
    selected: Optional[PinSource] = None
    # Which origin of `selected` is in use, by its key, where a source has several (a server each);
    # None reads as the first of the source.
    selected_key: Optional[str] = None
    # How many times it was written: a write carries the revision it read, and is refused if another
    # wrote meanwhile (review, 2026-10-22). 0 for a record stored before, or none at all.
    rev: int = 0

    def origin(self, source: PinSource) -> Optional[PinOrigin]:
        return next((o for o in self.origins if o.source == source), None)

    def by_key(self, key: str) -> Optional[PinOrigin]:
        return next((o for o in self.origins if o.key == key), None)

    def in_use(self) -> Optional[PinOrigin]:
        """The origin the pack uses; None when nothing is pinned."""
        if self.selected is None:
            return None
        if self.selected_key is not None:
            return self.by_key(self.selected_key)
        return self.origin(self.selected)

class PastOrigin(BaseModel):
    """An origin a pin no longer has (O8 D2): replaced, dropped, or gone with its mod (`removed_mod`,
    P5 of d-7985ee-a68b5c), and when (`until`)."""
    origin: PinOrigin
    until: datetime
    removed_mod: bool = False

class PackProject(BaseModel):
    """A Modrinth project in a bundle, by its Modrinth id: a reference, not a copy of the project
    that would go stale as the catalog refreshes (i-7985ee-a17c2c)."""
    project_id: str
    origin: ProjectOrigin

class ModpackBundleCreate(BaseModel):
    """What a client may set when creating a bundle: never its content or build pointers, which
    only ModpackService changes."""
    name: str
    description: str
    version: str
    loader: MinecraftLoaders
    loader_version: Optional[str] = None
    channels: list[str] = ["release"]
    pins: dict[str, str] = {}
    replacements: dict[str, Optional[str]] = {}
    server_decisions: dict[str, bool] = {}

class ModpackBundlePatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    loader: Optional[MinecraftLoaders] = None
    loader_version: Optional[str] = None
    channels: Optional[list[str]] = None
    # The maps change key by key: a key left out stays. In pins and server_decisions a null value
    # removes the key (RFC 7396); in replacements null keeps its meaning, "drop the mod", so a
    # pack's rule is removed with its own route.
    pins: Optional[dict[str, Optional[str]]] = None
    replacements: Optional[dict[str, Optional[str]]] = None
    server_decisions: Optional[dict[str, Optional[bool]]] = None
    # The bridges turned on (batch C, d-7985ee-a8d395): the whole list is replaced.
    secondary_loaders: Optional[list[str]] = None

# The fields of ModpackBundlePatch written key by key, and those where a null value removes the key.
BUNDLE_KEYED_FIELDS = ("pins", "replacements", "server_decisions")
BUNDLE_NULL_REMOVES = ("pins", "server_decisions")

class ModpackBundle(Document):
    """A modpack definition: what to resolve. What gets deployed is a build of it."""
    name: str
    description: str
    # The game version.
    version: str
    loader: MinecraftLoaders
    # The loader version, fixed by the admin. If unset, a build takes the stable one from the
    # loader's meta (Fabric, Quilt); NeoForge and Forge have none, so they need it set to export.
    loader_version: Optional[str] = None

    # Changed only through ModpackService, with atomic updates: concurrent additions all stay.
    projects: list[PackProject] = Field(default_factory=list)
    # Jars not on Modrinth, by sha512 (d-7985ee-c8a15d): kept on the server, never exported.
    local_files: list[LocalFile] = Field(default_factory=list)
    # Jars on servers the admin confirmed as a project of this bundle (import candidates).
    claimed_files: list[ClaimedFile] = Field(default_factory=list)

    # What resolution reads (i-7985ee-7f735a). Defaults, so bundles stored before load as before.
    # Version channels allowed, most stable first.
    channels: list[str] = Field(default_factory=lambda: ["release"])
    # Modrinth project id → version id the admin fixed.
    pins: dict[str, str] = Field(default_factory=dict)
    # Modrinth project id → the origins of its pin (O2): where each candidate version came from and
    # which one is in use. A pin without a record (every pin stored before O2) reads as the admin's.
    pin_origins: dict[str, PinRecord] = Field(default_factory=dict)
    # Modrinth project id → what its pin's origins replaced or dropped, oldest first (O8 D2): written
    # with each record, and kept when the mod is removed (P5). The history shows it (`past_origins`).
    pin_history: dict[str, list[PastOrigin]] = Field(default_factory=dict)
    # Modrinth project id → substitute project id, or None to drop it. Added to the global
    # rules, and wins over them for the same project (d-7985ee-86fffa).
    replacements: dict[str, Optional[str]] = Field(default_factory=dict)
    # Modrinth project id → whether an ambiguous mod goes on the server (d-7985ee-f0c7a6).
    server_decisions: dict[str, bool] = Field(default_factory=dict)
    # Modrinth project id → the least stable channel it also accepts ("beta" or "alpha"): the
    # admin's choice, or the version a server import found. It only has an effect while none of the
    # project's versions fits in `channels` (decided by the owner, 2026-10-09).
    channel_exceptions: dict[str, str] = Field(default_factory=dict)
    # The bridges the pack turned on (batch C, d-7985ee-a8d395): keys of src/library/bridges.py.
    # Kept when the Minecraft version changes; only those that apply to it take effect.
    secondary_loaders: list[str] = Field(default_factory=list)
    # Mods moved to their native versions («Pasar a nativa», d-7985ee-8e5f9e).
    native_only: list[str] = Field(default_factory=list)

    # Build pointers (i-7985ee-098611): latest follows the newest build; recommended moves only
    # when the admin moves it, and going back is moving it to an older build.
    # When its Modrinth information was last compared with Modrinth (spec 2026-10-09-economia-de-requests, E1).
    modrinth_checked_at: Optional[datetime] = None
    # When "Actualizar desde Modrinth" last ran: once a minute per pack (E3).
    manual_refreshed_at: Optional[datetime] = None
    latest_build: Optional[int] = None
    recommended_build: Optional[int] = None

class ModpackBuild(Document):
    """A resolved bundle, frozen (i-7985ee-098611): numbered per bundle and never edited, so a
    deploy can always say exactly what it installs, and go back to it."""
    bundle_id: PydanticObjectId
    number: int
    created_at: datetime
    # The loader version frozen with the build; None if there was none to take (i-7985ee-7352b3).
    loader_version: Optional[str] = None
    # The build job that made it, if one did: a retry of that job finds it instead of making another.
    job_id: Optional[str] = None
    # A test build (O8 B1, d-7985ee-a99db7): numbered like any other, never the latest nor the
    # recommended until approved. Its name and star (B2, B3) are the only other fields ever written
    # after it is made, each with its own $set: what it froze never changes.
    test: bool = False
    name: Optional[str] = None
    starred: bool = False
    # The bundle's local files when the build was made.
    local_files: list[LocalFile] = Field(default_factory=list)
    request: ResolveRequest
    lock: Lock

    class Settings:
        # Two builds created at once cannot share a number: the second insert fails.
        indexes = [IndexModel([("bundle_id", ASCENDING), ("number", ASCENDING)], unique=True)]

class ModpackResolution(Document):
    """The latest resolution of a bundle, kept only to show it (spec §3): never the input of a
    build, which always resolves again. `request_digest` says which request produced it, so the
    page can tell when the pack has changed since."""
    bundle_id: Annotated[PydanticObjectId, Indexed(unique=True)]
    resolved_at: datetime
    request_digest: str
    lock: Lock

class ModpackReplacement(Document):
    """A global replacement rule (d-7985ee-86fffa): wherever a dependency asks for project_id,
    use `replacement` instead, or nothing if it is None."""
    project_id: Annotated[str, Indexed(unique=True)]
    replacement: Optional[str]
