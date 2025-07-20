from beanie import Document, Indexed
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Annotated, Literal, Optional
from src.library.utils.enum import CustomEnum

class ModrinthVersionType(CustomEnum):
    release = "release"
    beta = "beta"
    alpha = "alpha"

class ModrinthVersionStatus(CustomEnum):
    listed = "listed"
    archived = "archived"
    draft = "draft"
    unlisted = "unlisted"
    scheduled = "scheduled"
    unknown = "unknown"

class ModrinthHashes(BaseModel):
    sha512: str
    sha1: str

class ModrinthDependencyType(CustomEnum):
    required = "required"
    optional = "optional"
    incompatible = "incompatible"
    emmbedded = "embedded"

class ModrinthVersionDependency(BaseModel):
    version_id: Optional[str]
    project_id: Optional[str]
    file_name: Optional[str]
    dependency_type: ModrinthDependencyType

class ModrinthVersionFile(BaseModel):
    hashes: ModrinthHashes
    url: str
    filename: str 
    primary: bool
    size: int

class ModrinthVersion(Document):
    project_id: str
    author_id: str

    name: str
    version_number: str
    version_type: ModrinthVersionType
    status: ModrinthVersionStatus
    downloads: int

    loaders: list[str]
    game_versions: list[str]
    featured: bool

    date_published: datetime
    dependencies: list[ModrinthVersionDependency]
    files: list[ModrinthVersionFile]

    class Settings:
        collection = "version"