from beanie import Document, Indexed
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Annotated, Literal, Optional
from src.library.datatypes import CustomEnum

class PterodactylInstance(Document):
    name: str
    api_url: str
    token: str

    class Settings:
        collection = "instance"