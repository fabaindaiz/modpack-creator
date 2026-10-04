import logging
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urlsplit
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Form, Query, Request
from starlette.datastructures import FormData
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from src.exceptions import BuildBlocked, HTTP_422_UNPROCESSABLE_ENTITY, UpstreamError
from src.library.resolution import Lock
from src.dependences import Dependences, ModpackService, ModrinthService, PterodactylService, UpdateCheckService
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

def _https_url(url: Any) -> Optional[str]:
    """A link from the catalog (source, issues) if it is https with a host, else None: never another scheme."""
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname else None

async def _titles(modrinthService: ModrinthService, project_ids: list[str]) -> dict[str, str]:
    """Project id → title, for what the catalog knows; an id it does not know shows as itself."""
    found = await modrinthService.projects_by_id(sorted(set(project_ids)))
    return {project_id: found[project_id].title if project_id in found else project_id for project_id in project_ids}

async def _mods_context(modpackService: ModpackService, updateCheckService: UpdateCheckService,
                        bundle_id: str) -> dict[str, Any]:
    """What _mods.html needs: the bundle and its mod list, with the latest update check (for the
    newer versions) and the titles of what the saved resolution names. Modrinth is asked at most
    once, inside mod_list, within one deadline."""
    bundle = await modpackService.bundle_get(bundle_id)
    mods = await modpackService.mod_list(bundle_id, await updateCheckService.latest(bundle_id))
    return {"bundle": bundle, "bundle_id": bundle_id, "mods": mods, "titles": mods.titles, "icon": _icon,
            "https_url": _https_url, "downloads": abbreviate_downloads}

async def _mods(request: Request, actor: Actor, modpackService: ModpackService, updateCheckService: UpdateCheckService,
                bundle_id: str, success: Optional[str] = None, info: Optional[str] = None,
                hit: Optional[str] = None) -> Response:
    """The #mods fragment; `success` or `info` and `hit` go after it, out of band (a notice, a search
    hit marked "Ya está")."""
    context = await _mods_context(modpackService, updateCheckService, bundle_id)
    return render(request, "modpacks/_mods.html", actor, success=success, info=info, hit=hit, **context)

def human_size(size: int) -> str:
    """1536 → "1,5 KiB", 3 MiB → "3,0 MiB"; under 1 KiB in bytes."""
    if size < 1024:
        return f"{max(size, 0)} B"
    unit, divisor = ("KiB", 1024) if size < 1024 ** 2 else ("MiB", 1024 ** 2)
    return f"{size / divisor:.1f}".replace(".", ",") + f" {unit}"

def _channels(request: Request, actor: Actor, bundle: ModpackBundle, bundle_id: str) -> Response:
    return render(request, "modpacks/_channels.html", actor, bundle_id=bundle_id, bundle=bundle, channel_names=CHANNELS)

def _has_pending(saved: Optional[tuple[Any, bool]]) -> bool:
    """Whether the current (not stale) saved resolution has mods left to decide."""
    return saved is not None and not saved[1] and bool(saved[0].lock.undecided)

async def _decided(request: Request, actor: Actor, modpackService: ModpackService,
                   updateCheckService: UpdateCheckService, bundle_id: str) -> Response:
    """#mods after saving decisions (and resolving again), with #builds redrawn out of band: its
    button says whether something is still pending, and "Crear build" is the highlighted next step
    when the current resolution has nothing left to decide and no problems (spec §2)."""
    context = await _mods_context(modpackService, updateCheckService, bundle_id)
    mods = context["mods"]
    saved = (mods.resolution, mods.stale) if mods.resolution else None
    pending = _has_pending(saved)
    lock = mods.resolution.lock if mods.resolution else None
    ready = lock is not None and not mods.stale and not pending and not lock.problems and lock.complete
    return render(request, "modpacks/_mods.html", actor, success="Decisiones guardadas", builds_oob=True,
                  builds=await modpackService.build_find(bundle_id), pending=pending, ready=ready, **context)

# What the decision dialog says when it is sent with every mod left "Sin decidir".
NOTHING_DECIDED = "Elige «En el servidor» o «Solo cliente» para al menos un mod."

def _blocked_reason(lock: Lock, titles: dict[str, str]) -> str:
    """Why a build was refused, from its fresh lock, naming mods by title (never by id): each
    problem, the mods left to decide, and a resolution that did not finish."""
    lines = [f"{titles.get(p.project_id, p.project_id)}: {p.detail}" if p.project_id else p.detail
             for p in lock.problems]
    if lock.undecided:
        lines.append("Falta decidir si van en el servidor: "
                     + ", ".join(titles.get(e.project_id, e.project_id) for e in lock.undecided))
    if not lock.complete:
        lines.append("La resolución no terminó: la cadena de dependencias es demasiado larga.")
    return "\n".join(["No se puede crear el build:", *lines])

