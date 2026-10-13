import logging
from datetime import datetime
from typing import Any, Callable, Optional, Protocol, runtime_checkable
from fastapi import HTTPException
from src.database.audit import AuditDatabase
from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.library.logging import request_id
from src.model.audit import AuditEvent, AuditTarget
from src.model.users import Actor
from src.library.utils import utcnow

logger = logging.getLogger("modpack.audit")
SECRET_KEYS = ("token", "secret", "password", "authorization")
MAX_LIMIT = 200

def _check_details(details: Any) -> None:
    """Refuse a key that looks like a secret, at any depth: the log is read by many people."""
    if isinstance(details, dict):
        for key, value in details.items():
            if any(word in str(key).lower() for word in SECRET_KEYS):
                raise ValueError(f"audit details must not hold '{key}'")
            _check_details(value)
    elif isinstance(details, (list, tuple)):
        for item in details:
            _check_details(item)

@runtime_checkable
class AuditSink(Protocol):
    """Where a service records what an actor asked for: AuditService, or a test double."""
    async def record(self, actor: Actor, action: str, target: AuditTarget,
                     details: Optional[dict[str, Any]] = None) -> Any: ...

class TargetNames():
    """The name of an event's target, looked up when the event is written (A10): a pack's name, a
    server's, or the catalog's title of a replacement's mod. Any other kind, an id that is not there
    or a failing read is no name: naming never stops an event from being written."""
    def __init__(self,
            modpackDB: ModpackDatabase,
            pterodactylDB: PterodactylDatabase,
            modrinthDB: ModrinthDatabase,
            ) -> None:
        self.modpackDB = modpackDB
        self.pterodactylDB = pterodactylDB
        self.modrinthDB = modrinthDB

    async def name(self, target: AuditTarget) -> Optional[str]:
        try:
            if target.kind == "bundle":
                return (await self.modpackDB.bundle_get(target.id)).name
            if target.kind == "server":
                return (await self.pterodactylDB.server_get(target.id)).name
            if target.kind == "replacement":
                found = await self.modrinthDB.project_find_by_modrinth_id([target.id])
                return found[0].title if found else None
        except HTTPException:
            return None  # not there (or not an id): shown by its kind and id
        except Exception:
            logger.warning("naming an audit target failed", exc_info=True,
                           extra={"kind": target.kind, "target_id": target.id})
        return None

    async def titles(self, project_ids: set[str], version_ids: set[str]) -> dict[str, str]:
        """The catalog's title of each project and number of each version it has, in one query
        each; an id it does not have is left out (shown raw)."""
        found: dict[str, str] = {}
        for version in await self.modrinthDB.version_find_by_version_ids(sorted(version_ids)):
            found[version.version_id] = version.version_number
        for project in await self.modrinthDB.project_find_by_modrinth_id(sorted(project_ids)):
            found[project.project_id] = project.title
        return found

# The detail keys that hold Modrinth project ids (a value, a list of them, or a map's keys) and the
# ones that hold version ids: what the audit page names from the catalog.
_PROJECT_VALUES = ("project_id", "replacement", "refs", "added")
_PROJECT_KEYS = ("decisions", "channel_exceptions")
_VERSION_VALUES = ("version_id",)

def _ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []

class AuditService():
    def __init__(self,
            auditDB: AuditDatabase,
            names: Optional[TargetNames] = None,
            clock: Callable[[], datetime] = utcnow,
            ) -> None:
        self.auditDB = auditDB
        self.names = names
        self.clock = clock

    async def record(self, actor: Actor, action: str, target: AuditTarget, details: Optional[dict[str, Any]] = None) -> AuditEvent:
        """Write who did what to what. The request id comes from the log's context."""
        details = dict(details or {})
        _check_details(details)
        if target.name is None and self.names is not None:
            target = target.model_copy(update={"name": await self.names.name(target)})
        return await self.auditDB.insert(AuditEvent(
            at=self.clock(), actor=actor.ref(), action=action, target=target,
            details=details, request_id=request_id()))

    async def names_for(self, events: list[AuditEvent]) -> dict[str, str]:
        """Id to name for the mods and versions the details of a page of events name: one catalog
        query for the projects and one for the versions. Without the catalog, or when it fails, no
        names: the ids are shown raw (a nicety, never a reason not to show the log)."""
        if self.names is None:
            return {}
        projects: set[str] = set()
        versions: set[str] = set()
        for event in events:
            for key in _PROJECT_VALUES:
                projects.update(_ids(event.details.get(key)))
            for key in _PROJECT_KEYS:
                if isinstance(event.details.get(key), dict):
                    projects.update(k for k in event.details[key] if isinstance(k, str))
            for key in _VERSION_VALUES:
                versions.update(_ids(event.details.get(key)))
        if not projects and not versions:
            return {}
        try:
            return await self.names.titles(projects, versions)
        except Exception:
            logger.warning("naming the mods of the audit log failed", exc_info=True)
            return {}

    async def find(self, *,
            actor_sub: Optional[str] = None,
            action: Optional[str] = None,
            target_id: Optional[str] = None,
            before_id: Optional[str] = None,
            limit: int = 50,
            since: Optional[datetime] = None,
            until: Optional[datetime] = None,
            ) -> list[AuditEvent]:
        """Events newest first; `before_id` is the id of the last event of the previous page; `limit` is held to 1..200;
        `since` (inclusive) and `until` (exclusive) are aware UTC datetimes."""
        return await self.auditDB.find(
            actor_sub=actor_sub, action=action, target_id=target_id, before_id=before_id,
            limit=max(1, min(limit, MAX_LIMIT)), since=since, until=until)
