from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from src.dependences import Dependences, JobService, ModpackService, PterodactylService, UpdateCheckService
from src.model.pterodactyl import PterodactylServer
from src.model.users import Actor
from src.web import WebRoute, get_web_actor, render

router = APIRouter(route_class=WebRoute, default_response_class=HTMLResponse)

RECENT_JOBS = 10

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
    """The dashboard: servers with their bundle and drift, each bundle's latest build, and the
    most recent jobs."""
    builds = await modpackService.latest_builds()
    bundle_names = {str(bundle.id): bundle.name for bundle, _ in builds}
    servers = [await _server_row(pterodactylService, server, bundle_names)
               for server in await pterodactylService.server_find()]
    jobs = await jobService.recent(RECENT_JOBS)
    # One query for every modpack; the pinned updates are shown on their page, never counted here.
    checks = await updateCheckService.latest_many([str(bundle.id) for bundle, _ in builds])
    update_counts = {str(bundle_id): sum(1 for u in check.updates if not u.pinned) for bundle_id, check in checks.items()}
    return render(request, "home.html", actor, servers=servers, builds=builds, jobs=jobs, update_counts=update_counts,
                  server_names={str(row.server.id): row.server.name for row in servers})
