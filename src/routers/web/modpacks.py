import logging
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urlsplit
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from src.exceptions import HTTP_422_UNPROCESSABLE_ENTITY
from src.dependences import Dependences, ModpackService, ModrinthService
from src.model.modpack import ModpackBundle, ModpackBundleCreate, ModpackBundlePatch, ProjectOrigin
from src.model.modrinth.project import ModrinthEnvironment
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from src.web import WebRoute, get_web_actor, render

logger = logging.getLogger("modpack.web")
router = APIRouter(route_class=WebRoute, default_response_class=HTMLResponse)

# A Modrinth id or slug: what a path or an attribute may carry unescaped.
MODRINTH_REF = r"^[A-Za-z0-9_\-]+$"
# The prefix of a bulk-decision field: `decision.<project id>`.
DECISION_PREFIX = "decision."
# The channels a modpack may allow, most stable first (Modrinth's version types).
CHANNELS = ("release", "beta", "alpha")
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
_ENVIRONMENT_LABELS: dict[str, str] = {
    ModrinthEnvironment.client_and_server.value: "cliente y servidor",  # must be installed on both
    ModrinthEnvironment.client_or_server.value: "cliente o servidor",  # works on either side alone
    ModrinthEnvironment.client_or_server_prefers_both.value: "cliente o servidor",
    ModrinthEnvironment.client_only.value: "cliente",
    ModrinthEnvironment.client_only_server_optional.value: "cliente",
    ModrinthEnvironment.server_only.value: "servidor",
    ModrinthEnvironment.server_only_client_optional.value: "servidor",
    ModrinthEnvironment.dedicated_server_only.value: "servidor",
    ModrinthEnvironment.singleplayer_only.value: "un jugador",  # does not work in multiplayer
}
ENVIRONMENT_VARIES = "varía según la versión"

def environment_label(environment: Any) -> Optional[str]:
    """The label of a search hit's environment list (aggregated over the project's versions). Unknown
    values are ignored; one label stays, different labels are "varía según la versión", and none known
    (empty, only unknown, not a list) is None: absence is never "both"."""
    if not isinstance(environment, list):
        return None
    labels = {_ENVIRONMENT_LABELS[v] for v in environment if isinstance(v, str) and v in _ENVIRONMENT_LABELS}
    if not labels:
        return None
    return labels.pop() if len(labels) == 1 else ENVIRONMENT_VARIES

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

