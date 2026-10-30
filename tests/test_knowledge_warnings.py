"""The warnings of a set of versions (O8 tanda 3; K-Q1 of d-7985ee-57d185; user-walk K5, K6): one read of the base
for the mods there, the dates of only the versions of the mods that match, and never a call to Modrinth. Only
confirmed entries warn, unless suspicions are asked for (the comparison); a rejected entry never warns."""
from datetime import datetime, timezone
from typing import Any

import pytest

from src.database.knowledge import KnowledgeDatabase
from src.database.modrinth import ModrinthDatabase
from src.model.modpack.known_issue import IssueSide, IssueStatus, KnownIssue
from src.model.modrinth import ModrinthVersion
from src.services.knowledge import warnings_for
from tests._actors import EDITOR
from tests._modrinth import IRIS, SODIUM, RecordedModrinthAPI
from tests.conftest import MongoRunner
from tests.test_knowledge import IRIS_BROKEN, SODIUM_V

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
# On 26.3 and Fabric, as the versions above.
PACK = {IRIS: IRIS_BROKEN, SODIUM: SODIUM_V, "lithium": "lith-1"}


class CountingKnowledge(KnowledgeDatabase):
    def __init__(self) -> None:
        self.reads = 0

    async def find(self, **filters: Any) -> list[KnownIssue]:
        self.reads += 1
        return await super().find(**filters)


class CountingCatalog(ModrinthDatabase):
    def __init__(self) -> None:
        self.asked: list[list[str]] = []

    async def version_find_by_version_ids(self, version_ids: list[str]) -> list[ModrinthVersion]:
        self.asked.append(sorted(version_ids))
        return await super().version_find_by_version_ids(version_ids)


async def seed(*statuses: IssueStatus) -> None:
    raw = [v for v in RecordedModrinthAPI().versions if v["id"] in (IRIS_BROKEN, SODIUM_V)]
    await ModrinthDatabase().version_upsert_many([ModrinthVersion.parse(**v) for v in raw])
    for n, status in enumerate(statuses):
        await KnownIssue.insert(KnownIssue(a=IssueSide(project_id=SODIUM, title="Sodium"),
                                           b=IssueSide(project_id=IRIS, title="Iris"), status=status, summary="negro",
                                           author=EDITOR.ref(), at=NOW, updated_at=NOW, key=f"k{n}"))


@pytest.mark.network
def test_warnings_read_the_base_once_and_never_modrinth(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        await seed("confirmed")
        knowledge, catalog = CountingKnowledge(), CountingCatalog()
        found = await warnings_for(knowledge, catalog, PACK, "26.3", "fabric")
        return sorted(found), knowledge.reads, catalog.asked

    keys, reads, asked = mongo(scenario)
    assert keys == sorted([IRIS, SODIUM]) and reads == 1
    # Only the versions of the mods that match: never lithium's.
    assert asked == [sorted([IRIS_BROKEN, SODIUM_V])]


@pytest.mark.network
def test_no_entry_for_the_mods_reads_no_dates(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        catalog = CountingCatalog()
        return await warnings_for(KnowledgeDatabase(), catalog, PACK, "26.3", "fabric"), catalog.asked

    assert mongo(scenario) == ({}, [])


@pytest.mark.network
def test_a_suspicion_is_left_out_unless_asked(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, int]:
        await seed("suspected")
        plain = await warnings_for(KnowledgeDatabase(), ModrinthDatabase(), PACK, "26.3", "fabric")
        asked = await warnings_for(KnowledgeDatabase(), ModrinthDatabase(), PACK, "26.3", "fabric", suspected=True)
        return len(plain), len(asked.get(IRIS, []))

    assert mongo(scenario) == (0, 1)


@pytest.mark.network
def test_a_rejected_entry_never_warns(mongo: MongoRunner) -> None:
    async def scenario() -> int:
        await seed("rejected")
        return len(await warnings_for(KnowledgeDatabase(), ModrinthDatabase(), PACK, "26.3", "fabric", suspected=True))

    assert mongo(scenario) == 0
