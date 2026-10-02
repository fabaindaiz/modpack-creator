from typing import Annotated, Optional
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from src.dependences import AuditService, Dependences, JobService, PterodactylService
from src.model.users import Actor
from src.web import WebRoute, get_web_actor, render

router = APIRouter(route_class=WebRoute, default_response_class=HTMLResponse)

JOBS_SHOWN = 50
AUDIT_PAGE = 50
# A filter or a cursor: kept short, and passed to the service as the exact value.
FilterValue = Annotated[Optional[str], Query(max_length=200)]

@router.get("/jobs")
@inject
async def job_list(
        request: Request,
        actor: Actor = Depends(get_web_actor),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        ):
    """The most recent jobs, newest first, with who asked for each."""
    jobs = await jobService.recent(limit=JOBS_SHOWN)
    return render(request, "activity/jobs.html", actor, jobs=jobs, nav_active="jobs")

@router.get("/jobs/{job_id}/row")
@inject
async def job_row(
        job_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        ):
    """One job's row: what an active row polls. A job about a server carries the server's name
    (for the real run's confirmation hint); a server that is gone just shows no hint."""
    job = await jobService.get(job_id)
    context = {}
    server_id = job.payload.get("server_id")
    if isinstance(server_id, str):
        try:
            context["server_name"] = (await pterodactylService.server_get(server_id)).name
        except HTTPException:
            pass
    return render(request, "activity/_job_row.html", actor, job=job, **context)

@router.get("/audit")
@inject
async def audit_log(
        request: Request,
        actor_filter: Annotated[Optional[str], Query(alias="actor", max_length=200)] = None,
        action: FilterValue = None,
        target: FilterValue = None,
        before: FilterValue = None,
        actor: Actor = Depends(get_web_actor),
        auditService: AuditService = Depends(Provide[Dependences.auditService]),
        ):
    """Who did what, newest first, 50 per page; `before` is the id of the last event shown."""
    events = await auditService.find(
        actor_sub=actor_filter or None, action=action or None, target_id=target or None,
        before_id=before or None, limit=AUDIT_PAGE)
    older = f"before={events[-1].id}" if len(events) >= AUDIT_PAGE else None
    filters = {"actor": actor_filter or "", "action": action or "", "target": target or ""}
    return render(request, "activity/audit.html", actor, events=events, filters=filters, older=older,
                  nav_active="audit")
