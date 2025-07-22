from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from src.model.modpack import ModpackBundle, ModpackBundlePatch
from src.dependences import *

router = APIRouter(
    tags=["modpack"],
)

@router.get("/modpack")
@inject
async def bundle_find(
        modpackDB: ModpackDatabase = Depends(Provide[Dependences.modpackDatabase]),
        current_user = Depends(get_current_user)
        ):
    response = await modpackDB.bundle_find(
    ).to_list()
    return response

@router.post("/modpack")
@inject
async def bundle_create(
        bundle: ModpackBundle,
        modpackDB: ModpackDatabase = Depends(Provide[Dependences.modpackDatabase]),
        current_user = Depends(get_current_user)
        ):
    response = await modpackDB.bundle_insert(bundle=bundle)
    return response

@router.get("/modpack/{id}")
@inject
async def bundle_get(
        id: str,
        modpackDB: ModpackDatabase = Depends(Provide[Dependences.modpackDatabase]),
        current_user = Depends(get_current_user)
        ):
    response = await modpackDB.bundle_get(bundle_id=id)
    return response

@router.patch("/modpack/{id}")
@inject
async def bundle_update(
        id: str,
        bundle: ModpackBundlePatch,
        modpackDB: ModpackDatabase = Depends(Provide[Dependences.modpackDatabase]),
        current_user = Depends(get_current_user)
        ):
    response = await modpackDB.bundle_update(bundle_id=id, patch=bundle)
    return response

@router.get("/modpack/{id}/project")
@inject
async def project_get(
        id: str,
        modpackDB: ModpackDatabase = Depends(Provide[Dependences.modpackDatabase]),
        current_user = Depends(get_current_user)
        ):
    response = await modpackDB.project_find(
        bundle_id=id
    ).to_list()
    return response

@router.delete("/modpack/{id}/project")
@inject
async def modpack_delete_project(
    id: str,
    project_id: str = Query(..., description="The ID of the project to delete"),
    modpackDB: ModpackDatabase = Depends(Provide[Dependences.modpackDatabase]),
    current_user = Depends(get_current_user)
):
    response = await modpackDB.project_delete(project_id=project_id, bundle_id=id)
    return response

@router.post("/modpack/{id}/modrinth")
@inject
async def modpack_add_modrinth_project(
        id: str,
        project_ids: list[str],
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    response = await modpack.project_modrinth_create_many(bundle_id=id, project_ids=project_ids)
    return response