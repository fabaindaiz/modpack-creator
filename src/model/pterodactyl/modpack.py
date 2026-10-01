from beanie import Document, Link, Indexed
from datetime import datetime
from typing import Annotated, Optional
from src.model.pterodactyl.server import PterodactylFile, PterodactylServer
from src.model.utils import FileHash

class PterodactylModpack(Document):
    server: Link[PterodactylServer]
    timestamp: datetime

class PterodactylVersion(Document):
    server: Link[PterodactylServer]
    file: PterodactylFile

    hashes: Optional[FileHash] = None

    class Settings:
        collection = "version"