from fastapi import APIRouter, Depends, Query
from dependency_injector.wiring import Provide, inject
from src.dependences import *

router = APIRouter(
    tags=["server"],
)

@router.get("/server")
@inject
async def server_get(
        serverService: ServerService = Depends(Provide[Dependences.serverService])
        ):
    response = await serverService.server_get()
    return response

@router.get("/server/{id}")
@inject
async def server_get(
        id: str,
        serverService: ServerService = Depends(Provide[Dependences.serverService])
        ):
    response = await serverService.server_get_id(id)
    return response

@router.get("/server/{id}/files")
@inject
async def server_get(
        id: str,
        directory: str = Query("/"),
        serverService: ServerService = Depends(Provide[Dependences.serverService])
        ):
    response = await serverService.server_files_id(id, directory)
    return response