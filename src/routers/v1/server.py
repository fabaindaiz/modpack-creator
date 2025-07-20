from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from src.dependences import *

router = APIRouter(
    tags=["server"],
)

@router.get("/server")
@inject
async def server_get(
        pterodactylApi: PterodactylAPI = Depends(Provide[Dependences.pterodactylAPI]),
        current_user = Depends(get_current_user)
        ):
    return await pterodactylApi.server_list()

@router.get("/server/{id}")
@inject
async def server_get_id(
        id: str,
        pterodactylAPI: PterodactylAPI = Depends(Provide[Dependences.pterodactylAPI]),
        current_user = Depends(get_current_user)
        ):
    return await pterodactylAPI.server_id(id)

@router.get("/server/{id}/files")
@inject
async def server_files_get_id(
        id: str,
        directory: str = Query("/"),
        pterodactylAPI: PterodactylAPI = Depends(Provide[Dependences.pterodactylAPI]),
        current_user = Depends(get_current_user)
        ):
    return await pterodactylAPI.server_files_list(id, directory)