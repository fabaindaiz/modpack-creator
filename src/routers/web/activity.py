import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Optional
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from src.exceptions import HTTP_404_NOT_FOUND, HTTP_422_UNPROCESSABLE_ENTITY
from src.dependences import AuditService, Dependences, JobService, ModpackService, ModrinthService, PterodactylService
from src.model.jobs import Job, is_real_update
from src.model.pterodactyl import ImportResult
from src.model.users import Actor
from src.routers.web.servers import preview_context
from src.web import WebRoute, get_web_actor, render
from src.web.audit_labels import ACTIONS as AUDIT_ACTIONS

logger = logging.getLogger("modpack.web")
router = APIRouter(route_class=WebRoute, default_response_class=HTMLResponse)

JOBS_SHOWN = 50
# The event a finished job's row sends: #server-main and #updates-main reload on it.
JOB_FINISHED = "job-finished"
AUDIT_PAGE = 50
# A filter or a cursor: kept short, and passed to the service as the exact value.
FilterValue = Annotated[Optional[str], Query(max_length=200)]

@router.get("/trabajos/mios")
@inject
async def my_jobs(
        request: Request,
        lista: Annotated[Optional[str], Query(max_length=1)] = None,
        actor: Actor = Depends(get_web_actor),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        ):
    """The bar's jobs (E8): what this person asked for that is queued or running, with its
    progress and a shortcut; it polls only while there are some. `lista`: the list alone, and
    `my-jobs-done` when it is empty, so the bar empties."""
    jobs = await jobService.active_for_user(actor)
    response = render(request, "_my_jobs.html", actor, jobs=jobs, lista=bool(lista))
    if lista and not jobs:
        response.headers["HX-Trigger"] = "my-jobs-done"
    return response

def _day(value: Optional[str], label: str) -> Optional[datetime]:
    """A `YYYY-MM-DD` date as midnight UTC; empty is no bound; anything else is a 422."""
    if not value:
        return None
    try:
        if len(value) != 10:
            raise ValueError(value)
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{label}' no es una fecha válida (AAAA-MM-DD)")

async def _server_names(pterodactylService: PterodactylService) -> dict[str, str]:
    """Server id to name, for the tables."""
    return {str(server.id): server.name for server in await pterodactylService.server_find()}

async def _server_context(job: Job, pterodactylService: PterodactylService,
                          jobService: Optional[JobService] = None,
                          modrinthService: Optional[ModrinthService] = None) -> dict[str, Any]:
    """A job about a server carries the server's name (for the real run's confirmation hint); a
    server that is gone just shows no hint. With `jobService`, a finished dry run also carries the
    server's pending rollback, if any: its real run discards it (R17) and the dialog says so."""
    context: dict[str, Any] = {}
    server_id = job.payload.get("server_id")
    if isinstance(server_id, str):
        try:
            context["server_name"] = (await pterodactylService.server_get(server_id)).name
        except HTTPException:
            pass
        if jobService is not None and job.kind == "server_update" and job.status == "done" \
                and not is_real_update(job):
            context["rollback"] = await jobService.rollback_state(server_id)
        if job.kind == "server_import" and job.status == "done" and isinstance(job.checkpoint.get("import"), dict):
            # The import's full report lives in its finished row (owner, 2026-10-12): the
            # server's page keeps only what waits for a decision.
            result = ImportResult(**job.checkpoint["import"])
            context["import_result"] = result
            context["import_titles"] = await _import_titles(modrinthService, result) if modrinthService else {}
    return context

async def _import_titles(modrinthService: ModrinthService, result: ImportResult) -> dict[str, str]:
    """The added mods' and candidates' titles, in one catalog fill; an id the fill cannot name, or a
    fill that fails, shows as itself (a nicety on a finished job's row)."""
    ids = list(dict.fromkeys([*result.added, *(c.project_id for item in result.candidates for c in item.candidates)]))
    if not ids:
        return {}
    try:
        found = await modrinthService.projects_filled(ids)
    except Exception:
        logger.warning("titles of a finished import failed", exc_info=True)
        return {}
    return {i: found[i].title for i in ids if i in found}

@router.get("/jobs")
@inject
async def job_list(
        request: Request,
        servidor: FilterValue = None,
        tipo: FilterValue = None,
        estado: FilterValue = None,
        before: FilterValue = None,
        actor: Actor = Depends(get_web_actor),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        ):
    """Jobs newest first, 50 per page, filtered by exact values; `before` is the id of the last
    job shown."""
    jobs = await jobService.find(server_id=servidor or None, kind=tipo or None, status=estado or None,
                                 before_id=before or None, limit=JOBS_SHOWN + 1)
    has_older = len(jobs) > JOBS_SHOWN  # one more than shown: only then is there a next page
    jobs = jobs[:JOBS_SHOWN]
    older = f"before={jobs[-1].id}" if has_older else None
    filters = {"servidor": servidor or "", "tipo": tipo or "", "estado": estado or ""}
    return render(request, "activity/jobs.html", actor, jobs=jobs, filters=filters, older=older,
                  server_names=await _server_names(pterodactylService), nav_active="jobs")

