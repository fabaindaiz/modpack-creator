from datetime import datetime
from typing import Annotated, Optional

from beanie import Document, Indexed


class LoaderVersion(Document):
    """The loader version the loader's meta gave for one loader and Minecraft version (U4/M4): kept
    24 h, forced by the manual refresh, and used past its age when the meta is down. `version` is
    None when the meta has none for that Minecraft version."""
    # "fabric|1.21.1": the unique key.
    key: Annotated[str, Indexed(unique=True)]
    version: Optional[str]
    fetched_at: datetime
