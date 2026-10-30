"""The knowledge base in the panel (O8 tanda 3, d-7985ee-a99db7 D6–D10; d-7985ee-57d185): «Marcar como sospechoso»
from a comparison of builds, joining an entry already there, and the base's pages."""
import secrets
from typing import Annotated, Optional

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request

from src.dependences import Dependences, KnowledgeService, ModpackService, ModrinthService, NoteService
from src.model.users import Actor
from src.routers.web.modpacks import _titles
from src.services.knowledge import IssueExists, SideInput
from src.web import get_web_actor, render

router = APIRouter()


@router.get("/modpacks/{bundle_id}/conocimiento/marcar")
@inject
async def mark_dialog(
        bundle_id: str,
        request: Request,
        de: Annotated[int, Query(ge=1)],
        a: Annotated[int, Query(ge=1)],
        mod: str,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        noteService: NoteService = Depends(Provide[Dependences.noteService]),
        ):
    """The dialog to mark `mod` as suspect, from the comparison of build `de` with build `a` (the one that failed):
    the other mod is chosen among build `a`'s (user-walk K17), with the notes of both builds."""
    failing = await modpackService.build_get(bundle_id, a)
    entries = {e.project_id: e for e in failing.lock.entries}
    titles = await _titles(modrinthService, sorted(entries | {mod: None}))
    others = sorted(({"project_id": e.project_id, "title": titles.get(e.project_id, e.project_id),
                      "version_number": e.version_number} for p, e in entries.items() if p != mod),
                    key=lambda o: str(o["title"]).lower())
    notes = [n for number in sorted({de, a}) for n in await noteService.find(bundle_id, build_number=number)]
    arrived = entries[mod].version_number if mod in entries else None
    target = failing.request.target
    return render(request, "knowledge/_mark_dialog.html", actor, bundle_id=bundle_id, de=de, a=a, mod=mod,
                  title=titles.get(mod, mod), arrived=arrived, others=others, notes=notes,
                  game_version=target.game_version, loader=target.loader, key=secrets.token_urlsafe(16),
                  draft=f"{bundle_id}:mark-{a}-{mod}")


@router.post("/modpacks/{bundle_id}/conocimiento")
@inject
async def mark(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        knowledgeService: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        ):
    """Mark the suspicion; «la que trae el build» is the version in the build that failed. A pair already in the
    base answers the dialog that offers to join it."""
    form = await request.form()
    failing = await modpackService.build_get(bundle_id, int(str(form.get("a") or "0")))
    versions = {e.project_id: e.version_id for e in failing.lock.entries}

    def side(project_id: str, since: object) -> SideInput:
        return SideInput(project_id=project_id, broken_from=versions.get(project_id) if since == "build" else None)

    mod, other = str(form.get("mod") or ""), str(form.get("b") or "")
    note_ids = [str(n) for n in form.getlist("notes")]
    try:
        issue = await knowledgeService.issue_mark(
            bundle_id, side(mod, form.get("a_desde")), side(other, form.get("b_desde")) if other else None,
            str(form.get("mc") or "") or None, str(form.get("loader") or "") or None, str(form.get("summary") or ""),
            note_ids, str(form.get("key") or secrets.token_urlsafe(16)), actor)
    except IssueExists as exists:
        return render(request, "knowledge/_exists.html", actor, issue=await knowledgeService.get(exists.issue_id),
                      bundle_id=bundle_id, note_ids=note_ids)
    return render(request, "knowledge/_marked.html", actor,
                  message="Marcada como sospecha. Un operador la confirma o la descarta en «Conocimiento».",
                  issue=issue)


@router.post("/conocimiento/{issue_id}/sumarse")
@inject
async def join(
        issue_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        knowledgeService: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        ):
    """Join an entry with this pack and these notes."""
    form = await request.form()
    issue = await knowledgeService.issue_join(issue_id, str(form.get("bundle") or ""),
                                              [str(n) for n in form.getlist("notes")], actor)
    return render(request, "knowledge/_marked.html", actor, message="Sumado a la entrada existente.", issue=issue)
