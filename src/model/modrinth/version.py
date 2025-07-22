from beanie import Document, Indexed
from datetime import datetime
from pydantic import BaseModel
from typing import Annotated, Optional
from src.model.utils import MinecraftLoaders
from src.library.datatypes import CustomEnum

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
    version_id: Annotated[str, Indexed(unique=True)]
    project_id: Annotated[str, Indexed(unique=True)]
    author_id: str

    name: str
    version_number: str
    version_type: ModrinthVersionType
    status: ModrinthVersionStatus
    downloads: int

    game_versions: Annotated[list[str], Indexed()]
    loaders: Annotated[list[MinecraftLoaders], Indexed()]
    featured: bool

    date_published: datetime
    dependencies: list[ModrinthVersionDependency]
    files: list[ModrinthVersionFile]

    class Settings:
        collection = "version"
        indexes = [
            "files.hashes.sha512"
        ]

    @staticmethod
    def parse(**kwargs) -> "ModrinthVersion":
        """
        Parse a dictionary into a ModrinthVersion instance.
        """
        kwargs["version_id"] = kwargs.pop("id")
        return ModrinthVersion(**kwargs)