async def _builds(request: Request, actor: Actor, modpackService: ModpackService, bundle_id: str,
                  success: Optional[str] = None, next_servers: Optional[list[Any]] = None) -> Response:
    bundle = await modpackService.bundle_get(bundle_id)
    builds = await modpackService.build_find(bundle_id)
    pending = _has_pending(await modpackService.resolution(bundle_id))
    return render(request, "modpacks/_builds.html", actor, bundle_id=bundle_id, bundle=bundle, builds=builds,
                  pending=pending, success=success, next_servers=next_servers)

async def _replacements(request: Request, actor: Actor, modpackService: ModpackService,
                        modrinthService: ModrinthService, fragment: bool) -> Response:
    rules = await modpackService.replacement_find()
    titles = await _titles(modrinthService, [r.project_id for r in rules] + [r.replacement for r in rules if r.replacement])
    name = "modpacks/_replacements.html" if fragment else "modpacks/replacements.html"
    return render(request, name, actor, rules=rules, titles=titles, nav_active="modpacks")

def _parse_decisions(form: FormData) -> dict[str, bool]:
    """The `decision.<id>` = `si` | `no` fields of a form as {project id: on the server}. An empty
    value is a mod left undecided and is skipped; any other value or id refuses the whole form."""
    decisions: dict[str, bool] = {}
    for field, value in form.items():
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
    return decisions

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
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        ):
    """A bundle: its mods as one list under the saved resolution, the search to add more, and on the
    side its builds, the servers linked to it, its channels and its local files."""
    context = await _mods_context(modpackService, updateCheckService, bundle_id)
    builds = await modpackService.build_find(bundle_id)
    linked = str(context["bundle"].id)
    servers = [s for s in await pterodactylService.server_find() if s.bundle_id == linked]
    saved = context["mods"].resolution
    return render(request, "modpacks/detail.html", actor, builds=builds, servers=servers, channel_names=CHANNELS,
                  pending=_has_pending((saved, context["mods"].stale) if saved else None),
                  human_size=human_size, nav_active="modpacks", **context)

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
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """Add a Modrinth project (slug or id) as an addition of the admin. Says what was added; when
    the click came from a search hit (its form's id), that hit is marked "Ya está" out of band."""
    ref = ref.strip()
    before = {p.project_id for p in (await modpackService.bundle_get(bundle_id)).projects}
    projects = await modpackService.project_add(bundle_id, [ref], ProjectOrigin.admin, actor)
    added = [p.project_id for p in projects if p.project_id not in before]
    from_hit = request.headers.get("HX-Trigger") == f"hit-add-{ref}" and any(p.project_id == ref for p in projects)
    context = await _mods_context(modpackService, updateCheckService, bundle_id)
    names = ", ".join(context["titles"].get(i, i) for i in added)
    return render(request, "modpacks/_mods.html", actor, success=f"Agregado: {names}" if names else None,
                  info=None if names else "Ese mod ya estaba en el modpack", hit=ref if from_hit else None, **context)

