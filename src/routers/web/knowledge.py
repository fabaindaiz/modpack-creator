"""The knowledge base in the panel (O8 tanda 3, d-7985ee-a99db7 D6–D10; d-7985ee-57d185): «Marcar como sospechoso»
from a comparison of builds, joining an entry already there, and the base's pages."""
import secrets
from datetime import datetime
from typing import Annotated, Literal, Optional

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Form, Query, Request, Response

from src.dependences import Dependences, KnowledgeService, ModpackService, ModrinthService, NoteService
from src.model.users import Actor
from src.routers.web.modpacks import _titles
from src.library.knowledge import warning_of
from src.model.modpack.known_issue import IssueSide, KnownIssue
from src.services.knowledge import IssueExists, SideInput
from src.web import get_web_actor, render

router = APIRouter()

# The base's pages show 20 entries at a time; «Ver más» asks for the older ones.
PAGE = 20
STATUSES = {"suspected": "Sospecha", "confirmed": "Confirmada", "rejected": "Descartada"}


def _mine(issue: KnownIssue, actor: Actor) -> bool:
    return (issue.author.iss, issue.author.sub) == (actor.iss, actor.sub)


def _can_edit(issue: KnownIssue, actor: Actor) -> bool:
    """As the service decides it (issue_edit): a suspicion by its author, anything by an operator."""
    return actor.is_operator or (issue.status == "suspected" and _mine(issue, actor))


@router.get("/conocimiento")
@inject
async def knowledge_list(
        request: Request,
        mod: Optional[str] = None,
        estado: Optional[Literal["suspected", "confirmed", "rejected"]] = None,
        mc: Optional[str] = None,
        antes: Optional[datetime] = None,
        actor: Actor = Depends(get_web_actor),
        knowledgeService: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        ):
    """The knowledge base (K-Q5): every entry, newest first, by mod, state and Minecraft version, 20 at a time."""
    entries = await knowledgeService.find(project=mod or None, status=estado, game_version=mc or None, before=antes,
                                          limit=PAGE)
    named = {s.project_id: s.title for e in entries for s in ([e.a] + ([e.b] if e.b else []))}
    filters = {"mod": mod or "", "estado": estado or "", "mc": mc or ""}
    return render(request, "knowledge/list.html", actor, entries=[(e, warning_of(e)) for e in entries],
                  filters=filters, mod_title=named.get(mod or "", mod), statuses=STATUSES,
                  more=entries[-1].updated_at.isoformat() if len(entries) == PAGE else None, nav_active="knowledge")


@router.get("/conocimiento/{issue_id}")
@inject
async def knowledge_entry(
        issue_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        knowledgeService: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        noteService: NoteService = Depends(Provide[Dependences.noteService]),
        ):
    """One entry: each side's range, where it was seen and the notes behind it (verdict and text, never the
    pasted error: the base is read from every pack, user-walk K21), with the actions the person's role allows."""
    issue = await knowledgeService.get(issue_id)
    names = {str(b.id): b.name for b in await modpackService.bundle_find()}
    notes = await noteService.find_by_ids(issue.note_ids)
    return render(request, "knowledge/entry.html", actor, issue=issue, warning=warning_of(issue), names=names,
                  notes=notes, statuses=STATUSES, mine=_mine(issue, actor), can_edit=_can_edit(issue, actor),
                  nav_active="knowledge")


@router.get("/conocimiento/{issue_id}/editar")
@inject
async def edit_dialog(
        issue_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        knowledgeService: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        ):
    """The dialog to edit an entry: its summary, where it was seen, and «Arreglado en» chosen among the
    catalog's versions of each mod after its start, on its branch (K-Q2)."""
    issue = await knowledgeService.get(issue_id)
    sides = [issue.a] + ([issue.b] if issue.b is not None else [])
    return render(request, "knowledge/_edit_dialog.html", actor, issue=issue, sides=sides,
                  choices=await knowledgeService.range_choices(issue))


@router.post("/conocimiento/{issue_id}/editar")
@inject
async def edit(
        issue_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        knowledgeService: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        ):
    """Save the dialog: each side keeps its mod and its «Rota desde»; «Arreglado en» is the one chosen."""
    form = await request.form()
    issue = await knowledgeService.get(issue_id)

    def side(found: IssueSide) -> SideInput:
        return SideInput(project_id=found.project_id,
                         broken_from=found.broken_from.version_id if found.broken_from else None,
                         fixed_in=str(form.get(f"fixed_{found.project_id}") or "") or None)

    await knowledgeService.issue_edit(issue_id, int(str(form.get("rev") or "0")), side(issue.a),
                                      side(issue.b) if issue.b is not None else None,
                                      str(form.get("mc") or "") or None, str(form.get("loader") or "") or None,
                                      str(form.get("summary") or ""), actor)
    return Response(status_code=200, headers={"HX-Refresh": "true"})


@router.post("/conocimiento/{issue_id}/estado")
@inject
async def status(
        issue_id: str,
        status: Annotated[Literal["suspected", "confirmed", "rejected"], Form()],
        rev: Annotated[int, Form(ge=0)],
        actor: Actor = Depends(get_web_actor),
        knowledgeService: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        ):
    """Confirm, reject or send back to suspicion (an operator: the service checks it, D10)."""
    await knowledgeService.issue_status(issue_id, rev, status, actor)
    return Response(status_code=200, headers={"HX-Refresh": "true"})


@router.post("/conocimiento/{issue_id}/retirar")
@inject
async def withdraw(
        issue_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        knowledgeService: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        ):
    """Its author takes back their own suspicion (user-walk K23)."""
    await knowledgeService.issue_withdraw(issue_id, actor)
    return Response(status_code=200, headers={"HX-Refresh": "true"})


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
