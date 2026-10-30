"""Notes on a build or a mod's version (O8 tanda 2: D3–D5, D10, D11 of d-7985ee-a99db7; P2, P3 of
d-7985ee-a68b5c; user-walk N14–N16, N19, N22).

A note has a verdict, a text and maybe an error. The error is pasted: over 64 KiB its head and tail are
kept with a mark; tokens and IPv4 are masked. Only its author edits it (the text before is kept); its
author archives it, or an operator; archived notes are left out. The same idempotency key is one note.
"""
from typing import Any

import pytest
from fastapi import HTTPException

from src.database.note import NoteDatabase
from src.library.notes import ERROR_HEAD, ERROR_MAX, ERROR_TAIL, cut_error, mask_secrets
from src.model.modpack.note import NoteTarget
from src.services.notes import NoteService
from tests._actors import EDITOR, OPERATOR, RecordingAudit
from tests.conftest import MongoRunner


def test_an_error_over_64_kib_keeps_its_head_and_tail() -> None:
    assert (ERROR_MAX, ERROR_HEAD, ERROR_TAIL) == (64 * 1024, 48 * 1024, 16 * 1024)
    short = "Caused by: x"
    assert cut_error(short) == (short, 0)
    long = "H" * ERROR_HEAD + "M" * 300_000 + "Caused by: sodium" + "T" * (ERROR_TAIL - 17)
    kept, cut = cut_error(long)
    assert kept.startswith("H" * ERROR_HEAD) and kept.endswith("Caused by: sodium" + "T" * (ERROR_TAIL - 17))
    assert cut == 293 and "… 293 KB cortados …" in kept  # 300000 bytes, rounded


def test_the_cut_counts_as_the_textarea_does_and_is_never_zero() -> None:
    """Review of 2026-10-30: a form sends a textarea's lines as CRLF, while its value (what app.js counts) has
    LF, so the server cut errors the panel said fit; and one just past the limit said «0 KB». The server counts
    the value's newlines, and a cut rounds up."""
    fits = "a" * (ERROR_MAX - 20) + "\r\n" * 20
    assert cut_error(fits) == ("a" * (ERROR_MAX - 20) + "\n" * 20, 0)
    _, cut = cut_error("a" * (ERROR_MAX + 1))
    assert cut == 1


def test_secrets_are_masked() -> None:
    text = ("--accessToken eyJhbGciOiJIUzI1NiJ9.abc --username Steve\n"
            "url?token=abc123&x=1 access_token=zzz\n"
            "https://discord.com/api/webhooks/123/AbC-_d hook\n"
            "/203.0.113.7:51234 logged in; 10.0.0.1 too; version 1.20.1 stays\n")
    masked = mask_secrets(text)
    assert "eyJ" not in masked and "abc123" not in masked and "zzz" not in masked and "AbC-_d" not in masked
    assert "203.0.113.7" not in masked and "10.0.0.1" not in masked
    assert "--accessToken ***" in masked and "token=***" in masked and "/***:51234" in masked
    assert "Steve" in masked and "1.20.1" in masked


def service() -> tuple[NoteService, RecordingAudit]:
    audit = RecordingAudit()
    return NoteService(noteDB=NoteDatabase(), audit=audit), audit


BUILD = NoteTarget(build_number=15)
IRIS = NoteTarget(project_id="YL57xq9U", version_id="iris-172", version_number="1.7.2")


