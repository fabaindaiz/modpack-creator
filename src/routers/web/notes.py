"""Notes in the panel (O8 tanda 2): the dialog that writes or edits one (P1 of d-7985ee-a68b5c), and
archiving it. The dialog lives in #note-dialog, outside the page's lists."""
import secrets
from typing import Annotated, Optional

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from src.dependences import Dependences, ModrinthService, NoteService, PterodactylService
from src.library.notes import ERROR_HEAD, ERROR_MAX, ERROR_TAIL
from src.model.modpack.note import NoteTarget, VersionNote
from src.model.users import Actor
from src.routers.web.modpacks import _titles
from src.web import get_web_actor, render

router = APIRouter()

VERDICTS = {"works", "fails", "issues", "untested"}
# What the dialog's error field tells app.js, so its notice says how much a paste over the limit loses (P2).
ERROR_LIMITS = {"limit": ERROR_MAX, "head": ERROR_HEAD, "tail": ERROR_TAIL}


def list_id(note: VersionNote | NoteTarget) -> str:
    """The id of the list a note shows in: a build's, or a mod's in its history."""
    return f"notes-build-{note.build_number}" if note.build_number is not None else f"notes-mod-{note.project_id}"


@router.get("/modpacks/{bundle_id}/notas/nueva")
@inject
async def note_new(
        bundle_id: str,
        request: Request,
        build: Annotated[Optional[int], Query(ge=1)] = None,
        mod: Optional[str] = None,
        version: Optional[str] = None,
        numero: Optional[str] = None,
        actor: Actor = Depends(get_web_actor),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """The dialog to write a note on build `build`, or on version `version` (`numero`) of mod `mod`."""
    target = NoteTarget(build_number=build) if build is not None else \
        NoteTarget(project_id=mod, version_id=version, version_number=numero)
    about = f"el build {build}" if build is not None else \
        f"{(await _titles(modrinthService, [mod or '']))[mod or '']} {numero or version or ''}".strip()
    servers = [s for s in await pterodactylService.server_find() if s.bundle_id == bundle_id]
    return render(request, "notes/_dialog.html", actor, bundle_id=bundle_id, target=target, about=about,
                  servers=servers, key=secrets.token_urlsafe(16), found=None, error_limits=ERROR_LIMITS,
                  # Per build, or per mod and version (final review, important 5).
                  draft=f"{bundle_id}:{list_id(target)[6:]}" + (f"-{version}" if version else ""))


@router.post("/modpacks/{bundle_id}/notas")
@inject
async def note_create(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        noteService: NoteService = Depends(Provide[Dependences.noteService]),
        ):
    """Write the note; the answer puts it first in its list, out of band, and empties the dialog."""
    form = await request.form()
    build = form.get("build")
    target = NoteTarget(build_number=int(str(build))) if build else NoteTarget(
        project_id=str(form.get("mod") or "") or None, version_id=str(form.get("version") or "") or None,
        version_number=str(form.get("numero") or "") or None)
    verdict = str(form.get("verdict") or "untested")
    created = await noteService.create(bundle_id, target, verdict if verdict in VERDICTS else "untested",  # type: ignore[arg-type]
                                       str(form.get("text") or ""), str(form.get("error") or "") or None,
                                       str(form.get("server") or "") or None, str(form.get("key") or secrets.token_urlsafe(16)), actor)
    return render(request, "notes/_saved.html", actor, found=created, list_id=list_id(created), message="Nota guardada.")


@router.get("/notas/{note_id}/editar")
@inject
async def note_edit_dialog(
        note_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        noteService: NoteService = Depends(Provide[Dependences.noteService]),
        ):
    """The same dialog, with the note as it is and the revision read."""
    found = await noteService.get(note_id)
    about = f"el build {found.build_number}" if found.build_number is not None else (found.version_number or "")
    return render(request, "notes/_dialog.html", actor, bundle_id=found.bundle_id, target=None, about=about, servers=[],
                  key="", draft=f"{found.bundle_id}:note-{note_id}", found=found, error_limits=ERROR_LIMITS)


@router.post("/notas/{note_id}")
@inject
async def note_edit(
        note_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        noteService: NoteService = Depends(Provide[Dependences.noteService]),
        ):
    """Save an edit (its author, from the revision read): the note is redrawn in place."""
    form = await request.form()
    verdict = str(form.get("verdict") or "untested")
    edited = await noteService.edit(note_id, verdict if verdict in VERDICTS else "untested",  # type: ignore[arg-type]
                                    str(form.get("text") or ""), str(form.get("error") or "") or None,
                                    int(str(form.get("rev") or "0")), actor)
    return render(request, "notes/_saved.html", actor, found=edited, list_id=None, message="Nota editada.")


@router.post("/notas/{note_id}/archivar")
@inject
async def note_archive(
        note_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        noteService: NoteService = Depends(Provide[Dependences.noteService]),
        ):
    """Archive a note (deleting is archiving, D11): it leaves its list, with «Deshacer»."""
    archived = await noteService.archive(note_id, actor)
    return render(request, "notes/_archived.html", actor, found=archived)


@router.post("/notas/{note_id}/recuperar")
@inject
async def note_unarchive(
        note_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        noteService: NoteService = Depends(Provide[Dependences.noteService]),
        ):
    """Bring an archived note back; the page reloads to show it where it was."""
    await noteService.archive(note_id, actor, archived=False)
    return Response(status_code=200, headers={"HX-Refresh": "true"})
