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


class Packs:
    """The packs and notes an entry names (review B7): packs b1 and b2, note n1 of b1 and n2 of b2."""
    NOTES = {"n1": "b1", "n2": "b2"}

    async def bundle_get(self, bundle_id: str) -> Any:
        if bundle_id not in ("b1", "b2"):
            raise HTTPException(404, "Bundle not found")
        return type("Bundle", (), {"id": bundle_id})()

    async def find_by_ids(self, note_ids: list[str], archived_too: bool = False) -> list[Any]:
        return [type("Note", (), {"id": n, "bundle_id": self.NOTES[n]})() for n in note_ids if n in self.NOTES]


def service(packs: Any = None) -> tuple[KnowledgeService, RecordingAudit]:
    audit = RecordingAudit()
    packs = packs or Packs()
    return KnowledgeService(knowledgeDB=KnowledgeDatabase(), modrinthDB=ModrinthDatabase(),
                            modrinthAPI=RecordedModrinthAPI(), audit=audit,  # type: ignore[arg-type]
                            modpackDB=packs, noteDB=packs), audit


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
        await knowledge.issue_join(str(found.id), "b2", ["n2"], EDITOR)
        joined = await knowledge.issue_join(str(found.id), "b2", ["n2"], EDITOR)
        return joined.seen_in, joined.note_ids

    seen, notes = mongo(scenario)
    assert seen == ["b1", "b2"] and notes == ["n1", "n2"]


@pytest.mark.network
def test_a_repeated_mark_or_join_whose_note_was_archived_is_the_repeat(mongo: MongoRunner) -> None:
    """Review of 2026-10-30: a mark or a join sent again after one of its notes was archived was refused (422) as
    naming an invalid note. A note the entry already has is the repeat it is; a new archived note is still refused."""
    class Archiving(Packs):
        archived: set[str] = set()

        async def find_by_ids(self, note_ids: list[str], archived_too: bool = False) -> list[Any]:
            return [n for n in await super().find_by_ids(note_ids) if archived_too or n.id not in self.archived]

    async def scenario() -> tuple[Any, ...]:
        packs = Archiving()
        knowledge, _ = service(packs)
        first = await mark(knowledge)
        joined = await knowledge.issue_join(str(first.id), "b2", ["n2"], EDITOR)
        packs.archived = {"n1", "n2"}
        again = await mark(knowledge)
        rejoined = await knowledge.issue_join(str(first.id), "b2", ["n2"], EDITOR)
        new_mark = await refused(knowledge.issue_mark("b1", SideInput(project_id=IRIS), None, None, None, "negro",
                                                      ["n1"], "k2", EDITOR))
        other = await knowledge.issue_mark("b2", SideInput(project_id=SODIUM), None, None, None, "negro", [], "k3", EDITOR)
        new_join = await refused(knowledge.issue_join(str(other.id), "b1", ["n1"], EDITOR))
        return str(again.id) == str(first.id), rejoined.note_ids == joined.note_ids, new_mark, new_join

    assert mongo(scenario) == (True, True, 422, 422)


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


@pytest.mark.network
def test_fixed_in_is_offered_from_the_catalog_after_the_start_and_on_its_branch(mongo: MongoRunner) -> None:
    """O8 tanda 3 (K-Q2): «Arreglado en» is chosen among the catalog's versions of the mod: later than «Rota desde»,
    on its Minecraft version and loader (a backport is another branch, user-walk K14)."""
    from tests.test_update_check import version_doc

    async def scenario() -> list[str]:
        knowledge, _ = service()
        backport = version_doc("i4", IRIS, "1.6.9", date="2023-01-01T00:00:00Z").model_copy(update={"game_versions": ["1.20.1"]})
        await ModrinthDatabase().version_upsert_many([
            version_doc("i1", IRIS, "1.6.0", date="2020-01-01T00:00:00Z"), version_doc("i2", IRIS, "1.7.0", date="2021-01-01T00:00:00Z"),
            version_doc("i3", IRIS, "1.7.3", date="2022-01-01T00:00:00Z"), backport])
        found = await mark(knowledge, a=SideInput(project_id=IRIS, broken_from="i2"), b=None)
        return [v.version_id for v in (await knowledge.range_choices(found))[IRIS]]

    assert mongo(scenario) == ["i3"]


