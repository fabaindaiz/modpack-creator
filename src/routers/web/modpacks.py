import logging
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urlsplit
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from src.dependences import Dependences, ModpackService, ModrinthService
from src.model.modpack import ModpackBundle, ModpackBundleCreate, ProjectOrigin
from src.model.modrinth.project import ModrinthEnvironment
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from src.web import WebRoute, get_web_actor, render

logger = logging.getLogger("modpack.web")
router = APIRouter(route_class=WebRoute, default_response_class=HTMLResponse)

# A Modrinth id or slug: what a path or an attribute may carry unescaped.
MODRINTH_REF = r"^[A-Za-z0-9_\-]+$"
SEARCH_LIMIT = 10
SEARCH_TEXT_MAX = 100
# The only host the CSP lets an <img> load from besides ourselves (web_security.CSP).
ICON_HOST = "cdn.modrinth.com"
MRPACK_TYPE = "application/x-modrinth-modpack+zip"

@dataclass(frozen=True)
class SearchHit:
    """A Modrinth search hit as the panel shows it. `icon` is None unless the CSP lets it load."""
    project_id: str
    title: str
    description: str
    author: str
    project_type: str
    downloads: str
    side: Optional[str]
    icon: Optional[str]
    added: bool

def _icon(url: Any) -> Optional[str]:
    """The icon URL if it is on Modrinth's CDN, over https, else None (a placeholder is shown)."""
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname == ICON_HOST and parts.port is None and not parts.username:
        return url
    return None

# What each Modrinth environment value means for the player, as the sides it runs on (openapi v2.7.0,
# EnvironmentEnum). An "optional" side still names the side the project is for; "singleplayer_only" runs
# in the client. `client_or_server*` work on either, so the project supports both. `unknown` is absent.
_CLIENT, _SERVER = "client", "server"
_ENVIRONMENT_SIDES: dict[str, frozenset[str]] = {
    ModrinthEnvironment.client_and_server.value: frozenset({_CLIENT, _SERVER}),
    ModrinthEnvironment.client_only.value: frozenset({_CLIENT}),
    ModrinthEnvironment.client_only_server_optional.value: frozenset({_CLIENT}),
    ModrinthEnvironment.singleplayer_only.value: frozenset({_CLIENT}),
    ModrinthEnvironment.server_only.value: frozenset({_SERVER}),
    ModrinthEnvironment.server_only_client_optional.value: frozenset({_SERVER}),
    ModrinthEnvironment.dedicated_server_only.value: frozenset({_SERVER}),
    ModrinthEnvironment.client_or_server.value: frozenset({_CLIENT, _SERVER}),
    ModrinthEnvironment.client_or_server_prefers_both.value: frozenset({_CLIENT, _SERVER}),
}

def environment_label(environment: Any) -> Optional[str]:
    """"cliente", "servidor" or "cliente y servidor" from a search hit's environment list. An empty
    list, a value we do not know or anything not a list gives None: absence is never "both"."""
    if not isinstance(environment, list):
        return None
    sides: set[str] = set()
    for value in environment:
        if isinstance(value, str):
            sides |= _ENVIRONMENT_SIDES.get(value, frozenset())
    if sides == {_CLIENT, _SERVER}:
        return "cliente y servidor"
    if sides:
        return "cliente" if _CLIENT in sides else "servidor"
    return None

def abbreviate_downloads(count: int) -> str:
    """1234 → "1,2 k", 34000 → "34 k", 1234567 → "1,2 M"; under 1000 as is. Truncates, never rounds up."""
    if count < 1000:
        return str(max(count, 0))
    unit, divisor = ("k", 1000) if count < 1_000_000 else ("M", 1_000_000)
    tenths = count * 10 // divisor
    if tenths >= 100:
        return f"{tenths // 10} {unit}"
    whole, tenth = divmod(tenths, 10)
    return f"{whole} {unit}" if tenth == 0 else f"{whole},{tenth} {unit}"

def _hit(raw: dict[str, Any], owned: set[str]) -> Optional[SearchHit]:
    project_id = raw.get("project_id")
    if not isinstance(project_id, str):
        return None
    downloads = raw.get("downloads")
    return SearchHit(
        project_id=project_id, title=str(raw.get("title") or raw.get("slug") or project_id),
        description=str(raw.get("description") or ""), author=raw["author"] if isinstance(raw.get("author"), str) else "",
        project_type=str(raw.get("project_type") or ""), downloads=abbreviate_downloads(downloads if isinstance(downloads, int) and not isinstance(downloads, bool) else 0),
        side=environment_label(raw.get("environment")), icon=_icon(raw.get("icon_url")), added=project_id in owned)