@pytest.mark.network
def test_a_note_on_a_build_and_on_a_mod_version(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        notes, audit = service()
        on_build = await notes.create("b1", BUILD, "fails", "pantalla negra", None, None, "k1", EDITOR)
        on_mod = await notes.create("b1", IRIS, "works", "anda", "--accessToken eyJx\n", None, "k2", EDITOR)
        return (on_build.build_number, on_mod.project_id, on_mod.error, [e[0] for e in audit.events],
                [n.id for n in await notes.find("b1", build_number=15)], [n.id for n in await notes.find("b1", project_id=IRIS.project_id)])

    build_number, project_id, error, events, by_build, by_mod = mongo(scenario)
    assert build_number == 15 and project_id == "YL57xq9U" and error == "--accessToken ***\n"
    assert events == ["note.create", "note.create"] and len(by_build) == 1 and len(by_mod) == 1


@pytest.mark.network
def test_the_same_key_is_one_note(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[bool, int]:
        notes, _ = service()
        first = await notes.create("b1", BUILD, "fails", "x", None, None, "same", EDITOR)
        again = await notes.create("b1", BUILD, "fails", "x", None, None, "same", EDITOR)
        return first.id == again.id, len(await notes.find("b1", build_number=15))

    assert mongo(scenario) == (True, 1)


@pytest.mark.network
def test_a_repeated_key_never_answers_another_persons_or_another_packs_note(mongo: MongoRunner) -> None:
    """Review of tanda 2: a `key` already used returned that note, whoever wrote it and in whatever pack.
    The same author in the same pack gets their note back; anyone else, a 409 and nothing written."""
    async def scenario() -> tuple[Any, ...]:
        notes, _ = service()
        mine = await notes.create("b1", BUILD, "fails", "mía", None, None, "same", EDITOR)
        refused = []
        for bundle_id, actor in (("b1", OPERATOR), ("b2", EDITOR)):
            with pytest.raises(HTTPException) as error:
                await notes.create(bundle_id, BUILD, "works", "otra", None, None, "same", actor)
            refused.append(error.value.status_code)
        return refused, (await notes.get(str(mine.id))).text, len(await notes.find("b2", build_number=15))

    assert mongo(scenario) == ([409, 409], "mía", 0)


@pytest.mark.network
def test_a_note_names_only_a_build_and_a_server_of_its_pack(mongo: MongoRunner) -> None:
    """Review of tanda 2: `create` took any build number and any server. A build the pack does not have,
    or a server linked to another pack (or to none), is a 422 and nothing is written."""
    from src.database.modpack import ModpackDatabase
    from src.database.pterodactyl import PterodactylDatabase
    from src.model.pterodactyl import PterodactylServer
    from tests.test_builds import empty_bundle
    from src.model.modpack import ProjectOrigin

    async def scenario() -> tuple[Any, ...]:
        modpack, bundle_id = await empty_bundle()
        await modpack.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        await modpack.build_create(bundle_id, actor=EDITOR)  # build 1
        _, other_id = await empty_bundle()
        servers = PterodactylDatabase()

        async def server(identifier: str, linked: str | None) -> str:
            made = await servers.server_insert(PterodactylServer(
                identifier=identifier, internal_id=1, uuid=f"{identifier}-0", name=identifier, node="n1", description="",
                status=None, is_suspended=False, is_installing=False, is_transferring=False, bundle_id=linked))
            return str(made.id)

        ours, theirs, loose = await server("ours", bundle_id), await server("theirs", other_id), await server("loose", None)
        notes = NoteService(noteDB=NoteDatabase(), audit=RecordingAudit(), pterodactylDB=servers, modpackDB=ModpackDatabase())
        refused: list[tuple[int, Any]] = []
        for target, server_id in ((NoteTarget(build_number=7), None), (NoteTarget(build_number=1), theirs),
                                  (NoteTarget(build_number=1), loose)):
            with pytest.raises(HTTPException) as error:
                await notes.create(bundle_id, target, "fails", "x", None, server_id, f"k-{len(refused)}", EDITOR)
            refused.append((error.value.status_code, error.value.detail))
        kept = await notes.create(bundle_id, NoteTarget(build_number=1), "works", "anda", None, ours, "k-ok", EDITOR)
        return refused, kept.server_name, len(await notes.find(bundle_id))

    refused, server_name, stored = mongo(scenario)
    assert refused == [(422, "El build 7 no es de este pack"), (422, "Ese servidor no está vinculado a este pack"),
                       (422, "Ese servidor no está vinculado a este pack")]
    assert server_name == "ours" and stored == 1


@pytest.mark.network
def test_only_the_author_edits_and_the_old_text_is_kept(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        notes, _ = service()
        note = await notes.create("b1", BUILD, "fails", "pantalla negar", None, None, "k", EDITOR)
        with pytest.raises(HTTPException) as other:
            await notes.edit(str(note.id), "fails", "no", None, note.rev, OPERATOR)
        edited = await notes.edit(str(note.id), "fails", "pantalla negra", None, note.rev, EDITOR)
        return other.value.status_code, edited.text, [p.text for p in edited.previous], edited.edited_at is not None

    assert mongo(scenario) == (403, "pantalla negra", ["pantalla negar"], True)


@pytest.mark.network
def test_two_edits_from_one_read_one_is_refused(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, str]:
        notes, _ = service()
        note = await notes.create("b1", BUILD, "fails", "a", None, None, "k", EDITOR)
        await notes.edit(str(note.id), "fails", "b", None, note.rev, EDITOR)
        with pytest.raises(HTTPException) as stale:
            await notes.edit(str(note.id), "fails", "c", None, note.rev, EDITOR)
        return stale.value.status_code, (await notes.get(str(note.id))).text

    assert mongo(scenario) == (409, "b")


@pytest.mark.network
def test_an_editor_archives_only_their_own_and_archived_notes_are_left_out(mongo: MongoRunner) -> None:
    from tests._actors import Actor
    other_editor = Actor(iss=EDITOR.iss, sub="otro", name="Otro", groups=frozenset(), is_operator=False)

    async def scenario() -> tuple[Any, ...]:
        notes, _ = service()
        mine = await notes.create("b1", BUILD, "fails", "a", None, None, "k1", EDITOR)
        theirs = await notes.create("b1", BUILD, "works", "b", None, None, "k2", other_editor)
        with pytest.raises(HTTPException) as refused:
            await notes.archive(str(theirs.id), EDITOR)
        await notes.archive(str(mine.id), EDITOR)
        await notes.archive(str(theirs.id), OPERATOR)
        return (refused.value.status_code, len(await notes.find("b1", build_number=15)),
                len(await notes.find("b1", build_number=15, archived=True)))

    assert mongo(scenario) == (403, 0, 2)


@pytest.mark.network
def test_an_entry_reads_its_notes_by_id_and_never_an_archived_one(mongo: MongoRunner) -> None:
    """O8 tanda 3 (user-walk K21): the base's page shows the notes behind an entry; an archived one is gone."""
    async def scenario() -> tuple[list[str], list[str]]:
        notes, _ = service()
        live = await notes.create("b1", BUILD, "fails", "se cae", None, None, "k1", EDITOR)
        gone = await notes.create("b2", BUILD, "fails", "negro", None, None, "k2", EDITOR)
        await notes.archive(str(gone.id), EDITOR)
        ids = [str(live.id), str(gone.id), "not-an-id"]
        # A mark sent again checks the notes it already had, archived ones too (review of 2026-10-30).
        return ([n.text for n in await notes.find_by_ids(ids)],
                sorted(n.text for n in await notes.noteDB.find_by_ids(ids, archived_too=True)))

    assert mongo(scenario) == (["se cae"], ["negro", "se cae"])
