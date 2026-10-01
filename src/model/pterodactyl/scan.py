from beanie import Document, Indexed, PydanticObjectId
from datetime import datetime
from typing import Annotated
from src.library.scan import ScanFile

class ServerScan(Document):
    """What one directory of a server had at one moment (i-7985ee-43df2f, option 1): every jar
    with its hashes and the Modrinth version they match. Read from the server, never written to it;
    the next scan reuses the hashes of files that did not change."""
    server_id: Annotated[PydanticObjectId, Indexed()]
    directory: str
    scanned_at: datetime
    full: bool
    files: list[ScanFile]
