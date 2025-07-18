from datetime import datetime
from pydantic import BaseModel, Field
from typing import Literal, Optional

class PterodactylFile(BaseModel):
    name: str
    size: int
    mode_bits: str

    is_file: bool
    is_symlink: bool
    mimetype: str

    created_at: datetime
    modified_at: datetime

class PterodactylServer(BaseModel):
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