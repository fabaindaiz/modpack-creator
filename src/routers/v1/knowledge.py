from datetime import datetime
from typing import Literal, Optional

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.dependences import Dependences, KnowledgeService, get_current_actor
from src.model.modpack.known_issue import KnownIssue
from src.model.users import Actor
from src.services.knowledge import SideInput

router = APIRouter(
    tags=["knowledge"],
    responses={404: {"description": "Not found"}},
)


class IssueMark(BaseModel):
    """Two mods that fail together, or one version alone (`b` empty), seen in the pack `bundle_id` (O8 tanda 3,
    D13). It is always a suspicion: a `status` sent here is ignored. `key` makes a request sent twice one entry."""
    bundle_id: str
    a: SideInput
    b: Optional[SideInput] = None
    game_version: Optional[str] = None
    loader: Optional[str] = None
    summary: str
    note_ids: list[str] = []
    key: str


class IssueJoin(BaseModel):
    bundle_id: str
    note_ids: list[str] = []


class IssueEdit(BaseModel):
    a: SideInput
    b: Optional[SideInput] = None
    game_version: Optional[str] = None
    loader: Optional[str] = None
    summary: str = ""
    # The revision read: an edit from an older one is a 409.
    rev: int


@router.post("/knowledge", response_model=KnownIssue, status_code=201)
@inject
async def knowledge_mark(
        issue: IssueMark,
        knowledge: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """Mark a suspicion; a pair already in the base is a 409 that names it (join it instead)."""
    return await knowledge.issue_mark(issue.bundle_id, issue.a, issue.b, issue.game_version, issue.loader,
                                      issue.summary, issue.note_ids, issue.key, actor)


@router.get("/knowledge", response_model=list[KnownIssue])
@inject
async def knowledge_find(
        project: Optional[str] = Query(None, description="the entries of this mod, on either side"),
        status: Optional[Literal["suspected", "confirmed", "rejected"]] = Query(None),
        game_version: Optional[str] = Query(None, description="seen on this Minecraft version, or on any"),
        before: Optional[datetime] = Query(None, description="updated before this, for the next page"),
        knowledge: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """The base, newest first, 20 at a time."""
    return await knowledge.find(project, status, game_version, before)


@router.get("/knowledge/{issue_id}", response_model=KnownIssue)
@inject
async def knowledge_get(
        issue_id: str,
        knowledge: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        actor: Actor = Depends(get_current_actor)
        ):
    return await knowledge.get(issue_id)


@router.post("/knowledge/{issue_id}/join", response_model=KnownIssue)
@inject
async def knowledge_join(
        issue_id: str,
        join: IssueJoin,
        knowledge: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """Add a pack and its notes to an entry."""
    return await knowledge.issue_join(issue_id, join.bundle_id, join.note_ids, actor)


@router.patch("/knowledge/{issue_id}", response_model=KnownIssue)
@inject
async def knowledge_edit(
        issue_id: str,
        edit: IssueEdit,
        knowledge: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """Edit an entry: a suspicion, its author; a confirmed one, an operator."""
    return await knowledge.issue_edit(issue_id, edit.rev, edit.a, edit.b, edit.game_version, edit.loader,
                                      edit.summary, actor)


@router.post("/knowledge/{issue_id}/status", response_model=KnownIssue)
@inject
async def knowledge_status(
        issue_id: str,
        status: Literal["suspected", "confirmed", "rejected"] = Query(...),
        rev: int = Query(..., description="the revision read: an older one is a 409"),
        knowledge: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """Confirm, reject or send back to suspicion (operator)."""
    return await knowledge.issue_status(issue_id, rev, status, actor)


@router.post("/knowledge/{issue_id}/withdraw", response_model=KnownIssue)
@inject
async def knowledge_withdraw(
        issue_id: str,
        knowledge: KnowledgeService = Depends(Provide[Dependences.knowledgeService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """Its author takes back their own suspicion."""
    return await knowledge.issue_withdraw(issue_id, actor)
