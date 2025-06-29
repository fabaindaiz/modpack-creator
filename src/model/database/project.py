from datetime import datetime
from pydantic import BaseModel, Field
from typing import Literal, Optional

class UnknownMod(BaseModel):
    """
    A model to represent an mod file that is not linked to any web repository.
    """
    type: Literal["UNKNOWN"] = "UNKNOWN"  # Default type is 'unknown'

    name: str
    size: int
    created_at: datetime
    modified_at: datetime

class ModrinthMod(BaseModel):
    """
    A model to represent a mod file that is linked to Modrinth.
    """
    type: Literal["MODRINTH"] = "MODRINTH"  # Default type is 'modrinth'