# The minors of the final review of O8 tanda 3.


def stored(project_id: str, status: str = "suspected", at: Any = None, b: Any = None, key: str = "") -> Any:
    from datetime import datetime, timezone
    from src.model.modpack.known_issue import IssueSide, KnownIssue
    when = at or datetime(2026, 10, 27, tzinfo=timezone.utc)
    return KnownIssue(a=IssueSide(project_id=project_id, title=project_id),
                      b=IssueSide(project_id=b, title=b) if b else None, status=status, summary="negro",  # type: ignore[arg-type]
                      author=EDITOR.ref(), at=when, updated_at=when, key=key or f"k-{project_id}-{b}-{status}")


@pytest.mark.network
def test_the_next_page_keeps_entries_written_at_the_same_moment(mongo: MongoRunner) -> None:
    """Review B5: «Ver más» pages on (updated_at, id): three entries of one moment, two a page, are all shown."""
    async def scenario() -> tuple[int, int, int]:
        from src.model.modpack.known_issue import KnownIssue
        knowledge, _ = service()
        for n in range(3):
            await KnownIssue.insert(stored(f"m{n}"))
        first = await knowledge.find(limit=2)
        rest = await knowledge.find(before=first[-1].updated_at, before_id=str(first[-1].id), limit=2)
        return len(first), len(rest), len({str(i.id) for i in first + rest})

    assert mongo(scenario) == (2, 1, 3)


@pytest.mark.network
def test_a_malformed_page_cursor_is_a_bad_request(mongo: MongoRunner) -> None:
    """Review of 2026-10-30: «Ver más» with a cursor that is not an id answered 404, as if an entry were missing.
    It is a bad request (422); a well-formed id of no entry still pages."""
    from datetime import datetime, timezone
    when = datetime(2026, 10, 27, tzinfo=timezone.utc)

    async def scenario() -> tuple[int, int]:
        knowledge, _ = service()
        bad = await refused(knowledge.find(before=when, before_id="not-an-id", limit=2))
        fine = len(await knowledge.find(before=when, before_id="6ae40b7c8901bede28d46699", limit=2))
        return bad, fine

    assert mongo(scenario) == (422, 0)


@pytest.mark.network
def test_notes_of_another_pack_are_refused(mongo: MongoRunner) -> None:
    """Review B7: marking and joining name a pack that exists, and notes of that pack only (422 otherwise: an
    entry backed by another pack's note would show it under the wrong pack)."""
    from datetime import datetime, timezone
    from src.database.modpack import ModpackDatabase
    from src.database.note import NoteDatabase
    from src.model.modpack.note import VersionNote
    from tests.test_builds import empty_bundle

    async def scenario() -> tuple[int, ...]:
        _, mine = await empty_bundle()
        _, other = await empty_bundle()
        when = datetime(2026, 10, 27, tzinfo=timezone.utc)
        notes = {}
        for pack in (mine, other):
            notes[pack] = str((await VersionNote.insert(VersionNote(
                bundle_id=pack, build_number=1, verdict="fails", text="x", author=EDITOR.ref(), at=when,
                key=f"n-{pack}"))).id)
        real = type("Real", (), {})()
        real.bundle_get, real.find_by_ids = ModpackDatabase().bundle_get, NoteDatabase().find_by_ids
        knowledge, _ = service(real)
        side = SideInput(project_id=IRIS)

        async def marking(pack: str, note_ids: list[str], key: str) -> int:
            return await refused(knowledge.issue_mark(pack, side, None, None, None, "negro", note_ids, key, EDITOR))

        alien = await marking(mine, [notes[other]], "k1")
        unknown = await marking(mine, ["6ae40b7c8901bede28d46699"], "k2")
        no_pack = await marking("6ae40b7c8901bede28d46600", [], "k3")
        made = await knowledge.issue_mark(mine, side, None, None, None, "negro", [notes[mine]], "k4", EDITOR)
        joined = await refused(knowledge.issue_join(str(made.id), other, [notes[mine]], EDITOR))
        return alien, unknown, no_pack, joined

    assert mongo(scenario) == (422, 422, 404, 422)


