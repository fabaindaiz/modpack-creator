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
async def project_find(
        game_version: Optional[str] = Query(None, description="Filter by game versions"),
        loader: Optional[MinecraftLoaders] = Query(None, description="Filter by loaders"),
        modrinth: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        current_user = Depends(get_current_user)
        ):
    return await modrinth.project_find(game_version=game_version, loader=loader)

@router.post(
    path="/modrinth/project",
    response_model=list[ModrinthProject],
)
@inject
async def project_create_many(
        slugs: list[str],
        modrinth: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        current_user = Depends(get_current_user)
        ):
    response = await modrinth.project_create_many(slugs=slugs)
    return response

@router.patch(
    path="/modrinth/project",
    response_model=list[ModrinthProject],
)
@inject
async def project_update_many(
        ids: list[str],
        modrinth: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        current_user = Depends(get_current_user)
        ):
    response = await modrinth.project_update_many(project_ids=ids)
    return response

@router.get(
    path="/modrinth/project/{id}",
    response_model=ModrinthProject,
)
@inject
async def project_get(
        id: str,
        modrinth: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        current_user = Depends(get_current_user)
        ):
    response = await modrinth.project_get(project_id=id)
    return response