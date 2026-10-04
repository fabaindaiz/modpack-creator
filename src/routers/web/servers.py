import logging
from typing import Annotated, Any, Literal
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from src.dependences import Dependences, JobService, ModpackService, ModrinthService, PterodactylService, UpdateService
from src.exceptions import HTTP_403_FORBIDDEN, HTTP_409_CONFLICT, HTTP_422_UNPROCESSABLE_ENTITY
from src.library.scan import Comparison
from src.model.jobs import Job
from src.model.pterodactyl import PterodactylServer
from src.model.users import Actor
from src.web import WebRoute, get_web_actor, render

logger = logging.getLogger("modpack.web")
router = APIRouter(route_class=WebRoute, default_response_class=HTMLResponse)

# A Modrinth id or slug: what an attribute may carry.
MODRINTH_REF = r"^[A-Za-z0-9_\-]+$"
CONFIRM_MESSAGE = "Escribe el nombre exacto del servidor"
RELINKED_MESSAGE = "El servidor cambió de modpack; vuelve a cargar la página"
STALE_B_MESSAGE = ("La reversión con A ya terminó; vuelve a cargar la página para confirmar B con el nombre "
                   "del servidor.")

def _back(request: Request, server_id: str) -> Response:
    """Go to the server's page: htmx cannot follow a redirect to a page, it is told where to go."""
    url = f"/servers/{server_id}"
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)

def _job_row(request: Request, actor: Actor, job: Job) -> Response:
    return render(request, "activity/_job_row.html", actor, job=job)

async def _titles(modrinthService: ModrinthService, project_ids: list[str]) -> dict[str, str]:
    """Project id → title, for what the catalog knows; an id it does not know (or every id, when
    Modrinth fails) shows as itself."""
    try:
        found = await modrinthService.projects_by_id(sorted(set(project_ids)))
    except Exception:
        # The page is about the server: titles are a nicety, and an import may already be applied.
        logger.warning("Modrinth title lookup failed", exc_info=True)
        found = {}
    return {project_id: found[project_id].title if project_id in found else project_id for project_id in project_ids}

async def preview_context(modpackService: ModpackService, modrinthService: ModrinthService, server_id: str,
                          build_number: int) -> dict[str, Any]:
    """What updating the server to a build would change, for a template: `preview` and `titles`, or
    `preview_note` when it cannot be shown (no scan, no bundle, no such build). Modrinth failing is
    not an error: the preview marks the changelogs it could not read."""
    try:
        preview = await modpackService.server_change_preview(server_id, build_number)
    except HTTPException as error:
        if error.status_code not in (404, 409):
            raise
        return {"preview_note": str(error.detail)}
    changes = preview.changes
    titles = await _titles(modrinthService, [c.project_id for c in changes.added + changes.removed + changes.changed])
    return {"preview": preview, "titles": titles}

def _comparison_ids(comparison: Comparison) -> list[str]:
    return ([f.project_id for f in comparison.matching if f.project_id]
            + [f.project_id for f, _ in comparison.different_version if f.project_id]
            + [e.project_id for _, e in comparison.different_version]
            + [e.project_id for e in comparison.missing]
            + [f.project_id for f in comparison.extra if f.project_id])

