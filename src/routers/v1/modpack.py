from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from typing import Optional
from fastapi import Response
from src.library.resolution import Lock
from src.model.modpack import ModpackBuild, ModpackBundle, ModpackBundleCreate, ModpackBundlePatch, ModpackReplacement, PackProject, ProjectOrigin
from src.dependences import *

router = APIRouter(
    tags=["modpack"],
)

@router.get("/modpack")
@inject
async def bundle_find(
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    response = await modpack.bundle_find()
    return response

@router.post("/modpack")
@inject
async def bundle_create(
        bundle: ModpackBundleCreate,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    response = await modpack.bundle_create(bundle=ModpackBundle(**bundle.model_dump()))
    return response

@router.get("/modpack/{id}")
@inject
async def bundle_get(
        id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    response = await modpack.bundle_get(bundle_id=id)
    return response

@router.patch("/modpack/{id}")
@inject
async def bundle_update(
        id: str,
        bundle: ModpackBundlePatch,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    response = await modpack.bundle_update(bundle_id=id, patch=bundle)
    return response

@router.get("/modpack/{id}/project", response_model=list[PackProject])
@inject
async def project_find(
        id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    return await modpack.project_find(bundle_id=id)

@router.post("/modpack/{id}/project", response_model=list[PackProject])
@inject
async def project_add(
        id: str,
        refs: list[str],
        origin: ProjectOrigin = Query(ProjectOrigin.admin, description="pack: what the pack brings; admin: added by hand"),
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    """Add Modrinth projects, by slug or id. All or nothing: an unknown one refuses the request."""
    return await modpack.project_add(bundle_id=id, refs=refs, origin=origin)

@router.put("/modpack/{id}/project", response_model=list[PackProject])
@inject
async def pack_replace(
        id: str,
        refs: list[str],
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    """Replace what the pack brings; what the admin added stays."""
    return await modpack.pack_replace(bundle_id=id, refs=refs)

@router.delete("/modpack/{id}/project/{project_id}", response_model=list[PackProject])
@inject
async def project_remove(
        id: str,
        project_id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    return await modpack.project_remove(bundle_id=id, project_id=project_id)

@router.post("/modpack/{id}/build", response_model=ModpackBuild)
@inject
async def build_create(
        id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    """Resolve and freeze the next build. 409 if the lock has problems or undecided mods."""
    return await modpack.build_create(bundle_id=id)

@router.get("/modpack/{id}/build", response_model=list[ModpackBuild])
@inject
async def build_find(
        id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    return await modpack.build_find(bundle_id=id)

@router.get("/modpack/{id}/build/{number}", response_model=ModpackBuild)
@inject
async def build_get(
        id: str,
        number: int,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    return await modpack.build_get(bundle_id=id, number=number)

@router.get("/modpack/{id}/build/{number}/mrpack")
@inject
async def build_mrpack(
        id: str,
        number: int,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    """The build as a .mrpack for players: the same files the server gets, by Modrinth URL."""
    filename, data = await modpack.build_mrpack(bundle_id=id, number=number)
    return Response(content=data, media_type="application/x-modrinth-modpack+zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})

@router.put("/modpack/{id}/recommended/{number}", response_model=ModpackBundle)
@inject
async def build_recommend(
        id: str,
        number: int,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    """Point recommended at a build; going back is pointing at an older one."""
    return await modpack.build_recommend(bundle_id=id, number=number)

@router.post("/modpack/{id}/resolve", response_model=Lock)
@inject
async def modpack_resolve(
        id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    """
    Resolve the modpack: every project with its dependencies, replacements applied, and the
    problems found. Fetches from Modrinth what it needs; nothing is installed anywhere.
    """
    return await modpack.resolve(bundle_id=id)

@router.get("/replacement", response_model=list[ModpackReplacement])
@inject
async def replacement_find(
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    """The global replacement rules; each pack can add or override them."""
    return await modpack.replacement_find()

@router.put("/replacement/{project_id}", response_model=ModpackReplacement)
@inject
async def replacement_set(
        project_id: str,
        replacement: Optional[str] = Query(None, description="The substitute project id; empty drops the dependency"),
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    return await modpack.replacement_set(project_id=project_id, replacement=replacement)

@router.delete("/replacement/{project_id}")
@inject
async def replacement_delete(
        project_id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        current_user = Depends(get_current_user)
        ):
    return await modpack.replacement_delete(project_id=project_id)