@pytest.mark.network
def test_two_people_joining_at_once_both_join(mongo: MongoRunner) -> None:
    """Review B4: two «Sumarse» that read the same revision both land; adding to a set never needs the revision."""
    import asyncio

    class Together(KnowledgeDatabase):
        """Both joins read the entry before either writes."""
        def __init__(self) -> None:
            self.readers = 0
            self.both = asyncio.Event()

        async def get(self, issue_id: str) -> Any:
            found = await super().get(issue_id)
            if self.readers < 2:
                self.readers += 1
                if self.readers == 2:
                    self.both.set()
                await self.both.wait()
            return found

    async def scenario() -> tuple[list[int], list[str]]:
        knowledge, _ = service()
        found = await mark(knowledge)
        knowledge.knowledgeDB = Together()
        joins = await asyncio.gather(*(refused(knowledge.issue_join(str(found.id), pack, [], EDITOR))
                                       for pack in ("b1", "b2")))
        return list(joins), (await KnowledgeDatabase().get(str(found.id))).seen_in

    joins, seen = mongo(scenario)
    assert joins == [0, 0] and seen == ["b1", "b2"]


@pytest.mark.network
def test_a_pair_has_one_live_entry(mongo: MongoRunner) -> None:
    """Review B2: sending a rejected entry back while a newer one of its pair lives, or editing an entry onto a pair
    that has one, is a 409 that names it; under that, the base itself refuses a second live entry of a pair (a race),
    while rejected ones may repeat."""
    from pymongo.errors import DuplicateKeyError
    from src.model.modpack.known_issue import KnownIssue

    async def scenario() -> tuple[Any, ...]:
        knowledge, _ = service()
        old = await mark(knowledge)
        old = await knowledge.issue_withdraw(str(old.id), EDITOR)
        new = await mark(knowledge, key="k2")
        back = await refused(knowledge.issue_status(str(old.id), old.rev, "suspected", OPERATOR))
        alone = await mark(knowledge, key="k3", b=None)
        onto = await refused(knowledge.issue_edit(str(alone.id), alone.rev, SideInput(project_id=IRIS),
                                                  SideInput(project_id=SODIUM), None, None, "x", EDITOR))
        await KnownIssue.insert(stored("z", status="rejected", key="r1"))
        await KnownIssue.insert(stored("z", status="rejected", key="r2"))
        try:
            await KnownIssue.insert(stored(IRIS, status="confirmed", b=SODIUM, key="race")
                                    if IRIS < SODIUM else stored(SODIUM, status="confirmed", b=IRIS, key="race"))
            race = "stored"
        except DuplicateKeyError:
            race = "refused"
        return back, onto, race, str(new.id) != str(old.id)

    assert mongo(scenario) == (409, 409, "refused", True)


@pytest.mark.network
def test_duplicates_written_before_the_index_never_stop_the_start(mongo: MongoRunner) -> None:
    """Review B2: two live entries of a pair from before the index leave it uncreated, logged; the app starts."""
    from src.model import ensure_live_pair_index
    from src.model.modpack.known_issue import KnownIssue

    async def scenario() -> tuple[bool, bool]:
        collection = KnownIssue.get_pymongo_collection()
        await collection.drop_index("live_pair")
        await KnownIssue.insert(stored("dup", key="d1"))
        await KnownIssue.insert(stored("dup", key="d2"))
        made = await ensure_live_pair_index(collection)
        return made, "live_pair" in await collection.index_information()

    assert mongo(scenario) == (False, False)


# K10 of d-7985ee-c0cbfb: joining from another Minecraft version may widen the entry, never on its own.


async def on_mc(knowledge: KnowledgeService, game_version: str, actor: Any = EDITOR, key: str = "k1") -> Any:
    return await knowledge.issue_mark("b1", SideInput(project_id=IRIS), SideInput(project_id=SODIUM), game_version,
                                      None, "pantalla negra", [], key, actor)