@router.get("/servers")
@inject
async def server_list(
        request: Request,
        actor: Actor = Depends(get_web_actor),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    """Our servers, and the ones in the Pterodactyl panel that are not here yet."""
    servers = await pterodactylService.server_find()
    names = {str(b.id): b.name for b in await modpackService.bundle_find()}
    panel_failed = False
    try:
        known = {s.uuid for s in servers}
        additions = [s for s in await pterodactylService.server_external() if s.uuid not in known]
    except Exception:
        logger.warning("The panel's server list failed", exc_info=True)
        additions, panel_failed = [], True
    return render(request, "servers/list.html", actor, servers=servers, additions=additions,
                  panel_failed=panel_failed, bundle_names=names, nav_active="servers")

@router.post("/servers")
@inject
async def server_create(
        uuid: Annotated[str, Form(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9\-]+$")],
        actor: Actor = Depends(get_web_actor),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        ):
    """Bring a server of the panel in, and go to its page."""
    created = await pterodactylService.server_create(uuid.strip(), actor)
    return RedirectResponse(f"/servers/{created.id}", status_code=303)

@router.get("/servers/{server_id}")
@inject
async def server_detail(
        server_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        ):
    """A server: its bundle, the last scan, how it compares with its build, and what can be done."""
    server = await pterodactylService.server_get(server_id)
    bundles = await modpackService.bundle_find()
    bundle = next((b for b in bundles if str(b.id) == server.bundle_id), None)
    scan = None
    try:
        scan = await pterodactylService.scan_latest(server_id)
    except HTTPException as error:
        if error.status_code != 404:
            raise
    comparison, compare_note = None, None
    if server.bundle_id is None:
        compare_note = "El servidor no tiene modpack: vincúlalo para compararlo con un build."
    elif bundle is not None and bundle.latest_build is None and bundle.recommended_build is None:
        compare_note = "Este modpack todavía no tiene builds: crea uno para comparar."
    elif scan is None:
        compare_note = "Todavía no hay escaneo para comparar."
    else:
        try:
            comparison = await pterodactylService.compare(server_id)
        except HTTPException as error:
            if error.status_code not in (404, 409):
                raise
            compare_note = str(error.detail)
    titles = await _titles(modrinthService, _comparison_ids(comparison) if comparison else [])
    active = await jobService.active_for_server(server_id)
    rollback = await jobService.rollback_state(server_id)
    build_number = (bundle.recommended_build or bundle.latest_build) if bundle else None
    builds = sorted(await modpackService.build_find(str(bundle.id)), key=lambda b: b.number, reverse=True) \
        if bundle is not None else []
    # The direct update dialog opens on the default build with its changes: only an operator can open it.
    preview = await preview_context(modpackService, modrinthService, server_id, build_number) \
        if actor.is_operator and bundle is not None and build_number is not None and builds else {}
    titles = {**titles, **preview.pop("titles", {})}
    return render(request, "servers/detail.html", actor, server=server, server_id=server_id, bundles=bundles,
                  bundle=bundle, scan=scan, comparison=comparison, compare_note=compare_note, titles=titles,
                  active_job=active, rollback=rollback, build_number=build_number, builds=builds, nav_active="servers",
                  **preview)

@router.post("/servers/{server_id}/refresh")
@inject
async def server_refresh(
        server_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        ):
    """Read the server again from the panel; our bundle link stays."""
    await pterodactylService.server_update(server_id, actor)
    return _back(request, server_id)

@router.post("/servers/{server_id}/scan")
@inject
async def server_scan(
        server_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        ):
    """Queue a scan; the answer is its row, which polls until it finishes. A 409 (the server has
    an active job) is shown as a notice."""
    job = await jobService.enqueue_scan(server_id, actor)
    return _job_row(request, actor, job)

@router.put("/servers/{server_id}/bundle")
@inject
async def server_bundle(
        server_id: str,
        request: Request,
        bundle_id: Annotated[str, Form(max_length=24, pattern=r"^([0-9a-fA-F]{24})?$")] = "",
        actor: Actor = Depends(get_web_actor),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        ):
    """Link the server to the bundle it runs, or unlink it (empty). Operators only."""
    await pterodactylService.server_link_bundle(server_id, bundle_id or None, actor)
    return _back(request, server_id)

@router.post("/servers/{server_id}/import")
@inject
async def server_import(
        server_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """Bring the server's unexplained jars into its bundle; whatever needs a person's decision
    is listed with its candidates. Operators only."""
    result = await pterodactylService.import_server(server_id, actor)
    ids = list(result.added) + [c.project_id for item in result.candidates for c in item.candidates]
    titles = await _titles(modrinthService, ids)
    return render(request, "servers/_import.html", actor, server_id=server_id, result=result, titles=titles)

@router.post("/servers/{server_id}/import/confirm")
@inject
async def server_import_confirm(
        server_id: str,
        request: Request,
        path: Annotated[str, Form(min_length=1, max_length=500)],
        project_id: Annotated[str, Form(min_length=1, max_length=200, pattern=MODRINTH_REF)],
        actor: Actor = Depends(get_web_actor),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        ):
    """The admin says a jar is this Modrinth project."""
    await pterodactylService.import_confirm(server_id, path, project_id, actor)
    return render(request, "servers/_import_done.html", actor, path=path, what="project", project_id=project_id)

@router.post("/servers/{server_id}/import/local")
@inject
async def server_import_local(
        server_id: str,
        request: Request,
        path: Annotated[str, Form(min_length=1, max_length=500)],
        actor: Actor = Depends(get_web_actor),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        ):
    """The admin keeps a jar as a local file of the bundle."""
    await pterodactylService.import_local(server_id, path, actor)
    return render(request, "servers/_import_done.html", actor, path=path, what="local", project_id="")

@router.get("/servers/{server_id}/update/preview")
@inject
async def server_update_changes(
        server_id: str,
        request: Request,
        build: Annotated[int, Query(ge=1)],
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """What updating the server to `build` would change, with the changelogs of what arrives: what
    the direct dialog loads when the build is chosen. A read, so an editor may load it; a server
    with no scan is a 409 notice."""
    preview = await modpackService.server_change_preview(server_id, build)
    changes = preview.changes
    titles = await _titles(modrinthService, [c.project_id for c in changes.added + changes.removed + changes.changed])
    return render(request, "servers/_preview_body.html", actor, preview=preview, titles=titles)

@router.post("/servers/{server_id}/update/preview")
@inject
async def server_update_preview(
        server_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        updateService: UpdateService = Depends(Provide[Dependences.updateService]),
        ):
    """A dry run of the update: it reads the server and writes nothing. The answer is the job's
    row, which polls and shows the plan when it finishes."""
    job = await updateService.enqueue_update(server_id, actor, dry_run=True)
    return _job_row(request, actor, job)

@router.post("/servers/{server_id}/update")
@inject
async def server_update_run(
        server_id: str,
        request: Request,
        build: Annotated[int, Form(ge=1)],
        confirm: Annotated[str, Form(max_length=200)] = "",
        bundle_id: Annotated[str, Form(max_length=24)] = "",
        actor: Actor = Depends(get_web_actor),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        updateService: UpdateService = Depends(Provide[Dependences.updateService]),
        ):
    """The real update, on a game server, to a fixed build, reached two ways (d-7985ee-aca3e8):
    the direct dialog of the server's page, with the build chosen there, or the result of a
    finished dry run, with the build it simulated (the recommended one may have changed since).
    Both send the bundle they were planned for: if the server was linked to another one since, it
    is a 409 before the service is called. The person must type the server's exact name: compared
    here, case-sensitive, nothing partial, and without calling the service when it differs. The
    service still enforces the operator role; an editor is refused before anything is compared,
    so the page does not tell them anything about it."""
    if not actor.is_operator:
        raise HTTP_403_FORBIDDEN("This action requires the operator role")
    server: PterodactylServer = await pterodactylService.server_get(server_id)
    if not bundle_id or bundle_id != server.bundle_id:
        raise HTTP_409_CONFLICT(RELINKED_MESSAGE)
    if confirm.strip() != server.name:
        raise HTTP_422_UNPROCESSABLE_ENTITY(CONFIRM_MESSAGE)
    job = await updateService.enqueue_update(server_id, actor, build=build, dry_run=False)
    return _job_row(request, actor, job)

@router.post("/servers/{server_id}/rollback")
@inject
async def server_rollback(
        server_id: str,
        request: Request,
        update_id: Annotated[str, Form(min_length=1, max_length=64)],
        mode: Annotated[Literal["A", "B"], Form()],
        confirm: Annotated[str, Form(max_length=200)] = "",
        actor: Actor = Depends(get_web_actor),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        updateService: UpdateService = Depends(Provide[Dependences.updateService]),
        ):
    """Undo a failed update: A brings the old jars back, B restores the backup. Operators only:
    an editor is refused before anything else. After a done A only B is left, and it loses what
    was played since the backup: then B needs the server's exact name, compared here as for the
    real update, without calling the service when it differs (d-7985ee-5445a0). A request for B
    with no name field at all comes from a page drawn before A finished: it is told to reload. A
    request for A goes to the service, which says that only B is left. The service checks every
    rule again."""
    if not actor.is_operator:
        raise HTTP_403_FORBIDDEN("This action requires the operator role")
    state = await jobService.rollback_state(server_id)
    if mode == "B" and state is not None and state.reverted_with_a is not None:
        if "confirm" not in await request.form():  # FastAPI reads an empty field as absent: ask the form
            raise HTTP_422_UNPROCESSABLE_ENTITY(STALE_B_MESSAGE)
        server: PterodactylServer = await pterodactylService.server_get(server_id)
        if confirm.strip() != server.name:
            raise HTTP_422_UNPROCESSABLE_ENTITY(CONFIRM_MESSAGE)
    job = await updateService.enqueue_rollback(server_id, update_id, mode, actor)
    return _job_row(request, actor, job)
