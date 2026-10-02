from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from typing import Optional
from src.library.scan import Comparison, LocalFile
from src.model.modpack import PackProject
from src.model.jobs import Job
from src.model.pterodactyl import ImportResult, PterodactylServer, ServerScan
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

@router.put("/server/{id}/bundle", response_model=PterodactylServer)
@inject
async def server_link_bundle(
        id: str,
        bundle_id: Optional[str] = Query(None, description="The bundle this server runs; empty unlinks it"),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    """Link the server to the bundle it runs: scans are compared with it by default."""
    return await pterodactylService.server_link_bundle(id, bundle_id)

@router.post("/server/{id}/import", response_model=ImportResult)
@inject
async def server_import(
        id: str,
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    """
    Bring the jars of the server's latest scan that its bundle does not explain into the bundle:
    by hash as the admin's, by metadata as candidates to confirm, the rest as local files.
    """
    return await pterodactylService.import_server(id)

@router.post("/server/{id}/import/confirm", response_model=list[PackProject])
@inject
async def server_import_confirm(
        id: str,
        path: str,
        project_id: str,
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    """Confirm that a jar of the server is this Modrinth project."""
    return await pterodactylService.import_confirm(id, path, project_id)

@router.post("/server/{id}/import/local", response_model=list[LocalFile])
@inject
async def server_import_local(
        id: str,
        path: str,
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    """Keep a jar of the server as a local file of its bundle."""
    return await pterodactylService.import_local(id, path)

@router.post("/server/{id}/update", response_model=Job, status_code=202)
@inject
async def server_update_build(
        id: str,
        build: Optional[int] = Query(None, description="Build number; by default the recommended, else the latest"),
        dry_run: bool = Query(True, description="Read and verify everything, write nothing; false to really update"),
        updates: UpdateService = Depends(Provide[Dependences.updateService]),
        current_user = Depends(get_current_user)
        ):
    """
    Queue an update of the server to a build of its bundle. A dry run unless dry_run=false, and
    even then nothing is written unless PTERODACTYL_WRITES is enabled. Follow it with GET /job/{id}.
    """
    return await updates.enqueue_update(id, build=build, dry_run=dry_run)

@router.post("/server/{id}/rollback", response_model=Job, status_code=202)
@inject
async def server_rollback(
        id: str,
        update: str = Query(..., description="The id of the failed update job"),
        mode: str = Query(..., description="A: bring the old jars back. B: restore the pre-update backup (loses what was played since)"),
        updates: UpdateService = Depends(Provide[Dependences.updateService]),
        current_user = Depends(get_current_user)
        ):
    """Queue rollback A or B of a failed update; never automatic."""
    return await updates.enqueue_rollback(id, update, mode)

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
        bundle_id: Optional[str] = Query(None, description="By default, the bundle the server is linked to"),
        build: Optional[int] = Query(None, description="Build number; by default the recommended, else the latest"),
        directory: str = Query("/mods"),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        current_user = Depends(get_current_user)
        ):
    """
    Compare the server's latest scan with a build of a bundle.
    """
    return await pterodactylService.compare(id, bundle_id, build=build, directory=directory)
