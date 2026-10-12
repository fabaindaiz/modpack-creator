from datetime import datetime
from typing import Annotated, Any, Optional

from beanie import Document, Indexed
from pydantic import BaseModel

from src.model.pterodactyl.server import PterodactylServer


class PanelServerList(Document):
    """The panel's list of servers as last asked (U6): one document, kept 5 minutes. `servers` holds
    each server as the panel gave it (without our fields), to show what can be brought in."""
    key: Annotated[str, Indexed(unique=True)] = "panel"
    servers: list[dict[str, Any]]
    fetched_at: datetime


class PanelServers(BaseModel):
    """What /servers shows of the panel: the servers, when they were asked (None: never), and
    whether the panel just failed to answer (then the kept list, however old)."""
    servers: list[PterodactylServer]
    fetched_at: Optional[datetime]
    failed: bool = False