def _int(value: Any) -> int:
    """The value if it is an integer (a bool is not), else 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0

def _hit(raw: dict[str, Any], owned: set[str]) -> Optional[SearchHit]:
    project_id = raw.get("project_id")
    if not isinstance(project_id, str):
        return None
    return SearchHit(
        project_id=project_id, title=str(raw.get("title") or raw.get("slug") or project_id),
        description=str(raw.get("description") or ""), author=str(raw.get("author") or ""),
        project_type=str(raw.get("project_type") or ""), downloads=abbreviate_downloads(_int(raw.get("downloads"))),
        side=environment_label(raw.get("environment")), icon=_icon(raw.get("icon_url")), added=project_id in owned)

async def _titles(modrinthService: ModrinthService, project_ids: list[str]) -> dict[str, str]:
    """Project id → title, for what the catalog knows; an id it does not know shows as itself."""
    found = await modrinthService.projects_by_id(sorted(set(project_ids)))
    return {project_id: found[project_id].title if project_id in found else project_id for project_id in project_ids}

async def _projects(request: Request, actor: Actor, modrinthService: ModrinthService, bundle_id: str,
                    projects: list[Any]) -> Response:
    titles = await _titles(modrinthService, [p.project_id for p in projects])
    return render(request, "modpacks/_projects.html", actor, bundle_id=bundle_id, projects=projects, titles=titles)

def human_size(size: int) -> str:
    """1536 → "1,5 KiB", 3 MiB → "3,0 MiB"; under 1 KiB in bytes."""
    if size < 1024:
        return f"{max(size, 0)} B"
    unit, divisor = ("KiB", 1024) if size < 1024 ** 2 else ("MiB", 1024 ** 2)
    return f"{size / divisor:.1f}".replace(".", ",") + f" {unit}"

def _channels(request: Request, actor: Actor, bundle: ModpackBundle, bundle_id: str) -> Response:
    return render(request, "modpacks/_channels.html", actor, bundle_id=bundle_id, bundle=bundle, channel_names=CHANNELS)

async def _pins(request: Request, actor: Actor, modrinthService: ModrinthService, bundle: ModpackBundle,
                bundle_id: str) -> Response:
    titles = await _titles(modrinthService, [p.project_id for p in bundle.projects] + list(bundle.pins))
    return render(request, "modpacks/_pins.html", actor, bundle_id=bundle_id, bundle=bundle, titles=titles)

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
    titles = await _titles(modrinthService, [p.project_id for p in bundle.projects] + list(bundle.pins))
    return render(request, "modpacks/detail.html", actor, bundle=bundle, bundle_id=bundle_id, projects=bundle.projects,
                  titles=titles, builds=builds, lock=None, channel_names=CHANNELS, human_size=human_size,
                  nav_active="modpacks")

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

@router.post("/modpacks/{bundle_id}/channels")
@inject
async def modpack_channels(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    """Save the channels the modpack allows (the checked boxes, most stable first). At least one."""
    chosen = {str(v) for k, v in (await request.form()).multi_items() if k == "channel"}
    if not chosen:
        raise HTTP_422_UNPROCESSABLE_ENTITY("Elige al menos un canal")
    if not chosen <= set(CHANNELS):
        raise HTTP_422_UNPROCESSABLE_ENTITY("Los canales son release, beta y alpha")
    updated = await modpackService.bundle_update(
        bundle_id, ModpackBundlePatch(channels=[c for c in CHANNELS if c in chosen]), actor)
    return _channels(request, actor, updated, bundle_id)

@router.post("/modpacks/{bundle_id}/pins")
@inject
async def pin_add(
        bundle_id: str,
        request: Request,
        project_id: Annotated[str, Form(min_length=1, max_length=200, pattern=MODRINTH_REF)],
        version_id: Annotated[str, Form(min_length=1, max_length=200, pattern=MODRINTH_REF)],
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """Pin a mod to a version of that mod: one key, written atomically by the service."""
    updated = await modpackService.pin_set(bundle_id, project_id, version_id, actor)
    return await _pins(request, actor, modrinthService, updated, bundle_id)

@router.delete("/modpacks/{bundle_id}/pins/{project_id}")
@inject
async def pin_remove(
        bundle_id: str,
        project_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    updated = await modpackService.pin_unset(bundle_id, project_id, actor)
    return await _pins(request, actor, modrinthService, updated, bundle_id)

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

@router.post("/modpacks/{bundle_id}/decisions/bulk")
@inject
async def modpack_decide_bulk(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """Save every decision of the form at once. Fields are `decision.<id>` = `si` | `no`; a mod left
    undecided sends an empty value and is skipped. Any other value or id refuses the whole form."""
    decisions: dict[str, bool] = {}
    for field, value in (await request.form()).items():
        if not field.startswith(DECISION_PREFIX):
            continue
        project_id = field[len(DECISION_PREFIX):]
        if not re.fullmatch(MODRINTH_REF, project_id) or len(project_id) > 200:
            raise HTTP_422_UNPROCESSABLE_ENTITY("Una decisión nombra un mod que no es un id de Modrinth")
        if value == "":
            continue
        if value not in ("si", "no"):
            raise HTTP_422_UNPROCESSABLE_ENTITY("Cada decisión es «sí» o «no»")
        decisions[project_id] = value == "si"
    if not decisions:
        raise HTTP_422_UNPROCESSABLE_ENTITY("Elige al menos una decisión")
    await modpackService.decisions_set(bundle_id, decisions, actor)
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

@router.get("/modpacks/{bundle_id}/builds/compare")
@inject
async def build_compare(
        bundle_id: str,
        request: Request,
        de: Annotated[int, Query(ge=1)],
        a: Annotated[int, Query(ge=1)],
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """What changes from build `de` to build `a`, with the changelogs of what arrives. A read:
    Modrinth failing shows as "changelog no disponible", never as an error."""
    bundle = await modpackService.bundle_get(bundle_id)
    preview = await modpackService.compare_builds(bundle_id, de, a)
    changes = preview.changes
    titles = await _titles(modrinthService, [c.project_id for c in changes.added + changes.removed + changes.changed])
    return render(request, "modpacks/compare.html", actor, bundle=bundle, bundle_id=bundle_id, preview=preview,
                  titles=titles, de=de, a=a, nav_active="modpacks")

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
