from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from typing import Optional
from src.model.modrinth import ModrinthProject
from src.model.utils import MinecraftLoaders
from src.dependences import *

router = APIRouter(
    tags=["modrinth"],
)

@router.get(
    path="/modrinth/project",
    response_model=list[ModrinthProject],
)
@inject
async def project_get_many(
        game_version: Optional[str] = Query(None, description="Filter by game versions"),
        loader: Optional[MinecraftLoaders] = Query(None, description="Filter by loaders"),
        projectService: ModrinthProjectService = Depends(Provide[Dependences.modrinthProjectService]),
        current_user = Depends(get_current_user)
        ):
    return await projectService.project_get_many(
        game_version=game_version,
        loader=loader
    ).to_list()

@router.post(
    path="/modrinth/project",
    response_model=list[ModrinthProject],
)
@inject
async def project_create_many(
        slugs: list[str],
        projectService: ModrinthProjectService = Depends(Provide[Dependences.modrinthProjectService]),
        current_user = Depends(get_current_user)
        ):
    response = await projectService.project_create_many(slugs)
    return response

@router.post(
    path="/modrinth/project/update",
    response_model=list[ModrinthProject],
)
@inject
async def project_update_many(
        ids: list[str],
        projectService: ModrinthProjectService = Depends(Provide[Dependences.modrinthProjectService]),
        current_user = Depends(get_current_user)
        ):
    response = await projectService.project_update_many(ids)
    return response

@router.get(
    path="/modrinth/project/{id}",
    response_model=ModrinthProject,
)
@inject
async def project_get_one(
        id: str,
        ModrinthProjectService: ModrinthProjectService = Depends(Provide[Dependences.modrinthProjectService]),
        current_user = Depends(get_current_user)
        ):
    response = await ModrinthProjectService.project_get_one(id)
    return response