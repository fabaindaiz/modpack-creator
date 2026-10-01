from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from typing import Optional
from src.library.scan import Comparison
from src.model.jobs import Job
from src.model.pterodactyl import ServerScan
from src.dependences import *

router = APIRouter(
    tags=["pterodactyl"],
)

@router.get("/server/external")
@inject
async def server_external(
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    """
    Get all Pterodactyl servers.
    """
    response = await pterodactylService.server_external()
    return response

@router.get("/server")
@inject
async def server_find(
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    return await pterodactylService.server_find()

@router.post("/server")
@inject
async def server_create(
        uuid: str,
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    """
    Create a new Pterodactyl server.
    """
    response = await pterodactylService.server_create(uuid=uuid)
    return response

@router.patch("/server")
@inject
async def server_update(
        server_id: str,
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    """
    Update a Pterodactyl server.
    """
    response = await pterodactylService.server_update(server_id=server_id)
    return response

@router.get("/server/{id}")
@inject
async def server_get_id(
        id: str,
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    response = await pterodactylService.server_get(id)
    return response

@router.get("/server/{id}/files")
@inject
async def server_files_get_id(
        id: str,
        directory: str = Query("/"),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    response = await pterodactylService.server_files(id, directory)
    return response

@router.post("/server/{id}/scan", response_model=ServerScan)
@inject
async def server_scan(
        id: str,
        directory: str = Query("/mods"),
        full: bool = Query(False, description="Hash every jar again, trusting no cached hash"),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    """
    Scan a server: which jars it has and which Modrinth version each is. Only reads the server.
    """
    return await pterodactylService.scan(id, directory=directory, full=full)

@router.post("/server/{id}/scan/job", response_model=Job, status_code=202)
@inject
async def server_scan_job(
        id: str,
        full: bool = Query(False, description="Hash every jar again, trusting no cached hash"),
        jobs: JobService = Depends(Provide[Dependences.jobService]),
        current_user = Depends(get_current_user)
        ):
    """
    Queue a scan of the server, run by the job worker. 409 if the server already has an active job.
    """
    return await jobs.enqueue_scan(id, full=full)

@router.get("/server/{id}/scan", response_model=ServerScan)
@inject
async def server_scan_latest(
        id: str,
        directory: str = Query("/mods"),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    return await pterodactylService.scan_latest(id, directory=directory)

@router.get("/server/{id}/compare", response_model=Comparison)
@inject
async def server_compare(
        id: str,
        bundle_id: str,
        build: Optional[int] = Query(None, description="Build number; by default the recommended, else the latest"),
        directory: str = Query("/mods"),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    """
    Compare the server's latest scan with a build of a bundle.
    """
    return await pterodactylService.compare(id, bundle_id, build=build, directory=directory)
