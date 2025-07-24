from urllib import response
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from src.dependences import *

router = APIRouter(
    tags=["pterodactyl"],
)

@router.get("/server/external")
@inject
async def server_external(
        pterodactylAPI: PterodactylAPI = Depends(Provide[Dependences.pterodactylAPI]),
        current_user = Depends(get_current_user)
        ):
    """
    Get all Pterodactyl servers.
    """
    response = await pterodactylAPI.server_get_many()
    return response

@router.get("/server")
@inject
async def server_find(
        pterodactylDB: PterodactylDatabase = Depends(Provide[Dependences.pterodactylDatabase]),
        current_user = Depends(get_current_user)
        ):
    return await pterodactylDB.server_find(
    ).to_list()

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
        pterodactylDB: PterodactylDatabase = Depends(Provide[Dependences.pterodactylDatabase]),
        current_user = Depends(get_current_user)
        ):
    response = await pterodactylDB.server_get(id)
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

@router.get("/version")
@inject
async def version_find(
        server_id: str,
        pterodactylDB: PterodactylDatabase = Depends(Provide[Dependences.pterodactylDatabase]),
        current_user = Depends(get_current_user)
        ):
    """
    Get all Pterodactyl versions.
    """
    return await pterodactylDB.version_find(
        server_id=server_id
    ).to_list()

@router.get("/version/{id}")
@inject
async def version_get(
        id: str,
        pterodactylDB: PterodactylDatabase = Depends(Provide[Dependences.pterodactylDatabase]),
        current_user = Depends(get_current_user)
        ):
    """
    Get a single Pterodactyl version by its ID.
    """
    response = await pterodactylDB.version_get(id)
    return response