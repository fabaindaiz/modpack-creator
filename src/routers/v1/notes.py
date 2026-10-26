from typing import Optional

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.dependences import Dependences, NoteService, get_current_actor
from src.model.modpack.note import NoteTarget, Verdict, VersionNote
from src.model.users import Actor

router = APIRouter(
    tags=["notes"],
    responses={404: {"description": "Not found"}},
)


class NoteCreate(BaseModel):
    """A note on a build (`build_number`) or a mod's version (`project_id` and `version_id`) of the pack
    (O8 tanda 2, D13). `key` makes a request sent twice one note."""
    target: NoteTarget
    verdict: Verdict
    text: str = ""
    error: Optional[str] = None
    server_id: Optional[str] = None
    key: str


class NoteEdit(BaseModel):
    verdict: Verdict
    text: str = ""
    error: Optional[str] = None
    # The revision read: an edit from an older one is a 409.
    rev: int


@router.post("/modpack/{id}/notes", response_model=VersionNote, status_code=201)
@inject
async def note_create(
        id: str,
        note: NoteCreate,
        notes: NoteService = Depends(Provide[Dependences.noteService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """Write a note; its error is masked and cut as in the panel."""
    return await notes.create(id, note.target, note.verdict, note.text, note.error, note.server_id, note.key, actor)


@router.get("/modpack/{id}/notes", response_model=list[VersionNote], response_model_exclude={"__all__": {"error"}})
@inject
async def note_find(
        id: str,
        build: Optional[int] = Query(None, description="the notes of this build"),
        project: Optional[str] = Query(None, description="the notes of this mod's versions"),
        archived: bool = Query(False),
        notes: NoteService = Depends(Provide[Dependences.noteService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """A pack's notes, newest first, without their error (user-walk N21): GET /v1/note/{id} has it."""
    return await notes.find(id, build_number=build, project_id=project, archived=archived)


@router.get("/note/{note_id}", response_model=VersionNote)
@inject
async def note_get(
        note_id: str,
        notes: NoteService = Depends(Provide[Dependences.noteService]),
        actor: Actor = Depends(get_current_actor)
        ):
    return await notes.get(note_id)


@router.patch("/note/{note_id}", response_model=VersionNote)
@inject
async def note_edit(
        note_id: str,
        edit: NoteEdit,
        notes: NoteService = Depends(Provide[Dependences.noteService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """Edit a note: only its author, from the revision read."""
    return await notes.edit(note_id, edit.verdict, edit.text, edit.error, edit.rev, actor)


@router.delete("/note/{note_id}", response_model=VersionNote)
@inject
async def note_archive(
        note_id: str,
        notes: NoteService = Depends(Provide[Dependences.noteService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """Archive a note (deleting is archiving): its author, or an operator."""
    return await notes.archive(note_id, actor)
