"""The target's name an audit event keeps (A10), looked up when it is written: never a reason not to
write it. The lookups against MongoDB are in test_audit_log.py."""
import asyncio
from typing import Any, Optional, cast

from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.model.audit import AuditTarget
from src.services.audit import TargetNames


class Broken:
    async def bundle_get(self, bundle_id: str) -> Any:
        raise RuntimeError("database down")

    server_get = bundle_get

    async def project_find_by_modrinth_id(self, ids: list[str]) -> list[Any]:
        raise RuntimeError("database down")


def test_a_failing_lookup_is_no_name() -> None:
    broken = Broken()
    names = TargetNames(cast(ModpackDatabase, broken), cast(PterodactylDatabase, broken), cast(ModrinthDatabase, broken))

    async def scenario() -> list[Optional[str]]:
        return [await names.name(AuditTarget(kind=kind, id="x")) for kind in ("bundle", "server", "replacement", "job")]

    assert asyncio.run(scenario()) == [None, None, None, None]
