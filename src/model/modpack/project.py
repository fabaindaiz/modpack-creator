from abc import ABC, abstractmethod
from beanie import Document, Link, Indexed
from pydantic import BaseModel, Field
from typing import Annotated, Literal, Optional
from src.model.modrinth import ModrinthProject
from src.model.utils import MinecraftLoaders
from src.library.datatypes import CustomEnum

class ProjectSourceList(CustomEnum):
    MODRINTH = "modrinth"
    UNKNOWN = "unknown"

class ProjectSourceBase(ABC, BaseModel):
    pass

class ProjectSourceModrinth(ProjectSourceBase):
    source: Literal[ProjectSourceList.MODRINTH] = ProjectSourceList.MODRINTH
    project: Link[ModrinthProject]

class ProjectSourceUnknown(ProjectSourceBase):
    source: Literal[ProjectSourceList.UNKNOWN] = ProjectSourceList.UNKNOWN

type ProjectSource = Annotated[
    ProjectSourceModrinth |
    ProjectSourceUnknown,
    Field(discriminator="source", default=ProjectSourceUnknown())
]

class ModpackProjectPatch(BaseModel):
    pass

class ModpackProject(Document):
    modpack: Link["ModpackBundle"]
    source: ProjectSource

    class Settings:
        collection = "project"


class ModpackBundlePatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    loader: Optional[MinecraftLoaders] = None

class ModpackBundle(Document):
    name: str
    description: str
    version: str
    loader: MinecraftLoaders

    class Settings:
        collection = "bundle"