from pydantic import BaseModel, Field
from typing import Annotated, Literal
from src.library.utils.enum import CustomEnum

class ProjectSourceList(CustomEnum):
    MODRINTH = "modrinth"
    UNKNOWN = "unknown"

class ProjectSourceBase(BaseModel):
    pass

class ProjectSourceModrinth(ProjectSourceBase):
    source: Literal[ProjectSourceList.MODRINTH] = ProjectSourceList.MODRINTH
    project_id: str

class ProjectSourceUnknown(ProjectSourceBase):
    source: Literal[ProjectSourceList.UNKNOWN] = ProjectSourceList.UNKNOWN

type ProjectSource = Annotated[
    ProjectSourceModrinth |
    ProjectSourceUnknown,
    Field(discriminator="source", default=ProjectSourceUnknown())
]

class LocalProject(BaseModel):
    id: str = ""
    source: ProjectSource