from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from src.dependences import *

router = APIRouter(
    tags=["project"],
)

@router.get("/project/modrinth/{slug}")
@inject
async def project_get_one(
        slug: str,
        modrinthAPI: ModrinthAPI = Depends(Provide[Dependences.modrinthAPI]),
        current_user = Depends(get_current_user)
        ):
    response = await modrinthAPI.project_get_one(slug)
    return response

@router.post("/project/modrinth/{slug}")
@inject
async def project_add_one(
        slug: str,
        projectService: ModrinthProjectService = Depends(Provide[Dependences.modrinthProjectService]),
        current_user = Depends(get_current_user)
        ):
    response = await projectService.project_add_one(slug)
    return response