@router.get("/jobs/{job_id}")
@inject
async def job_page(
        job_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """One job in full: the shared detail, with its plan and, for an operator, the real run."""
    job = await jobService.get(job_id)
    return render(request, "activity/job.html", actor, job=job, nav_active="jobs",
                  **await _server_context(job, pterodactylService, jobService, modrinthService))

@router.get("/jobs/{job_id}/row")
@inject
async def job_row(
        job_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """One job's detail: what an active detail polls. A finished one also tells the page
    (`HX-Trigger: job-finished`), so its sections reload (F19): only once, since a finished row
    does not poll again, and never for an active one, which would reload the page every 2 s."""
    job = await jobService.get(job_id)
    response = render(request, "activity/_job_row.html", actor, job=job,
                      **await _server_context(job, pterodactylService, jobService, modrinthService))
    if not job.active:
        response.headers["HX-Trigger"] = JOB_FINISHED
    return response

@router.get("/jobs/{job_id}/preview")
@inject
async def job_preview(
        job_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """What a finished dry run's plan changes on its server, with changelogs: loaded by the result
    after the page, because it may wait for Modrinth. A read; anything but a finished dry run with a
    plan is a 404."""
    job = await jobService.get(job_id)
    server_id = job.payload.get("server_id")
    build = job.checkpoint.get("preflight", {}).get("build")
    if job.kind != "server_update" or job.status != "done" or is_real_update(job) \
            or not isinstance(server_id, str) or not isinstance(build, int):
        raise HTTP_404_NOT_FOUND(f"Job '{job_id}' is not a finished dry run")
    return render(request, "servers/_preview_body.html", actor,
                  **await preview_context(modpackService, modrinthService, server_id, build))

@router.get("/jobs/{job_id}/summary")
@inject
async def job_summary(
        job_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        ):
    """The line of one job's card (its `<summary>`): what an active card polls. Only the line, so
    an open card stays open while it polls."""
    job = await jobService.get(job_id)
    context = await _server_context(job, pterodactylService)
    names = {job.payload["server_id"]: context["server_name"]} if "server_name" in context else {}
    return render(request, "activity/_job_summary.html", actor, job=job, server_names=names)

@router.get("/audit")
@inject
async def audit_log(
        request: Request,
        actor_filter: Annotated[Optional[str], Query(alias="actor", max_length=200)] = None,
        action: FilterValue = None,
        target: FilterValue = None,
        before: FilterValue = None,
        desde: FilterValue = None,
        hasta: FilterValue = None,
        actor: Actor = Depends(get_web_actor),
        auditService: AuditService = Depends(Provide[Dependences.auditService]),
        ):
    """Who did what, newest first, 50 per page; `before` is the id of the last event shown. Person
    and action are exact values, an action the panel does not know is ignored; `target` is
    `kind:id` (what the list offers) or, without a colon, the exact name an event kept. Each
    filter's options come with their counts (A11, A12)."""
    since = _day(desde, "Desde")
    last_day = _day(hasta, "Hasta")
    if since is not None and last_day is not None and last_day < since:
        raise HTTP_422_UNPROCESSABLE_ENTITY("'Hasta' es anterior a 'Desde'")
    try:
        until = last_day + timedelta(days=1) if last_day is not None else None  # the whole day is included
    except OverflowError:
        raise HTTP_422_UNPROCESSABLE_ENTITY("'Hasta' no es una fecha válida (AAAA-MM-DD)")
    action = action if action in AUDIT_ACTIONS else None
    kind, colon, target_id = (target or "").partition(":")
    page = await auditService.page(
        actor_sub=actor_filter or None, action=action,
        target=(kind, target_id) if colon else None, target_name=None if colon else (target or None),
        before_id=before or None, limit=AUDIT_PAGE + 1, since=since, until=until)
    has_older = len(page.events) > AUDIT_PAGE
    events = page.events[:AUDIT_PAGE]
    older = f"before={events[-1].id}" if has_older else None
    filters = {"actor": actor_filter or "", "action": action or "", "target": target or "",
               "desde": desde or "", "hasta": hasta or ""}
    return render(request, "activity/audit.html", actor, events=events, filters=filters, older=older, page=page,
                  action_counts={facet.value: facet.count for facet in page.actions},
                  names=await auditService.names_for(events), nav_active="audit")
