import re
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request
from starlette.datastructures import UploadFile
from typing import Optional
from pydantic import BaseModel
from fastapi import Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from src.exceptions import HTTP_422_UNPROCESSABLE_ENTITY, CatalogPending
from src.routers.web.modpacks import IMPORT_MAX_BYTES, _upload_room
from src.library.cf_import import is_curseforge
from src.library.prism_import import is_prism
from src.web import BUNDLE_REF
from src.model.jobs import Job
from src.model.users import Actor
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
        actor: Actor = Depends(get_current_actor)
        ):
    response = await modpack.bundle_create(bundle=ModpackBundle(**bundle.model_dump()), actor=actor)
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
        confirm_downgrade: bool = Query(False, description="save a pin before its mod's floor (the build in force, "
                                                           "a linked server): without it, such a pin is a 409"),
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        actor: Actor = Depends(get_current_actor)
        ):
    response = await modpack.bundle_update(bundle_id=id, patch=bundle, actor=actor, confirm_downgrade=confirm_downgrade)
    return response

class ViaChange(BaseModel):
    project_id: str
    # True: to its native versions; False: back through its bridge.
    native: bool

@router.post("/modpack/{id}/via")
@inject
async def bundle_via(
        id: str,
        change: ViaChange,
        confirm_via: bool = Query(False, description="move the mod's way (native or through its bridge): without it, "
                                                     "a 409 that describes the change and its risk"),
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """Move a mod between its native and bridged versions (batch C, d-7985ee-7e602f)."""
    return await modpack.via_set(id, change.project_id, change.native, actor, confirm=confirm_via)

@router.delete("/modpack/{id}/replacement/{project_id}", response_model=ModpackBundle)
@inject
async def bundle_replacement_delete(
        id: str,
        project_id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """Remove the pack's own replacement rule for one mod; the global rule, if any, applies again.
    In the PATCH, a null replacement means "drop the mod", not "remove the rule"."""
    return await modpack.bundle_replacement_delete(bundle_id=id, project_id=project_id, actor=actor)

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
        actor: Actor = Depends(get_current_actor)
        ):
    """Add Modrinth projects, by slug or id. All or nothing: an unknown one refuses the request."""
    return await modpack.project_add(bundle_id=id, refs=refs, origin=origin, actor=actor)

@router.put("/modpack/{id}/project", response_model=list[PackProject])
@inject
async def pack_replace(
        id: str,
        refs: list[str],
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """Replace what the pack brings; what the admin added stays."""
    return await modpack.pack_replace(bundle_id=id, refs=refs, actor=actor)

@router.delete("/modpack/{id}/project/{project_id}", response_model=list[PackProject])
@inject
async def project_remove(
        id: str,
        project_id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        actor: Actor = Depends(get_current_actor)
        ):
    return await modpack.project_remove(bundle_id=id, project_id=project_id, actor=actor)

@router.post("/modpack/{id}/build", response_model=Job, status_code=202)
@inject
async def build_create(
        id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        actor: Actor = Depends(get_current_actor)
        ):
    """Queue the next build: 202 with its job, the panel's own (one per pack: asking again gives the
    running one; poll /v1/job/{id}). 409 now if the pack, resolved from the catalog, has problems
    or undecided mods (owner's decision U5, 2026-10-09)."""
    return await modpack.build_request(bundle_id=id, actor=actor)

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
        actor: Actor = Depends(get_current_actor)
        ):
    """Point recommended at a build; going back is pointing at an older one."""
    return await modpack.build_recommend(bundle_id=id, number=number, actor=actor)

@router.post("/modpack/{id}/catalog", response_model=Job, status_code=202)
@inject
async def modpack_catalog(
        id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        actor: Actor = Depends(get_current_actor),
        ):
    """
    «Actualizar desde Modrinth»: the pack's catalog job, checking Modrinth now (key `catalog:<id>`,
    the panel's). Once a minute per pack; while one is queued, that one. Poll /v1/job/{id}.
    """
    return await modpack.catalog_refresh(id, actor)

def _into(into: Optional[str]) -> Optional[str]:
    if into and not re.fullmatch(BUNDLE_REF, into):
        raise HTTP_422_UNPROCESSABLE_ENTITY("Ese modpack no existe")
    return into or None

@router.post("/modpack/import/modrinth", response_model=Job, status_code=202)
@inject
async def modpack_import_modrinth(
        ref: str = Query(..., min_length=1, max_length=500, description="A Modrinth modpack: URL, slug or id"),
        into: Optional[str] = Query(None, description="A pack whose content it replaces; a new pack if empty"),
        importer: ModpackImportService = Depends(Provide[Dependences.modpackImportService]),
        actor: Actor = Depends(get_current_actor),
        ):
    """Import a modpack published on Modrinth, as the panel does: a job (`modpack_import`). Poll
    /v1/job/{id}; its checkpoint holds the report."""
    return await importer.import_modrinth(ref.strip(), actor, into=_into(into))

@router.post("/modpack/import/mrpack", response_model=Job, status_code=202,
             openapi_extra={"requestBody": {"content": {"multipart/form-data": {"schema": {
                 "type": "object", "required": ["archivo"],
                 "properties": {"archivo": {"type": "string", "format": "binary",
                                            "description": "A .mrpack (50 MiB at most) or a Prism instance zip (1 GiB)"}}}}}}})
@inject
async def modpack_import_mrpack(
        request: Request,
        into: Optional[str] = Query(None, description="A pack whose content it replaces; a new pack if empty"),
        importer: ModpackImportService = Depends(Provide[Dependences.modpackImportService]),
        actor: Actor = Depends(get_current_actor),
        ):
    """Import an uploaded .mrpack, Prism instance zip or CurseForge zip (field `archivo`; the last
    needs the owner's key), as the panel does: read now,
    identified in a job. A .mrpack past 50 MiB is a 422; any upload past 1 GiB, 413; past the
    temporary disk the uploads in flight may take, 429 (the form is read only after reserving it)."""
    with _upload_room(request):
        form = await request.form(max_files=1, max_fields=1)
        archivo = form.get("archivo")
        if not isinstance(archivo, UploadFile):
            raise HTTP_422_UNPROCESSABLE_ENTITY("Falta el archivo (campo «archivo»)")
        if is_prism(archivo.file):
            return await importer.import_prism(archivo.file, actor, into=_into(into))
        if is_curseforge(archivo.file):
            return await importer.import_curseforge(await archivo.read(IMPORT_MAX_BYTES + 1), actor, into=_into(into))
        return await importer.import_mrpack(await archivo.read(IMPORT_MAX_BYTES + 1), actor, into=_into(into))

@router.post("/modpack/import/curseforge", response_model=Job, status_code=202)
@inject
async def modpack_import_curseforge_code(
        code: str = Query(..., min_length=1, max_length=40, description="A CurseForge profile code (it lives 7 days)"),
        into: Optional[str] = Query(None, description="A pack whose content it replaces; a new pack if empty"),
        importer: ModpackImportService = Depends(Provide[Dependences.modpackImportService]),
        actor: Actor = Depends(get_current_actor),
        ):
    """Import a CurseForge profile code, as the panel does: a job. Only with the owner's CurseForge
    key (422 without it); it uses an undocumented CurseForge endpoint (C5)."""
    return await importer.import_curseforge_code(code, actor, into=_into(into))

@router.post("/modpack/{id}/resolve", response_model=Lock, responses={202: {"model": Job}})
@inject
async def modpack_resolve(
        id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        actor: Actor = Depends(get_current_actor),
        ):
    """
    Resolve the modpack from the catalog: every project with its dependencies, replacements applied,
    and the problems found. When the catalog lacks something, 202 with the catalog job that brings
    it (spec 2026-10-09-economia-de-requests); poll /v1/job/{id} and ask again. Nothing is installed.
    """
    try:
        return await modpack.resolve(bundle_id=id, actor=actor)
    except CatalogPending as pending:
        return JSONResponse(status_code=202, content=jsonable_encoder(pending.job))

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
        actor: Actor = Depends(get_current_actor)
        ):
    return await modpack.replacement_set(project_id=project_id, replacement=replacement, actor=actor)

@router.delete("/replacement/{project_id}")
@inject
async def replacement_delete(
        project_id: str,
        modpack: ModpackService = Depends(Provide[Dependences.modpackService]),
        actor: Actor = Depends(get_current_actor)
        ):
    return await modpack.replacement_delete(project_id=project_id, actor=actor)
