from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from src.dependences import Dependences, JobService, ModpackService, PterodactylService, UpdateCheckService
from src.library.attention import VISIBLE, PackFact, ServerFact, attention, failed_job_fact
from src.library.utils import as_utc, utcnow
from src.model.pterodactyl import PterodactylServer
from src.model.users import Actor
from src.web import WebRoute, get_web_actor, render

router = APIRouter(route_class=WebRoute, default_response_class=HTMLResponse)

RECENT_JOBS = 10
# The failed jobs read for the strip: the newest, of which only the last day's are shown.
FAILED_LOOKUP = 20

@dataclass(frozen=True)
class ServerRow:
    """A server as the dashboard shows it: its bundle and how its latest scan compared."""
    server: PterodactylServer
    bundle_name: Optional[str]
    state: str  # "sync", "drift", "unknown" (scanned, not compared) or "none" (never scanned)
    drift: Optional[str]
    scanned_at: Optional[datetime]

async def _server_row(pterodactylService: PterodactylService, server: PterodactylServer,
                      bundle_names: dict[str, str]) -> ServerRow:
    bundle_name = bundle_names.get(server.bundle_id) if server.bundle_id else None
    try:
        scan = await pterodactylService.scan_latest(str(server.id))
    except HTTPException as error:
        if error.status_code != 404:
            raise
        return ServerRow(server, bundle_name, "none", None, None)
    if scan.drift is None:
        state = "unknown"
    else:
        state = "sync" if scan.drift.startswith("✅") else "drift"
    return ServerRow(server, bundle_name, state, scan.drift, scan.scanned_at)

@router.get("/")
@inject
async def home(
        request: Request,
        actor: Actor = Depends(get_web_actor),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """The dashboard: what needs attention first (B2), then the servers with their bundle and drift,
    each bundle's latest build, and the most recent jobs."""
    builds = await modpackService.latest_builds()
    bundle_names = {str(bundle.id): bundle.name for bundle, _ in builds}
    servers = [await _server_row(pterodactylService, server, bundle_names)
               for server in await pterodactylService.server_find()]
    jobs = await jobService.recent(RECENT_JOBS)
    bundle_ids = [str(bundle.id) for bundle, _ in builds]
    # One query for every modpack; the pinned updates are shown on their page, never counted here.
    checks = await updateCheckService.latest_many(bundle_ids)
    update_counts = {str(bundle_id): sum(1 for u in check.updates if not u.pinned) for bundle_id, check in checks.items()}
    resolutions = await modpackService.resolution_many(bundle_ids)
    failed = await jobService.find(server_id=None, kind=None, status="failed", before_id=None, limit=FAILED_LOOKUP)
    server_names = {str(row.server.id): row.server.name for row in servers}
    checked_at = {str(bundle_id): check.checked_at for bundle_id, check in checks.items()}
    items = attention(
        [ServerFact(id=str(row.server.id), name=row.server.name, state=row.state, has_pack=row.bundle_name is not None,
                    scanned_at=row.scanned_at) for row in servers],
        [PackFact(id=bundle_id, name=bundle_names[bundle_id],
                  undecided=len(resolutions[bundle_id].lock.undecided) if bundle_id in resolutions else 0,
                  problems=len(resolutions[bundle_id].lock.problems) if bundle_id in resolutions else 0,
                  updates=update_counts.get(bundle_id, 0), checked_at=checked_at.get(bundle_id),
                  resolved_at=resolutions[bundle_id].resolved_at if bundle_id in resolutions else None)
         for bundle_id in bundle_ids],
        [failed_job_fact(job, server_names, bundle_names) for job in failed], utcnow())
    last_check = max((as_utc(check.checked_at) for check in checks.values()), default=None)
    return render(request, "home.html", actor, servers=servers, builds=builds, jobs=jobs, update_counts=update_counts,
                  server_names=server_names, attention=items, visible=VISIBLE, last_check=last_check)
