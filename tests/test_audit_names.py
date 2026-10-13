"""The target's name an audit event keeps (A10), looked up when it is written: never a reason not to
write it. The lookups against MongoDB are in test_audit_log.py."""
import asyncio
from datetime import datetime, timezone
from typing import Any, Optional, cast

from src.database.audit import AuditDatabase
from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.model.audit import AuditEvent, AuditTarget
from src.model.users.actor import ActorRef
from src.services.audit import AuditService, TargetNames


class Broken:
    async def bundle_get(self, bundle_id: str) -> Any:
        raise RuntimeError("database down")

    server_get = bundle_get

    async def project_find_by_modrinth_id(self, ids: list[str]) -> list[Any]:
        raise RuntimeError("database down")

    version_find_by_version_ids = project_find_by_modrinth_id


def test_a_failing_lookup_is_no_name() -> None:
    broken = Broken()
    names = TargetNames(cast(ModpackDatabase, broken), cast(PterodactylDatabase, broken), cast(ModrinthDatabase, broken))

    async def scenario() -> list[Optional[str]]:
        return [await names.name(AuditTarget(kind=kind, id="x")) for kind in ("bundle", "server", "replacement", "job")]

    assert asyncio.run(scenario()) == [None, None, None, None]


class Catalog:
    def __init__(self) -> None:
        self.asked: list[tuple[str, list[str]]] = []

    async def project_find_by_modrinth_id(self, ids: list[str]) -> list[Any]:
        self.asked.append(("projects", ids))
        return [type("P", (), {"project_id": i, "title": i.title()})() for i in ids if i != "gone"]

    async def version_find_by_version_ids(self, ids: list[str]) -> list[Any]:
        self.asked.append(("versions", ids))
        return [type("V", (), {"version_id": i, "version_number": "1." + i})() for i in ids]


def _event(details: dict[str, Any]) -> AuditEvent:
    return AuditEvent.model_construct(at=datetime(2026, 10, 1, tzinfo=timezone.utc), actor=ActorRef(iss="i", sub="s", name="A"),
                                      action="bundle.pin", target=AuditTarget(kind="bundle", id="b"), details=details)


def test_a_page_names_its_mods_and_versions_in_one_query_each() -> None:
    catalog = Catalog()
    names = TargetNames(cast(ModpackDatabase, None), cast(PterodactylDatabase, None), cast(ModrinthDatabase, catalog))
    service = AuditService(AuditDatabase(), names)
    events = [_event({"project_id": "jei", "version_id": "v1"}), _event({"refs": ["sodium", "gone"], "number": 3}),
              _event({"decisions": {"iris": True}, "replacement": None})]
    found = asyncio.run(service.names_for(events))
    assert found == {"jei": "Jei", "sodium": "Sodium", "iris": "Iris", "v1": "1.v1"}
    assert sorted(catalog.asked) == [("projects", ["gone", "iris", "jei", "sodium"]), ("versions", ["v1"])]


def test_a_page_without_ids_or_with_a_failing_catalog_has_no_names() -> None:
    catalog = Catalog()
    names = TargetNames(cast(ModpackDatabase, None), cast(PterodactylDatabase, None), cast(ModrinthDatabase, catalog))
    assert asyncio.run(AuditService(AuditDatabase(), names).names_for([_event({"number": 3})])) == {}
    assert catalog.asked == []
    broken = cast(ModrinthDatabase, Broken())
    failing = AuditService(AuditDatabase(), TargetNames(cast(ModpackDatabase, None), cast(PterodactylDatabase, None), broken))
    assert asyncio.run(failing.names_for([_event({"project_id": "jei"})])) == {}
    assert asyncio.run(AuditService(AuditDatabase()).names_for([_event({"project_id": "jei"})])) == {}


def test_audit_has_only_the_index_its_page_can_use() -> None:
    """Final review of batch A (minor 1): the page is one aggregation whose filters live inside
    `$facet`, and a `$facet` sub-pipeline uses no index; only the leading match and sort on `at` do.
    An index on a filter field would only cost on every write."""
    keys = [index.document["key"] for index in AuditEvent.Settings.indexes]
    assert [list(key.items()) for key in keys] == [[("at", -1), ("_id", -1)]]