@pytest.mark.network
def test_joining_widens_only_when_asked_and_as_an_edit_would(mongo: MongoRunner) -> None:
    from dataclasses import replace
    other = replace(EDITOR, sub=OTHER)

    async def scenario() -> tuple[Any, ...]:
        knowledge, audit = service()
        found = await on_mc(knowledge, "1.21.1")
        plain = (await knowledge.issue_join(str(found.id), "b2", [], other)).also_game_versions
        alien = await refused(knowledge.issue_join(str(found.id), "b2", [], other, also_game_version="1.21.4"))
        mine = (await knowledge.issue_join(str(found.id), "b2", [], EDITOR, also_game_version="1.21.4")).also_game_versions
        done = await knowledge.issue_status(str(found.id), (await knowledge.get(str(found.id))).rev, "confirmed", OPERATOR)
        confirmed = await refused(knowledge.issue_join(str(done.id), "b2", [], EDITOR, also_game_version="1.21.5"))
        by_operator = (await knowledge.issue_join(str(done.id), "b1", [], OPERATOR, also_game_version="1.21.5")).also_game_versions
        listed = [str(i.id) for i in await knowledge.find(game_version="1.21.5")]
        return plain, alien, mine, confirmed, by_operator, listed == [str(found.id)], audit.events[-1][2]

    plain, alien, mine, confirmed, by_operator, listed, detail = mongo(scenario)
    assert (plain, alien, mine, confirmed) == ([], 403, ["1.21.4"], 403)
    assert by_operator == ["1.21.4", "1.21.5"] and listed and detail.get("also_game_version") == "1.21.5"


@pytest.mark.network
def test_the_widening_hint_says_how_likely_it_is_compatible(mongo: MongoRunner) -> None:
    """The research's rule: one hotfix group, probably compatible; else a version file of either mod in the catalog
    listing both, probably compatible; else very probably not. Only the catalog, never Modrinth."""
    from tests.test_update_check import version_doc

    async def scenario() -> tuple[Any, ...]:
        knowledge, _ = service()
        found = await on_mc(knowledge, "1.21.1")
        asked = len(knowledge.modrinthAPI.calls)  # type: ignore[attr-defined]
        grouped = await knowledge.widen_hint(found, "1.21")
        apart = await knowledge.widen_hint(found, "1.21.4")
        both = version_doc("s9", SODIUM, "0.6.0").model_copy(update={"game_versions": ["1.21.1", "1.21.4"]})
        await ModrinthDatabase().version_upsert_many([both])
        listed = await knowledge.widen_hint(found, "1.21.4")
        same = await knowledge.widen_hint(found, "1.21.1")
        return grouped, apart, listed, same, knowledge.modrinthAPI.calls[asked:]  # type: ignore[attr-defined]

    grouped, apart, listed, same, calls = mongo(scenario)
    assert grouped.likely and grouped.text.startswith("Probablemente sea compatible") and "1.21 y 1.21.1" in grouped.text
    assert not apart.likely and apart.text.startswith("Es muy probable que no sea compatible")
    assert listed.likely and "Sodium 0.6.0" in listed.text and same is None and calls == []


@pytest.mark.network
def test_an_edit_narrows_the_widened_versions_and_never_repeats_or_revives_one(mongo: MongoRunner) -> None:
    """Review of 2026-10-30, Important 1: a version ticked by mistake comes off with an edit; editing to «any»
    clears the list (it would come back later); the main version is never also listed."""
    async def scenario() -> tuple[Any, ...]:
        knowledge, _ = service()
        found = await on_mc(knowledge, "1.21.1")
        await knowledge.issue_join(str(found.id), "b2", [], EDITOR, also_game_version="1.21.4")
        await knowledge.issue_join(str(found.id), "b2", [], EDITOR, also_game_version="1.21.5")

        async def edit(game_version: Any, also: Any) -> Any:
            entry = await knowledge.get(str(found.id))
            return (await knowledge.issue_edit(str(found.id), entry.rev, SideInput(project_id=IRIS, broken_from=IRIS_BROKEN),
                                               SideInput(project_id=SODIUM), game_version, None, "x", EDITOR,
                                               also_game_versions=also)).also_game_versions
        kept = await edit("1.21.1", None)
        narrowed = await edit("1.21.1", ["1.21.5"])
        moved = await edit("1.21.5", ["1.21.5", "1.21.4"])
        anywhere = await edit(None, ["1.21.4"])
        return kept, narrowed, moved, anywhere

    assert mongo(scenario) == (["1.21.4", "1.21.5"], ["1.21.5"], ["1.21.4"], [])
