from beanie import Document, Indexed
from datetime import datetime
from pydantic import Field
from typing import Annotated, Optional
from src.library.datatypes import CustomEnum

class ModrinthProjectSide(CustomEnum):
    required = "required"
    optional = "optional"
    unsupported = "unsupported"
    unknown = "unknown"

class ModrinthEnvironment(CustomEnum):
    """Where a project or version runs (Modrinth openapi.yaml v2.7.0, EnvironmentEnum)."""
    client_and_server = "client_and_server"
    client_only = "client_only"
    client_only_server_optional = "client_only_server_optional"
    singleplayer_only = "singleplayer_only"
    server_only = "server_only"
    server_only_client_optional = "server_only_client_optional"
    dedicated_server_only = "dedicated_server_only"
    client_or_server = "client_or_server"
    client_or_server_prefers_both = "client_or_server_prefers_both"
    unknown = "unknown"

class ModrinthProjectType(CustomEnum):
    mod = "mod"
    modpack = "modpack"
    resourcepack = "resourcepack"
    shader = "shader"

class ModrinthProjectStatus(CustomEnum):
    approved = "approved"
    archived = "archived"
    rejected = "rejected"
    draft = "draft"
    unlisted = "unlisted"
    processing = "processing"
    withheld = "withheld"
    scheduled = "scheduled"
    private = "private"
    unknown = "unknown"

class ModrinthProject(Document):
    project_id: Annotated[str, Indexed(unique=True)]
    slug: Annotated[str, Indexed(unique=True)]
    team: str
    title: str
    description: str
    body: str

    # environment replaces the deprecated sides (i-7985ee-f4ba63). Both are optional: documents
    # stored before environment lack it, and Modrinth may drop the sides.
    environment: list[ModrinthEnvironment] = Field(default_factory=list)
    client_side: Optional[ModrinthProjectSide] = None
    server_side: Optional[ModrinthProjectSide] = None
    project_type: ModrinthProjectType
    status: ModrinthProjectStatus
    downloads: int
    followers: int

    categories: list[str]
    additional_categories: list[str]

    versions: list[str]
    game_versions: Annotated[list[str], Indexed()]
    # Modrinth's loaders are an open set: a shader has iris and optifine, a datapack datapack.
    loaders: Annotated[list[str], Indexed()]

    issues_url: Optional[str]
    source_url: Optional[str]
    wiki_url: Optional[str]
    # Absent when Modrinth gives no icon; the panel only shows one from cdn.modrinth.com.
    icon_url: Optional[str] = None

    published: datetime
    updated: datetime
    # When the catalog last stored it from Modrinth; None on projects stored before it was kept.
    fetched_at: Optional[datetime] = None

    @staticmethod
    def parse(**kwargs) -> "ModrinthProject":
        """
        Parse a dictionary into a ModrinthProject instance.
        """
        kwargs["project_id"] = kwargs.pop("id")
        return ModrinthProject(**kwargs)