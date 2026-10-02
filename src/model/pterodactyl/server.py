from beanie import Document, Indexed
from datetime import datetime
from pydantic import BaseModel
from typing import Annotated, Optional

class PterodactylPermission(BaseModel):
    description: str
    keys: dict[str, str]

class PterodactylAccount(BaseModel):
    admin: bool
    email: str
    username: str
    first_name: str
    last_name: str
    language: str

class PterodactylFile(BaseModel):
    name: str
    size: int
    mode_bits: str

    is_file: bool
    is_symlink: bool
    mimetype: str

    created_at: datetime
    modified_at: datetime

class PterodactylServer(Document):
    identifier: str
    internal_id: int
    uuid: str

    name: str
    node: str
    description: str

    status: Optional[str]
    is_suspended: bool
    is_installing: bool
    is_transferring: bool

    # The bundle this server runs: what a scan is compared with by default. Ours, not the
    # panel's, so refreshing the server from the panel keeps it.
    bundle_id: Optional[str] = None

class ServerState(BaseModel):
    """What the panel says about a server before touching it: its status (None when idle; else
    installing, restoring_backup...), flags, and how many backups it may keep."""
    status: Optional[str]
    is_suspended: bool
    is_installing: bool
    is_transferring: bool
    backup_limit: int

class ServerResources(BaseModel):
    """Its power state (offline, starting, running, stopping) and uptime. Cached by the panel
    for up to 20 s: a state is confirmed by polling, never by the 204 of a power signal."""
    current_state: str
    is_suspended: bool
    uptime_ms: int

class PterodactylBackup(BaseModel):
    uuid: str
    name: str
    is_successful: bool
    is_locked: bool
    bytes: int
    created_at: datetime
    completed_at: Optional[datetime]
