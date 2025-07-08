from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from src.dependences import *

router = APIRouter(
    tags=["server"],
)

@router.get("/server")
@inject
async def server_get(
        serverService: PterodactylServerService = Depends(Provide[Dependences.pterodactylServerService]),
        current_user = Depends(get_current_user)
        ):
    response = await serverService.server_get()
    return response

@router.get("/server/{id}")
@inject
async def server_get(
        id: str,
        serverService: PterodactylServerService = Depends(Provide[Dependences.pterodactylServerService]),
        current_user = Depends(get_current_user)
        ):
    response = await serverService.server_get_id(id)
    return response 

@router.get("/server/{id}/files")
@inject
async def server_get(
        id: str,
        directory: str = Query("/"),
        serverService: PterodactylServerService = Depends(Provide[Dependences.pterodactylServerService]),
        current_user = Depends(get_current_user)
        ):
    response = await serverService.server_files_id(id, directory)
    return response