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
