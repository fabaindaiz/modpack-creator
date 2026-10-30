"""Writing in the knowledge base (O8 tanda 3: D6, D7, D10 of d-7985ee-a99db7; K-Q2, K-Q4 of d-7985ee-57d185;
user-walk K10, K11, K15, K22, K23).

An editor marks a pair, or a version alone, always as a suspicion; the pair is stored in a fixed order, the same
key is one entry, and a pair already there is named so the editor joins it. Each version must be its mod's, and a
range never goes backwards. Confirming, rejecting and editing a confirmed entry are an operator's; the author edits
and withdraws their own suspicion. An edit carries the revision it read.
"""
from typing import Any

import pytest
from fastapi import HTTPException

from src.database.knowledge import KnowledgeDatabase
from src.database.modrinth import ModrinthDatabase
from src.services.knowledge import KnowledgeService, SideInput
from tests._actors import EDITOR, OPERATOR, RecordingAudit
from tests._modrinth import IRIS, SODIUM, RecordedModrinthAPI
from tests.conftest import MongoRunner

IRIS_BROKEN, IRIS_FIXED, SODIUM_V = "bAdKrpw8", "vTN4NRGW", "v4PSXean"
OTHER = "op-2"


def service() -> tuple[KnowledgeService, RecordingAudit]:
    audit = RecordingAudit()
    return KnowledgeService(knowledgeDB=KnowledgeDatabase(), modrinthDB=ModrinthDatabase(),
                            modrinthAPI=RecordedModrinthAPI(), audit=audit), audit  # type: ignore[arg-type]


async def mark(knowledge: KnowledgeService, key: str = "k1", actor: Any = EDITOR, **sides: Any) -> Any:
    return await knowledge.issue_mark("b1", sides.get("a", SideInput(project_id=IRIS, broken_from=IRIS_BROKEN)),
                                sides.get("b", SideInput(project_id=SODIUM)), None, None, "pantalla negra",
                                ["n1"], key, actor)


async def refused(call: Any) -> int:
    try:
        await call
    except HTTPException as error:
        return error.status_code
    return 0


@pytest.mark.network
def test_marking_orders_the_pair_and_is_suspected(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        knowledge, audit = service()
        found = await mark(knowledge)
        iris = found.a if found.a.project_id == IRIS else found.b
        return (found.a.project_id, found.b.project_id, found.status, found.seen_in, iris.broken_from.version_number,
                iris.title, [e[0] for e in audit.events])

    a, b, status, seen, number, title, events = mongo(scenario)
    assert (a, b) == tuple(sorted([IRIS, SODIUM])) and status == "suspected" and seen == ["b1"]
    assert number == "1.11.6+26.3-fabric" and title == "Iris Shaders" and events == ["knowledge.mark"]


@pytest.mark.network
def test_the_same_key_is_one_entry(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, str]:
        knowledge, _ = service()
        return str((await mark(knowledge)).id), str((await mark(knowledge)).id)

    first, second = mongo(scenario)
    assert first == second


@pytest.mark.network
def test_marking_an_existing_pair_says_which(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, Any]:
        knowledge, _ = service()
        first = await mark(knowledge)
        try:
            await mark(knowledge, key="k2", a=SideInput(project_id=SODIUM), b=SideInput(project_id=IRIS))
        except HTTPException as error:
            return str(first.id), (error.status_code, error.detail)
        return str(first.id), None

    first, refusal = mongo(scenario)
    assert refusal is not None and refusal[0] == 409 and first in str(refusal[1])


@pytest.mark.network
def test_joining_adds_the_pack_and_notes_once(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        knowledge, _ = service()
        found = await mark(knowledge)
        await knowledge.issue_join(str(found.id), "b2", ["n1", "n2"], EDITOR)
        joined = await knowledge.issue_join(str(found.id), "b2", ["n2"], EDITOR)
        return joined.seen_in, joined.note_ids

    seen, notes = mongo(scenario)
    assert seen == ["b1", "b2"] and notes == ["n1", "n2"]


@pytest.mark.network
def test_a_version_of_another_mod_is_refused(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, int]:
        knowledge, _ = service()
        alien = await refused(mark(knowledge, a=SideInput(project_id=IRIS, broken_from=SODIUM_V)))
        unknown = await refused(mark(knowledge, key="k2", a=SideInput(project_id=IRIS, broken_from="nope")))
        return alien, unknown

    assert mongo(scenario) == (422, 422)


@pytest.mark.network
def test_an_inverted_range_is_refused(mongo: MongoRunner) -> None:
    async def scenario() -> int:
        knowledge, _ = service()
        return await refused(mark(knowledge, a=SideInput(project_id=IRIS, broken_from=IRIS_FIXED, fixed_in=IRIS_BROKEN)))

    assert mongo(scenario) == 422


@pytest.mark.network
def test_an_editor_cannot_confirm_or_edit_a_confirmed_entry(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, int, str, int]:
        knowledge, _ = service()
        found = await mark(knowledge)
        confirm = await refused(knowledge.issue_status(str(found.id), found.rev, "confirmed", EDITOR))
        done = await knowledge.issue_status(str(found.id), found.rev, "confirmed", OPERATOR)
        edit = await refused(knowledge.issue_edit(str(done.id), done.rev, SideInput(project_id=IRIS, fixed_in=IRIS_FIXED),
                                            SideInput(project_id=SODIUM), None, None, "x", EDITOR))
        mine = await refused(knowledge.issue_withdraw(str(done.id), EDITOR))
        return confirm, edit, done.status, mine

    assert mongo(scenario) == (403, 403, "confirmed", 409)


@pytest.mark.network
def test_the_author_withdraws_their_suspicion(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, str, list[str]]:
        knowledge, audit = service()
        found = await mark(knowledge)
        from dataclasses import replace
        other = await refused(knowledge.issue_withdraw(str(found.id), replace(EDITOR, sub=OTHER)))
        withdrawn = await knowledge.issue_withdraw(str(found.id), EDITOR)
        return other, withdrawn.status, [e[0] for e in audit.events]

    other, status, events = mongo(scenario)
    assert other == 403 and status == "rejected" and events[-1] == "knowledge.withdraw"


@pytest.mark.network
def test_an_edit_from_an_old_revision_is_a_conflict(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, int]:
        knowledge, _ = service()
        found = await mark(knowledge)
        edited = await knowledge.issue_edit(str(found.id), found.rev, SideInput(project_id=IRIS, broken_from=IRIS_BROKEN,
                                      fixed_in=IRIS_FIXED), SideInput(project_id=SODIUM), "26.3", None, "negro", EDITOR)
        stale = await refused(knowledge.issue_status(str(found.id), found.rev, "confirmed", OPERATOR))
        iris = edited.a if edited.a.project_id == IRIS else edited.b
        assert iris is not None and iris.fixed_in is not None
        return iris.fixed_in.version_number, stale

    fixed, stale = mongo(scenario)
    assert fixed == "1.11.7+26.3-fabric" and stale == 409


@pytest.mark.network
def test_the_base_lists_by_mod_and_status_and_counts_suspicions(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, int, int, int]:
        knowledge, _ = service()
        found = await mark(knowledge)
        await mark(knowledge, key="k2", a=SideInput(project_id=IRIS, broken_from=IRIS_BROKEN), b=None)
        await knowledge.issue_status(str(found.id), found.rev, "confirmed", OPERATOR)
        return (len(await knowledge.find(project=SODIUM)), len(await knowledge.find(project=IRIS)),
                len(await knowledge.find(status="suspected")), await knowledge.suspected_count())

    assert mongo(scenario) == (1, 2, 1, 1)
