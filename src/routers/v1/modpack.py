from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from src.model.modpack import ModpackBundle, ModpackProject
from src.dependences import *

router = APIRouter(
    tags=["modpack"],
)

@router.get("/modpack")
@inject
async def modpack_get_all(
        modpackBundleService: ModpackBundleService = Depends(Provide[Dependences.modpackBundleService]),
        current_user = Depends(get_current_user)
        ):
    response = await modpackBundleService.bundle_get_all()
    return response

@router.post("/modpack")
@inject
async def modpack_create_one(
        bundle: ModpackBundle,
        modpackBundleService: ModpackBundleService = Depends(Provide[Dependences.modpackBundleService]),
        current_user = Depends(get_current_user)
        ):
    response = await modpackBundleService.bundle_create_one(bundle)
    return response

@router.get("/modpack/{id}")
@inject
async def modpack_get_one(
        id: str,
        modpackBundleService: ModpackBundleService = Depends(Provide[Dependences.modpackBundleService]),
        current_user = Depends(get_current_user)
        ):
    response = await modpackBundleService.bundle_get_one(id)
    return response

@router.get("/modpack/projects")
@inject
async def modpack_get_all_projects(
        modpackBundleService: ModpackBundleService = Depends(Provide[Dependences.modpackBundleService]),
        current_user = Depends(get_current_user)
        ):
    response = await modpackBundleService.project_get_all()
    return response

@router.get("/modpack/{id}/projects")
@inject
async def modpack_get_projects(
        id: str,
        modpackBundleService: ModpackBundleService = Depends(Provide[Dependences.modpackBundleService]),
        current_user = Depends(get_current_user)
        ):
    response = await modpackBundleService.project_get_all(id)
    return response

@router.post("/modpack/{id}/projects/modrinth")
@inject
async def modpack_add_modrinth_project(
        id: str,
        project_id: str = Query(..., description="Modrinth project ID to add"),
        modpackBundleService: ModpackBundleService = Depends(Provide[Dependences.modpackBundleService]),
        current_user = Depends(get_current_user)
        ):
    response = await modpackBundleService.project_modrinth_add_one(id, project_id)
    return response