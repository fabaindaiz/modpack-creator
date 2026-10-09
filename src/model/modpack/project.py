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
    # Modrinth project id → substitute project id, or None to drop it. Added to the global
    # rules, and wins over them for the same project (d-7985ee-86fffa).
    replacements: dict[str, Optional[str]] = Field(default_factory=dict)
    # Modrinth project id → whether an ambiguous mod goes on the server (d-7985ee-f0c7a6).
    server_decisions: dict[str, bool] = Field(default_factory=dict)
    # Modrinth project id → the least stable channel it also accepts ("beta" or "alpha"): the
    # admin's choice, or the version a server import found. It only has an effect while none of the
    # project's versions fits in `channels` (decided by the owner, 2026-10-09).
    channel_exceptions: dict[str, str] = Field(default_factory=dict)

    # Build pointers (i-7985ee-098611): latest follows the newest build; recommended moves only
    # when the admin moves it, and going back is moving it to an older build.
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