async def _titles(modrinthService: ModrinthService, project_ids: list[str]) -> dict[str, str]:
    """Project id → title, for what the catalog knows; an id it does not know shows as itself."""
    found = await modrinthService.projects_by_id(sorted(set(project_ids)))
    return {project_id: found[project_id].title if project_id in found else project_id for project_id in project_ids}

async def _projects(request: Request, actor: Actor, modrinthService: ModrinthService, bundle_id: str,
                    projects: list[Any]) -> Response:
    titles = await _titles(modrinthService, [p.project_id for p in projects])
    return render(request, "modpacks/_projects.html", actor, bundle_id=bundle_id, projects=projects, titles=titles)

async def _lock(request: Request, actor: Actor, modpackService: ModpackService, modrinthService: ModrinthService,
                bundle_id: str) -> Response:
    lock = await modpackService.resolve(bundle_id)
    titles = await _titles(modrinthService, [e.project_id for e in lock.entries]
                           + [p.project_id for p in lock.problems if p.project_id]
                           + [s.project_id for s in lock.suggestions])
    return render(request, "modpacks/_lock.html", actor, bundle_id=bundle_id, lock=lock, titles=titles)

async def _builds(request: Request, actor: Actor, modpackService: ModpackService, bundle_id: str) -> Response:
    bundle = await modpackService.bundle_get(bundle_id)
    builds = await modpackService.build_find(bundle_id)
    return render(request, "modpacks/_builds.html", actor, bundle_id=bundle_id, bundle=bundle, builds=builds)

async def _replacements(request: Request, actor: Actor, modpackService: ModpackService,
                        modrinthService: ModrinthService, fragment: bool) -> Response:
    rules = await modpackService.replacement_find()
    titles = await _titles(modrinthService, [r.project_id for r in rules] + [r.replacement for r in rules if r.replacement])
    name = "modpacks/_replacements.html" if fragment else "modpacks/replacements.html"
    return render(request, name, actor, rules=rules, titles=titles, nav_active="modpacks")

