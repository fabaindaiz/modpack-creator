from datetime import datetime
from pydantic import BaseModel, Field
from typing import Literal, Optional
from src.library.utils.enum import CustomEnum

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

class ModrinthProject(BaseModel):
    id: str
    team: str

    slug: str
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
    game_versions: list[str]

    issues_url: Optional[str]
    source_url: Optional[str]
    wiki_url: Optional[str]

    published: datetime
    updated: datetime
