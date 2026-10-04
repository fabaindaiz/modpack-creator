from beanie import Document, Indexed, PydanticObjectId
from datetime import datetime
from typing import Annotated, Optional
from pydantic import BaseModel
from src.library.jarmeta import ModMetadata
from src.library.scan import ScanFile
from src.model.modpack import ModpackBundle

class ServerScan(Document):
    """What one directory of a server had at one moment (i-7985ee-43df2f, option 1): every jar
    with its hashes and the Modrinth version they match. Read from the server, never written to it;
    the next scan reuses the hashes of files that did not change."""
    server_id: Annotated[PydanticObjectId, Indexed()]
    directory: str
    scanned_at: datetime
    full: bool
    files: list[ScanFile]
    # How the scan compared with the server's build, as announced; None if it was not linked.
    drift: Optional[str] = None

class ProjectCandidate(BaseModel):
    """A Modrinth project a jar's metadata may be: by its mod id as a slug, or by a name search."""
    project_id: str
    slug: str
    title: str
    via: str

class ImportCandidate(BaseModel):
    """A jar Modrinth does not know by hash, with the projects its metadata points to. It joins
    the bundle only when the admin confirms one (d-7985ee-c8a15d)."""
    path: str
    sha512: str
    metadata: Optional[ModMetadata]
    candidates: list[ProjectCandidate]

class ImportResult(BaseModel):
    """What importing a server did: projects added, candidates waiting for the admin, jars
    registered as local files, and jars too large to read."""
    added: list[str]
    candidates: list[ImportCandidate]
    local: list[str]
    too_large: list[str]

class BundleForServer(BaseModel):
    """A bundle made for a server: created and linked, and what importing the server did, or why
    it failed (the bundle stays created and linked, and importing can be tried again)."""
    bundle: ModpackBundle
    imported: Optional[ImportResult]
    import_error: Optional[str]