@router.delete("/modpacks/{bundle_id}/projects/{project_id}")
@inject
async def project_remove(
        bundle_id: str,
        project_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    await modpackService.project_remove(bundle_id, project_id, actor)
    return await _mods(request, actor, modpackService, updateCheckService, bundle_id)

@router.get("/modpacks/{bundle_id}/projects/{project_id}/versions")
@inject
async def project_versions(
        bundle_id: str,
        project_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    """The versions a mod can be pinned to, as the <select> its card loads when it opens. A read:
    Modrinth failing is an answer in the fragment, with a retry, never a 500."""
    if not re.fullmatch(MODRINTH_REF, project_id) or len(project_id) > 200:
        raise HTTP_422_UNPROCESSABLE_ENTITY("Eso no es un id de Modrinth")
    versions: list[Any] = []
    failed = False
    try:
        versions = await modpackService.project_versions(bundle_id, project_id)
    except (UpstreamError, TimeoutError):
        # Only Modrinth failing or not answering is "Modrinth no respondió"; anything else is ours.
        logger.warning("Modrinth versions failed for %s in bundle %s", project_id, bundle_id, exc_info=True)
        failed = True
    return render(request, "modpacks/_versions.html", actor, bundle_id=bundle_id, project_id=project_id,
                  versions=versions, failed=failed)

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
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """Pin a mod to a version of that mod: one key, written atomically by the service."""
    await modpackService.pin_set(bundle_id, project_id, version_id, actor)
    return await _mods(request, actor, modpackService, updateCheckService, bundle_id)

@router.delete("/modpacks/{bundle_id}/pins/{project_id}")
@inject
async def pin_remove(
        bundle_id: str,
        project_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    await modpackService.pin_unset(bundle_id, project_id, actor)
    return await _mods(request, actor, modpackService, updateCheckService, bundle_id)

@router.post("/modpacks/{bundle_id}/resolve")
@inject
async def modpack_resolve(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """Resolve the bundle now and show the mods under it. The resolution is kept only to show it:
    a build resolves again and freezes that lock."""
    await modpackService.resolve(bundle_id)
    return await _mods(request, actor, modpackService, updateCheckService, bundle_id)

@router.post("/modpacks/{bundle_id}/decisions")
@inject
async def modpack_decide(
        bundle_id: str,
        request: Request,
        project_id: Annotated[str, Form(min_length=1, max_length=200, pattern=MODRINTH_REF)],
        server: Annotated[Literal["yes", "no"], Form()],
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """Decide whether a mod goes on the server: one key, written atomically by the service. The
    resolution is run again so the mod leaves "Sin decidir"."""
    await modpackService.decision_set(bundle_id, project_id, server == "yes", actor)
    await modpackService.resolve(bundle_id)
    return await _decided(request, actor, modpackService, updateCheckService, bundle_id)

@router.post("/modpacks/{bundle_id}/decisions/bulk")
@inject
async def modpack_decide_bulk(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """Save every decision of the form at once, then resolve again (as for one decision). Fields are `decision.<id>` = `si` | `no`; a mod left
    undecided sends an empty value and is skipped. Any other value or id refuses the whole form."""
    decisions = _parse_decisions(await request.form())
    if not decisions:
        raise HTTP_422_UNPROCESSABLE_ENTITY("Elige al menos una decisión")
    await modpackService.decisions_set(bundle_id, decisions, actor)
    await modpackService.resolve(bundle_id)
    return await _decided(request, actor, modpackService, updateCheckService, bundle_id)

@router.post("/modpacks/{bundle_id}/builds")
@inject
async def build_create(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        ):
    """Freeze the lock as the next build. With `decision.<id>` fields (from the dialog) they are saved
    first. Blocked by undecided mods, or blocked again when sent from the dialog (its `dialog`
    field), it answers the dialog into #build-dialog, with the reason inside once the dialog was
    sent: the mods by title, or that none was decided. Blocked by problems from the button, or any
    other 409, is a notice."""
    form = await request.form()
    decisions = _parse_decisions(form)
    from_dialog = form.get("dialog") == "1"
    try:
        if decisions:
            await modpackService.decisions_set(bundle_id, decisions, actor)
        build = await modpackService.build_create(bundle_id, actor)
    except BuildBlocked as blocked:
        if not (blocked.only_undecided or decisions or from_dialog):
            raise
        undecided = blocked.lock.undecided if blocked.only_undecided else []
        named = [e.project_id for e in blocked.lock.undecided] + [p.project_id for p in blocked.lock.problems
                                                                  if p.project_id]
        titles = await _titles(modrinthService, named)
        reason = None
        if from_dialog and not decisions and blocked.only_undecided:
            reason = NOTHING_DECIDED
        elif from_dialog or decisions:
            reason = _blocked_reason(blocked.lock, titles)
        response = render(request, "modpacks/_decide_dialog.html", actor, bundle_id=bundle_id, undecided=undecided,
                          titles=titles, reason=reason)
        response.headers["HX-Retarget"] = "#build-dialog"
        response.headers["HX-Reswap"] = "innerHTML"
        return response
    linked = str((await modpackService.bundle_get(bundle_id)).id)
    servers = [s for s in await pterodactylService.server_find() if s.bundle_id == linked]
    return await _builds(request, actor, modpackService, bundle_id, success=f"Build {build.number} creado",
                         next_servers=servers)

def _builds_page(request: Request, bundle_id: str) -> Response:
    """Go to the builds of the modpack: htmx cannot follow a redirect to a page, it is told where to go."""
    url = f"/modpacks/{bundle_id}#builds-section"
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)

@router.get("/modpacks/{bundle_id}/actualizaciones")
@inject
async def update_page(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """The latest check of the modpack's recommended build: what has a newer version, with the
    changelogs. A read: Modrinth failing shows as "changelog no disponible", never as an error."""
    bundle = await modpackService.bundle_get(bundle_id)
    check = await updateCheckService.latest(bundle_id)
    counted = [u for u in check.updates if not u.pinned] if check else []
    pinned = [u for u in check.updates if u.pinned] if check else []
    preview = await modpackService.update_preview(counted) if counted else None
    return render(request, "modpacks/updates.html", actor, bundle=bundle, bundle_id=bundle_id, check=check,
                  preview=preview, pinned=pinned, titles={u.project_id: u.title for u in counted},
                  nav_active="modpacks")

@router.post("/modpacks/{bundle_id}/actualizaciones/buscar")
@inject
async def update_search(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """Queue a check now; the answer is its row, which polls until it finishes. A 409 (the modpack
    has an active job) is shown as a notice."""
    job = await updateCheckService.enqueue(bundle_id, actor)
    return render(request, "activity/_job_row.html", actor, job=job)

@router.post("/modpacks/{bundle_id}/actualizaciones/build")
@inject
async def update_build(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    """Create a build with the same call as the builds section: it resolves again, so the result may
    differ from the check (pins, replacements). It does not recommend it nor deploy it."""
    await modpackService.build_create(bundle_id, actor)
    return _builds_page(request, bundle_id)

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
