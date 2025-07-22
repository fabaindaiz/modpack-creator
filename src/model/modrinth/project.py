from beanie import Document, Indexed
from datetime import datetime
from typing import Annotated, Optional
from src.model.utils import MinecraftLoaders
from src.library.datatypes import CustomEnum

class ModrinthProjectSide(CustomEnum):
    required = "required"
    optional = "optional"
    unsupported = "unsupported"
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

    client_side: ModrinthProjectSide
    server_side: ModrinthProjectSide
    project_type: ModrinthProjectType
    status: ModrinthProjectStatus
    downloads: int
    followers: int

    categories: list[str]
    additional_categories: list[str]

    versions: list[str]
    game_versions: Annotated[list[str], Indexed()]
    loaders: Annotated[list[MinecraftLoaders], Indexed()]

    issues_url: Optional[str]
    source_url: Optional[str]
    wiki_url: Optional[str]

    published: datetime
    updated: datetime

    class Settings:
        collection = "project"
    
    @staticmethod
    def parse(**kwargs) -> "ModrinthProject":
        """
        Parse a dictionary into a ModrinthProject instance.
        """
        kwargs["project_id"] = kwargs.pop("id")
        return ModrinthProject(**kwargs)