@router.get("/modpacks")
@inject
async def modpack_list(
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    """Every bundle, and the form to create one."""
    bundles = await modpackService.bundle_find()
    return render(request, "modpacks/list.html", actor, bundles=bundles, loaders=[l.value for l in MinecraftLoaders])

@router.post("/modpacks")
@inject
async def modpack_create(
        name: Annotated[str, Form(min_length=1, max_length=120)],
        version: Annotated[str, Form(min_length=1, max_length=40)],
        loader: Annotated[MinecraftLoaders, Form()],
        description: Annotated[str, Form(max_length=2000)] = "",
        loader_version: Annotated[str, Form(max_length=40)] = "",
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    """Create a bundle from the form and go to its page."""
    data = ModpackBundleCreate(name=name.strip(), description=description.strip(), version=version.strip(),
                               loader=loader, loader_version=loader_version.strip() or None)
    created = await modpackService.bundle_create(bundle=ModpackBundle(**data.model_dump()), actor=actor)
    return RedirectResponse(f"/modpacks/{created.id}", status_code=303)

@router.get("/modpacks/{bundle_id}")
@inject
async def modpack_detail(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """A bundle: its projects by origin, the search to add more, the lock and its builds."""
    bundle = await modpackService.bundle_get(bundle_id)
    builds = await modpackService.build_find(bundle_id)
    titles = await _titles(modrinthService, [p.project_id for p in bundle.projects])
    return render(request, "modpacks/detail.html", actor, bundle=bundle, bundle_id=bundle_id, projects=bundle.projects,
                  titles=titles, builds=builds, lock=None, nav_active="modpacks")

@router.get("/modpacks/{bundle_id}/search")
@inject
async def modpack_search(
        bundle_id: str,
        request: Request,
        q: Annotated[str, Query(max_length=SEARCH_TEXT_MAX)] = "",
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """Live search on Modrinth, for the bundle's game version and loader. Whatever goes wrong
    on Modrinth's side is an answer in the fragment, never a 500."""
    bundle = await modpackService.bundle_get(bundle_id)
    text = q.strip()
    if not text:
        return render(request, "modpacks/_search.html", actor, bundle_id=bundle_id, hits=[], failed=False, searched=False)
    try:
        raw = await modrinthService.search(text, bundle.version, str(bundle.loader.value), SEARCH_LIMIT)
    except Exception:
        logger.warning("Modrinth search failed for bundle %s", bundle_id, exc_info=True)
        return render(request, "modpacks/_search.html", actor, bundle_id=bundle_id, hits=[], failed=True, searched=True)
    owned = {p.project_id for p in bundle.projects}
    hits = [hit for hit in (_hit(item, owned) for item in raw if isinstance(item, dict)) if hit is not None]
    return render(request, "modpacks/_search.html", actor, bundle_id=bundle_id, hits=hits, failed=False, searched=True)

@router.post("/modpacks/{bundle_id}/projects")
@inject
async def project_add(
        bundle_id: str,
        request: Request,
        ref: Annotated[str, Form(min_length=1, max_length=200, pattern=MODRINTH_REF)],
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """Add a Modrinth project (slug or id) as an addition of the admin."""
    projects = await modpackService.project_add(bundle_id, [ref.strip()], ProjectOrigin.admin, actor)
    return await _projects(request, actor, modrinthService, bundle_id, projects)

@router.delete("/modpacks/{bundle_id}/projects/{project_id}")
@inject
async def project_remove(
        bundle_id: str,
        project_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    projects = await modpackService.project_remove(bundle_id, project_id, actor)
    return await _projects(request, actor, modrinthService, bundle_id, projects)

@router.post("/modpacks/{bundle_id}/resolve")
@inject
async def modpack_resolve(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """Resolve the bundle now and show the lock; nothing is stored (a build freezes it)."""
    return await _lock(request, actor, modpackService, modrinthService, bundle_id)

@router.post("/modpacks/{bundle_id}/decisions")
@inject
async def modpack_decide(
        bundle_id: str,
        request: Request,
        project_id: Annotated[str, Form(min_length=1, max_length=200, pattern=MODRINTH_REF)],
        server: Annotated[Literal["yes", "no"], Form()],
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """Decide whether a mod goes on the server: one key, written atomically by the service."""
    await modpackService.decision_set(bundle_id, project_id, server == "yes", actor)
    return await _lock(request, actor, modpackService, modrinthService, bundle_id)

@router.post("/modpacks/{bundle_id}/builds")
@inject
async def build_create(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    """Freeze the lock as the next build; a 409 (undecided mods, problems) is shown as a notice."""
    await modpackService.build_create(bundle_id, actor)
    return await _builds(request, actor, modpackService, bundle_id)

@router.post("/modpacks/{bundle_id}/builds/{number}/recommend")
@inject
async def build_recommend(
        bundle_id: str,
        number: int,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    await modpackService.build_recommend(bundle_id, number, actor)
    return await _builds(request, actor, modpackService, bundle_id)

@router.get("/modpacks/{bundle_id}/builds/{number}/mrpack")
@inject
async def build_mrpack(
        bundle_id: str,
        number: int,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    filename, data = await modpackService.build_mrpack(bundle_id, number)
    return Response(content=data, media_type=MRPACK_TYPE,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})

@router.get("/replacements")
@inject
async def replacement_list(
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """The global replacement rules, and the form to set one."""
    return await _replacements(request, actor, modpackService, modrinthService, fragment=False)

@router.post("/replacements")
@inject
async def replacement_set(
        request: Request,
        project_id: Annotated[str, Form(min_length=1, max_length=200, pattern=MODRINTH_REF)],
        replacement: Annotated[str, Form(max_length=200, pattern=r"^\s*[A-Za-z0-9_\-]*\s*$")] = "",
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """Set a rule; an empty replacement means the mod is dropped with nothing in its place."""
    await modpackService.replacement_set(project_id.strip(), replacement.strip() or None, actor)
    return await _replacements(request, actor, modpackService, modrinthService, fragment=True)

@router.delete("/replacements/{project_id}")
@inject
async def replacement_delete(
        project_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    await modpackService.replacement_delete(project_id, actor)
    return await _replacements(request, actor, modpackService, modrinthService, fragment=True)
