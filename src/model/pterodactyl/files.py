from datetime import datetime
from pydantic import BaseModel

class FileObject(BaseModel):
    name: str
    mode: str
    size: int
    is_file: bool
    is_symlink: bool
    mimetype: str
    created_at: datetime
    modified_at: datetime