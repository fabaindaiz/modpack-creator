"""A mod's local page (spec §2, "Ficha local de cada mod"): `/mods/{id}`, its compact version for
the side panel, and the table with every version Modrinth has. Reads for the editor: what they write
is only the catalog of Modrinth data, through ModpackService."""
import logging
import re
from typing import Any, Optional

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from src.dependences import Dependences, ModpackService
from src.exceptions import HTTP_404_NOT_FOUND, UpstreamError
from src.model.users import Actor
from src.routers.web.modpacks import _https_url, _icon, abbreviate_downloads, environment_label
from src.web import BUNDLE_REF, MODRINTH_REF, WebRoute, get_web_actor, render, sanitize_detail, upstream_message

logger = logging.getLogger("modpack.web")
router = APIRouter(route_class=WebRoute, default_response_class=HTMLResponse)

# What the person reads when Modrinth did not answer in time (the fill's deadline).
MODRINTH_TIMEOUT = "Modrinth no respondió a tiempo; intenta de nuevo."


def _project_ref(project_id: str) -> str:
    """The id if it is a Modrinth id or slug that may go unescaped in a path; else 404."""
    if not re.fullmatch(MODRINTH_REF, project_id) or len(project_id) > 200:
        raise HTTP_404_NOT_FOUND("Eso no es un id de Modrinth")
    return project_id


def _pack_ref(pack: Optional[str]) -> Optional[str]:
    """The bundle id of the pack the card was opened from; anything else is ignored, never an error."""
    return pack if pack and re.fullmatch(BUNDLE_REF, pack) else None


def _failure(error: Exception) -> tuple[str, Optional[str]]:
    """The message and detail of Modrinth failing, as WebRoute words an UpstreamError."""
    if isinstance(error, UpstreamError):
        detail = f"{error.service} · HTTP {error.upstream_status} · {sanitize_detail(error.raw)}"
        return upstream_message(error.service, error.upstream_status), detail
    return MODRINTH_TIMEOUT, None


def _helpers() -> dict[str, Any]:
    return {"icon": _icon, "https_url": _https_url, "side_label": environment_label,
            "downloads": abbreviate_downloads}


@router.get("/mods/{project_id}")
@inject
async def mod_page(
        project_id: str,
        request: Request,
        pack: Optional[str] = None,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    """The mod's page: who it is, where it is used and its versions from the catalog. Modrinth not
    answering (only asked when the catalog lacks the project) is a notice with "Reintentar", never a
    500; a project Modrinth does not know is a 404. `pack` is the bundle the link came from: the card
    marks its Minecraft version and loader; an invalid or unknown one is ignored."""
    project_id = _project_ref(project_id)
    pack = _pack_ref(pack)
    try:
        page = await modpackService.mod_page(project_id, pack=pack)
    except (UpstreamError, TimeoutError) as error:
        logger.warning("mod page of %s: Modrinth failed", project_id, exc_info=True)
        message, detail = _failure(error)
        response = render(request, "mods/page.html", actor, page=None, project_id=project_id, pack=pack,
                          failure=message, detail=detail)
        response.status_code = 504 if isinstance(error, TimeoutError) else 502
        return response
    return render(request, "mods/page.html", actor, page=page, project_id=page.project.project_id,
                  pack=page.pack.bundle_id if page.pack else None, failure=None, detail=None, **_helpers())


@router.get("/mods/{project_id}/panel")
@inject
async def mod_panel(
        project_id: str,
        request: Request,
        desde: Optional[str] = None,
        pack: Optional[str] = None,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    """The mod's card for the side panel, without the versions table. `desde` is the mod the panel
    showed before (one level of history): it gets a way back; an invalid one is ignored. `pack` as
    for the page, and the card's links keep it."""
    project_id = _project_ref(project_id)
    back = desde if desde and re.fullmatch(MODRINTH_REF, desde) and len(desde) <= 200 and desde != project_id else None
    pack = _pack_ref(pack)
    try:
        page = await modpackService.mod_page(project_id, named=[back] if back else [], pack=pack)
    except (UpstreamError, TimeoutError) as error:
        logger.warning("mod panel of %s: Modrinth failed", project_id, exc_info=True)
        message, detail = _failure(error)
        return render(request, "mods/_panel.html", actor, page=None, project_id=project_id, back=back, pack=pack,
                      failure=message, detail=detail)
    return render(request, "mods/_panel.html", actor, page=page, project_id=page.project.project_id, back=back,
                  pack=page.pack.bundle_id if page.pack else None, failure=None, detail=None, **_helpers())


@router.get("/mods/{project_id}/versiones")
@inject
async def mod_versions(
        project_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ) -> Response:
    """Every version of the mod, asked of Modrinth now: it replaces the catalog's table. Modrinth
    failing is said in the table's place, with "Reintentar"."""
    project_id = _project_ref(project_id)
    try:
        versions = await modpackService.mod_versions_all(project_id)
    except (UpstreamError, TimeoutError) as error:
        logger.warning("all versions of %s: Modrinth failed", project_id, exc_info=True)
        message, detail = _failure(error)
        return render(request, "mods/_versions.html", actor, project_id=project_id, versions=[], partial=False,
                      failure=message, detail=detail)
    return render(request, "mods/_versions.html", actor, project_id=project_id, versions=versions, partial=False,
                  failure=None, detail=None)
