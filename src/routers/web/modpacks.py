import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional
from urllib.parse import parse_qs, urlsplit
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Form, Query, Request
from starlette.datastructures import FormData, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from src.exceptions import BuildBlocked, DowngradeRefused, HTTP_404_NOT_FOUND, HTTP_413_CONTENT_TOO_LARGE, HTTP_422_UNPROCESSABLE_ENTITY, QuotaExceeded, UpstreamError
from src.library.mod_filters import ModFilters, apply as filter_rows, counts as filter_counts
from src.library.onboarding import PackFacts, pack_steps, pending_count
from src.library.resolution import ADMIN, Lock
from src.dependences import Dependences, JobService, ModpackImportService, ModpackService, ModrinthService, PterodactylService, UpdateCheckService
from src.model.modpack import ModpackBundle, ModpackBundleCreate, ModpackBundlePatch, ProjectOrigin
from src.model.modrinth.project import ModrinthEnvironment
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from src.library.utils import as_utc, getenv
from src.library.cf_import import is_curseforge
from src.library.prism_import import is_prism
from src.library.upload_budget import UploadBudget, UploadBusy
from src.web import BUNDLE_REF, JOB_STARTED, MODRINTH_REF, WebRoute, get_web_actor, render

logger = logging.getLogger("modpack.web")
# The event a pack hears when its last job ended: it reloads.
JOB_FINISHED = "job-finished"
# And when that job was a build its check blocked: the build dialog opens.
BUILD_BLOCKED = "build-blocked"
BUILD_QUEUED = "Creando el build: revisando cambios en Modrinth"
# How long after it is made a build offers to simulate it on the linked servers.
FRESH_BUILD = timedelta(minutes=2)

router = APIRouter(route_class=WebRoute, default_response_class=HTMLResponse)

# The prefix of a bulk-decision field: `decision.<project id>`.
DECISION_PREFIX = "decision."
# The channels a modpack may allow, most stable first (Modrinth's version types).
CHANNELS = ("release", "beta", "alpha")
SEARCH_LIMIT = 10
SEARCH_TEXT_MAX = 100
# The only host the CSP lets an <img> load from besides ourselves (web_security.CSP).
ICON_HOST = "cdn.modrinth.com"
MRPACK_TYPE = "application/x-modrinth-modpack+zip"
# The views of a pack's Mods (spec §3): every mod, the ones to decide, the problems. Any other value
# is "todos": a stale or hand-written link still shows the pack.
VISTAS = ("todos", "decidir", "problemas", "sugerencias")
# Where a problem's action was clicked: the route applies it, resolves again and answers there (spec
# §1): "problemas" the Mods tab, "dialogo" the build dialog. Without it, the route answers as it
# always did.
Contexto = Literal["problemas", "dialogo"]

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

def _vista(value: str) -> str:
    return value if value in VISTAS else "todos"

def _vista_from(request: Request) -> str:
    """The tab the page is on (B1): the tabs push `?vista=` to the page's URL, and htmx names that URL
    in HX-Current-URL. Unknown or absent: "todos"."""
    query = urlsplit(request.headers.get("HX-Current-URL", "")).query
    return _vista(parse_qs(query).get("vista", ["todos"])[0])

_NAME_TOKEN = re.compile(r"[A-Za-z0-9_\-]+")

def humanize(detail: str, titles: dict[str, str]) -> str:
    """A problem's saved text with the projects it names by title (the ids `titles` knows) and the
    admin in words: what the panel shows when the structured fields do not say it (old resolutions,
    a dependency without versions)."""
    return _NAME_TOKEN.sub(lambda m: titles.get(m.group(0), "el admin" if m.group(0) == ADMIN else m.group(0)), detail)

async def _titles(modrinthService: ModrinthService, project_ids: list[str]) -> dict[str, str]:
    """Project id → title, from the catalog, filled first from Modrinth for what it lacks: one batch
    for every mod the render shows, within its deadline, so each has a card (spec §2, "ningún mod sin
    ficha"). An id still unknown shows as itself."""
    found = await modrinthService.projects_filled(sorted(set(project_ids))) if project_ids else {}
    return {project_id: found[project_id].title if project_id in found else project_id for project_id in project_ids}

def _filters_from(request: Request) -> ModFilters:
    """The mod list's filters (A6): a page or tab read (GET) carries them in its own query; any
    other action, in the page's URL, which htmx names in HX-Current-URL."""
    if request.method == "GET":
        return ModFilters.parse(request.query_params)
    query = parse_qs(urlsplit(request.headers.get("HX-Current-URL", "")).query)
    return ModFilters.parse({name: values[0] for name, values in query.items()})

async def _mods_context(modpackService: ModpackService, updateCheckService: UpdateCheckService,
                        bundle_id: str, vista: str = "todos", filters: ModFilters = ModFilters()) -> dict[str, Any]:
    """What _mods.html needs: the bundle and its mod list, with the latest update check (for the
    newer versions) and the titles of what the saved resolution names, in one of its views. Modrinth
    is asked at most once, inside mod_list, within one deadline. `cards` has a card for every row
    and every problem's mod; `origins` is the pack's project ids → origin. `shown` are the rows the
    filters keep, and `filter_counts` what each filter option would show (A6, A12)."""
    bundle = await modpackService.bundle_get(bundle_id)
    mods = await modpackService.mod_list(bundle_id, await updateCheckService.latest(bundle_id))
    exceptions = bundle.channel_exceptions
    return {"filters": filters, "shown": filter_rows(mods.rows, filters, exceptions),
            "filter_counts": filter_counts(mods.rows, filters, exceptions),
            "bundle": bundle, "bundle_id": bundle_id, "mods": mods, "titles": mods.titles, "icon": _icon,
            "https_url": _https_url, "downloads": abbreviate_downloads, "vista": _vista(vista),
            "cards": {r.project_id: r for r in mods.rows} | mods.problem_cards,
            "origins": {p.project_id: p.origin.value for p in bundle.projects},
            "humanize": humanize, "environment_label": environment_label,
            # A queued or running job of the pack: its buttons say "En curso…" (E8).
            "busy": bool(await modpackService.active_jobs(bundle_id))}

async def _mods(request: Request, actor: Actor, modpackService: ModpackService, updateCheckService: UpdateCheckService,
                bundle_id: str, success: Optional[str] = None, info: Optional[str] = None,
                hit: Optional[str] = None, vista: str = "todos") -> Response:
    """The #mods fragment; `success` or `info` and `hit` go after it, out of band (a notice, a search
    hit marked "Ya está")."""
    context = await _mods_context(modpackService, updateCheckService, bundle_id, vista, _filters_from(request))
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

async def _resolved(request: Request, actor: Actor, modpackService: ModpackService,
                    updateCheckService: UpdateCheckService, bundle_id: str, success: str,
                    vista: str = "todos") -> Response:
    """#mods after an action that resolved again (decisions, a problem's action), with #builds
    redrawn out of band: its button says whether something is still pending, and "Crear build" is
    the highlighted next step when the current resolution has nothing left to decide and no
    problems (spec §2). Nothing here creates a build."""
    context = await _mods_context(modpackService, updateCheckService, bundle_id, vista, _filters_from(request))
    mods = context["mods"]
    saved = (mods.resolution, mods.stale) if mods.resolution else None
    pending = _has_pending(saved)
    lock = mods.resolution.lock if mods.resolution else None
    ready = lock is not None and not mods.stale and not pending and not lock.problems and lock.complete
    return render(request, "modpacks/_mods.html", actor, success=success, builds_oob=True,
                  builds=await modpackService.build_find(bundle_id), pending=pending, ready=ready, **context)

async def _decided(request: Request, actor: Actor, modpackService: ModpackService,
                   updateCheckService: UpdateCheckService, bundle_id: str, vista: str = "todos") -> Response:
    """#mods after saving decisions (and resolving again), in the view they were saved from."""
    return await _resolved(request, actor, modpackService, updateCheckService, bundle_id, "Decisiones guardadas", vista)

async def _acted(request: Request, actor: Actor, modpackService: ModpackService,
                 updateCheckService: UpdateCheckService, bundle_id: str, contexto: Optional[str],
                 success: str) -> Response:
    """The answer to an action on the pack's mods. Without `contexto`, #mods as always; from a
    problem's card the pack is resolved again (after every action, so what is left is current):
    from the Mods tab ("problemas") #mods answers in that view, from the build dialog ("dialogo")
    the dialog answers, recalculated from that lock, with #mods (in the page's tab, B1) and #builds
    behind it redrawn out of band. Never a build: that is the owner's click."""
    if contexto is None:
        return await _mods(request, actor, modpackService, updateCheckService, bundle_id, vista=_vista_from(request))
    lock = await modpackService.resolve(bundle_id, actor)
    if contexto == "dialogo":
        # mod_list (inside) fills the catalog once for every mod the page and the dialog name: the
        # resolution it reads is the one just saved, this lock.
        context = await _mods_context(modpackService, updateCheckService, bundle_id, _vista_from(request), _filters_from(request))
        mods = context["mods"]
        return _dialog(request, actor, lock, done=success, mods_oob=True, builds_oob=True,
                       builds=await modpackService.build_find(bundle_id),
                       pending=_has_pending((mods.resolution, mods.stale) if mods.resolution else None), **context)
    return await _resolved(request, actor, modpackService, updateCheckService, bundle_id, success, vista="problemas")

# What the decision dialog says when it is sent with every mod left "Por decidir".
NOTHING_DECIDED = "Elige «En el servidor» o «Desactivado» para al menos un mod."

def _dialog(request: Request, actor: Actor, lock: Lock, **context: Any) -> Response:
    """The build dialog, into #build-dialog, from a fresh lock (spec §1): its problems first, each a
    card with its actions (`contexto` "dialogo"), then the mods to decide; with nothing left, "Todo
    resuelto" and "Crear build". `context` has the `bundle`, `bundle_id`, `titles` and `cards` of
    the mods it names, and what else the template shows (a `reason`, `done`, #mods out of band)."""
    bundle = context["bundle"]
    response = render(request, "modpacks/_decide_dialog.html", actor, **({"icon": _icon, "humanize": humanize, "reason": None, "done": None} | context | {
        "problems": lock.problems, "undecided": lock.undecided, "complete": lock.complete,
        "ready": lock.complete and not lock.problems and not lock.undecided, "contexto": "dialogo",
        "pack": {p.project_id: p.origin.value for p in bundle.projects}}))
    response.headers["HX-Retarget"] = "#build-dialog"
    response.headers["HX-Reswap"] = "innerHTML"
    return response

async def _build_dialog(request: Request, actor: Actor, modpackService: ModpackService,
                        modrinthService: ModrinthService, bundle_id: str, lock: Lock, reason: Optional[str] = None,
                        explain: bool = False) -> Response:
    """The build dialog for a refused build (`_dialog`). Every mod it names gets its card in one
    Modrinth call. `explain` adds why the build was refused (unless a `reason` is given), naming the
    mods by title."""
    named = [e.project_id for e in lock.undecided]
    for problem in lock.problems:
        named += [problem.project_id] if problem.project_id else []
        named += [*problem.requested_by, *([problem.conflict_with] if problem.conflict_with else [])]
    cards = await modrinthService.projects_filled(sorted(set(named))) if named else {}
    titles = {i: cards[i].title if i in cards else i for i in named}
    if explain and reason is None:
        reason = _blocked_reason(lock, titles)
    return _dialog(request, actor, lock, bundle=await modpackService.bundle_get(bundle_id), bundle_id=bundle_id,
                   titles=titles, cards=cards, reason=reason)

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
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """Every bundle, and the form to create one (its Minecraft version from Modrinth's list, F09)."""
    bundles = await modpackService.bundle_find()
    return render(request, "modpacks/list.html", actor, bundles=bundles, loaders=[l.value for l in MinecraftLoaders],
                  game_versions=await modrinthService.game_versions_release())

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

# An upload is refused by its declared size before the form is read: Starlette 1.7 caps the form's
# text fields, not its files, which it would spool whole. The margin is the form around the file.
IMPORT_MAX_BYTES = 50 * 1024 * 1024
# A Prism instance's zip may be much bigger (T6/P1): read from the upload's temporary file, its
# worlds never opened. A .mrpack is still refused past IMPORT_MAX_BYTES once read.
PRISM_MAX_BYTES = 1024 * 1024 * 1024
IMPORT_MAX_REQUEST = PRISM_MAX_BYTES + 1024 * 1024
# The temporary disk the imports being uploaded may take together (owner, 2026-10-12): each holds its
# Content-Length from before its body is read until its answer, when Starlette deletes the file.
UPLOADS = UploadBudget(int(getenv("IMPORT_TMP_MAX_MIB", "4096")) * 1024 * 1024)
UPLOAD_BUSY = "Hay otra importación subiendo; intenta en un momento"

@contextmanager
def _upload_room(request: Request) -> Iterator[None]:
    """413 past the size an import may have; 429 if the uploads in flight leave no room for it."""
    declared = request.headers.get("content-length", "")
    if not declared.isdigit() or int(declared) > IMPORT_MAX_REQUEST:
        raise HTTP_413_CONTENT_TOO_LARGE("El archivo pesa más de 1 GiB")
    try:
        with UPLOADS.reserve(int(declared)):
            yield
    except UploadBusy as busy:
        raise QuotaExceeded(UPLOAD_BUSY, 30.0) from busy

@router.get("/modpacks/importar")
@inject
async def modpack_import_page(
        request: Request,
        actor: Actor = Depends(get_web_actor),
        importService: ModpackImportService = Depends(Provide[Dependences.modpackImportService]),
        ):
    """Import a modpack as a new one: an uploaded .mrpack, Prism or CurseForge zip, a CurseForge
    profile code (with the owner's key, T5), or one published on Modrinth."""
    return render(request, "modpacks/import.html", actor, curseforge=importService.curseforge_enabled,
                  nav_active="modpacks")

@router.post("/modpacks/importar")
@inject
async def modpack_import(
        request: Request,
        actor: Actor = Depends(get_web_actor),
        importService: ModpackImportService = Depends(Provide[Dependences.modpackImportService]),
        ):
    """Import an uploaded .mrpack (`archivo`) or a Modrinth reference (`ref`): as a new pack, or as the
    content of `into`. The answer is the report, into the form's #import-result."""
    with _upload_room(request):
        form = await request.form(max_files=1, max_fields=4)
        into = str(form.get("into") or "") or None
        if into is not None and not re.fullmatch(BUNDLE_REF, into):
            raise HTTP_422_UNPROCESSABLE_ENTITY("Ese modpack no existe")
        upload, ref = form.get("archivo"), str(form.get("ref") or "").strip()
        code = str(form.get("codigo") or "").strip()
        if isinstance(upload, UploadFile) and upload.filename:
            if is_prism(upload.file):
                job = await importService.import_prism(upload.file, actor, into=into)
            elif is_curseforge(upload.file):
                job = await importService.import_curseforge(await upload.read(IMPORT_MAX_BYTES + 1), actor, into=into)
            else:
                job = await importService.import_mrpack(await upload.read(IMPORT_MAX_BYTES + 1), actor, into=into)
        elif code:
            job = await importService.import_curseforge_code(code[:40], actor, into=into)
        elif ref:
            job = await importService.import_modrinth(ref[:500], actor, into=into)
        else:
            raise HTTP_422_UNPROCESSABLE_ENTITY("Elige un archivo .mrpack o escribe la URL de un modpack de Modrinth")
    response = render(request, "modpacks/_import_job.html", actor, job=job, report=None)
    response.headers["HX-Trigger"] = JOB_STARTED
    return response

@router.get("/modpacks/importar/{job_id}")
@inject
async def modpack_import_status(
        job_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        importService: ModpackImportService = Depends(Provide[Dependences.modpackImportService]),
        ):
    """An import job in the form's result: its progress while it runs (polling), then its report or
    why it failed."""
    if not re.fullmatch(BUNDLE_REF, job_id):
        raise HTTP_404_NOT_FOUND("Esa importación no existe")
    job, report = await importService.import_status(job_id)
    return render(request, "modpacks/_import_job.html", actor, job=job, report=report)

@router.get("/modpacks/{bundle_id}")
@inject
async def modpack_detail(
        bundle_id: str,
        request: Request,
        vista: Annotated[str, Query()] = "todos",
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        pterodactylService: PterodactylService = Depends(Provide[Dependences.pterodactylService]),
        importService: ModpackImportService = Depends(Provide[Dependences.modpackImportService]),
        jobService: JobService = Depends(Provide[Dependences.jobService]),
        ):
    """A bundle: its mods under the saved resolution, in one of their views (`vista`: every mod, the
    ones to decide, the problems; spec §3), the search to add more, and on the side its builds, the
    servers linked to it, its channels and its local files. A tab asks for this page and keeps only
    #mods."""
    context = await _mods_context(modpackService, updateCheckService, bundle_id, vista, _filters_from(request))
    builds = await modpackService.build_find(bundle_id)
    linked = str(context["bundle"].id)
    servers = [s for s in await pterodactylService.server_find() if s.bundle_id == linked]
    saved = context["mods"].resolution
    # A build made a moment ago (by its job, E9) offers to simulate it on the linked servers.
    fresh = bool(builds) and as_utc(builds[0].created_at) > datetime.now(timezone.utc) - FRESH_BUILD
    bundle = context["bundle"]
    simulated = [await jobService.simulated_build(str(s.id)) for s in servers]
    onboarding = pack_steps(bundle_id, PackFacts(
        projects=len(bundle.projects), resolved=saved is not None and not context["mods"].stale,
        pending=pending_count((saved, context["mods"].stale) if saved else None), latest_build=bundle.latest_build,
        linked_servers=tuple((str(s.id), s.name) for s in servers),
        simulated=bundle.latest_build is not None and bundle.latest_build in simulated))
    return render(request, "modpacks/detail.html", actor, onboarding=onboarding, builds=builds, servers=servers, channel_names=CHANNELS,
                  pending=_has_pending((saved, context["mods"].stale) if saved else None),
                  next_servers=servers if fresh else None, human_size=human_size, nav_active="modpacks",
                  curseforge=importService.curseforge_enabled, **context)

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
    except QuotaExceeded as error:
        # Not a failure of Modrinth: no requests left this minute, and it says for how long (E5).
        return render(request, "modpacks/_search.html", actor, bundle_id=bundle_id, hits=[], failed=str(error.detail),
                      searched=True)
    except Exception:
        logger.warning("Modrinth search failed for bundle %s", bundle_id, exc_info=True)
        return render(request, "modpacks/_search.html", actor, bundle_id=bundle_id, hits=[], failed=True, searched=True)
    owned = {p.project_id for p in bundle.projects}
    # No catalog fill: the hit carries its title and icon, and a card the catalog lacks fetches
    # its own project when opened. Filling here doubled the Modrinth calls on every keystroke.
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
    context = await _mods_context(modpackService, updateCheckService, bundle_id, _vista_from(request), _filters_from(request))
    names = ", ".join(context["titles"].get(i, i) for i in added)
    return render(request, "modpacks/_mods.html", actor, success=f"Agregado: {names}" if names else None,
                  info=None if names else "Ese mod ya estaba en el modpack", hit=ref if from_hit else None, **context)

@router.delete("/modpacks/{bundle_id}/projects/{project_id}")
@inject
async def project_remove(
        bundle_id: str,
        project_id: str,
        request: Request,
        contexto: Annotated[Optional[Contexto], Query()] = None,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """Remove a project from the pack; from a problem's card (`contexto`), resolve again too. When
    another mod still brings it, its pin, decision and exception stay, and the notice says so
    (d-7985ee-881689)."""
    dependents = await modpackService.project_dependents(bundle_id, project_id)
    await modpackService.project_remove(bundle_id, project_id, actor)
    success = "Quitado del modpack"
    if dependents:
        titles = await _titles(modrinthService, dependents)
        success += (f". Sigue como dependencia de {', '.join(titles[i] for i in dependents)}: "
                    "su fijación, su decisión y su excepción se mantienen.")
        if contexto is None:
            # The list alone would show the mod still there, as a dependency, with no word why.
            return await _mods(request, actor, modpackService, updateCheckService, bundle_id, info=success,
                               vista=_vista_from(request))
    return await _acted(request, actor, modpackService, updateCheckService, bundle_id, contexto, success)

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
        # Empty arrives as missing (FastAPI reads an empty form value as absent): "sin fijar" in a
        # row's select, which unpins. Elsewhere a version is required.
        version_id: Annotated[Optional[str], Form(max_length=200, pattern=MODRINTH_REF)] = None,
        contexto: Annotated[Optional[Contexto], Form()] = None,
        confirm_downgrade: Annotated[bool, Form()] = False,
        # From a mod's row (A1, A14): the answer offers to undo, back to `previous` (empty: unpinned).
        fila: Annotated[Optional[Literal["fila"]], Form(alias="from")] = None,
        previous: Annotated[Optional[str], Form(max_length=200, pattern=MODRINTH_REF)] = None,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """Pin a mod to a version of that mod: one key, written atomically by the service. From a
    problem's card ("Usar la beta …", `contexto`), resolve again too. A version before the mod's
    floor is not saved: the answer is a dialog, into #pin-dialog, that describes the step back and
    sends the same pin again with `confirm_downgrade` (F27, d-7985ee-c62c61). From a row (`from`),
    no version unpins, and #mods comes with a notice that undoes the change."""
    if version_id is None:
        if fila is None:
            raise HTTP_422_UNPROCESSABLE_ENTITY("Falta la versión que se fija")
        await modpackService.pin_unset(bundle_id, project_id, actor)
    else:
        try:
            await modpackService.pin_set(bundle_id, project_id, version_id, actor, confirm_downgrade=confirm_downgrade)
        except DowngradeRefused as refused:
            response = render(request, "modpacks/_pin_confirm.html", actor, bundle_id=bundle_id, downgrade=refused.downgrade,
                              version_id=version_id, contexto=contexto, fila=fila, previous=previous)
            response.headers["HX-Retarget"] = "#pin-dialog"
            response.headers["HX-Reswap"] = "innerHTML"
            return response
    if fila is not None:
        context = await _mods_context(modpackService, updateCheckService, bundle_id, _vista_from(request), _filters_from(request))
        return render(request, "modpacks/_mods.html", actor, success=None, info=None, hit=None,
                      undo={"project_id": project_id, "version_id": version_id or "", "previous": previous or ""},
                      **context)
    return await _acted(request, actor, modpackService, updateCheckService, bundle_id, contexto, "Versión fijada")

@router.delete("/modpacks/{bundle_id}/pins/{project_id}")
@inject
async def pin_remove(
        bundle_id: str,
        project_id: str,
        request: Request,
        contexto: Annotated[Optional[Contexto], Query()] = None,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """Remove a mod's pin; from a problem's card (`contexto`), resolve again too."""
    await modpackService.pin_unset(bundle_id, project_id, actor)
    return await _acted(request, actor, modpackService, updateCheckService, bundle_id, contexto, "Fijación quitada")

@router.get("/modpacks/{bundle_id}/trabajo")
@inject
async def pack_jobs(
        bundle_id: str,
        request: Request,
        activo: Annotated[Optional[str], Query(max_length=1)] = None,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    """The pack's banner (E8): its queued or running jobs, polling while there are some. When the
    last one ends (`activo` says the banner showed one), the pack reloads on `job-finished`."""
    jobs = await modpackService.active_jobs(bundle_id)
    # When the last one ended failing, the banner says why instead of emptying in silence.
    failed = await modpackService.last_failed_job(bundle_id) if activo and not jobs else None
    response = render(request, "modpacks/_job_banner.html", actor, jobs=jobs, bundle_id=bundle_id, failed=failed)
    if activo and not jobs:
        # A build its check blocked opens the dialog (E9), outside the part that reloads.
        blocked = await modpackService.last_build_blocked(bundle_id)
        response.headers["HX-Trigger"] = f"{JOB_FINISHED}, {BUILD_BLOCKED}" if blocked else JOB_FINISHED
    return response

@router.post("/modpacks/{bundle_id}/catalogo")
@inject
async def pack_catalog_refresh(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        ):
    """"Actualizar desde Modrinth" (E3): the pack's catalog job, or the one already running; the
    banner and the bar show it."""
    job = await modpackService.catalog_refresh(bundle_id, actor)
    response = render(request, "_notice_oob.html", actor, message="Actualizando desde Modrinth",
                      banner_jobs=[job] if job is not None else None, bundle_id=bundle_id)
    response.headers["HX-Trigger"] = JOB_STARTED
    return response

@router.post("/modpacks/{bundle_id}/channel-exceptions")
@inject
async def channel_exception_add(
        bundle_id: str,
        request: Request,
        project_id: Annotated[str, Form(min_length=1, max_length=200, pattern=MODRINTH_REF)],
        # Empty ("canal del pack" in a row's select) arrives as missing: FastAPI reads an empty form
        # value as absent. Anything else but beta or alpha is a 422.
        channel: Annotated[Optional[Literal["beta", "alpha"]], Form()] = None,
        contexto: Annotated[Optional[Contexto], Form()] = None,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """Let a mod take beta (or alpha) versions while none fits the pack's channels ("Usar la beta" on
    a problem's card, `contexto`), or only the pack's again (`channel` empty, the row's select "canal
    del pack", A1): one key, written by the service; then resolve again, so whether the exception is
    used is current. From a row (no `contexto`), #mods in the page's view."""
    if channel:
        await modpackService.channel_exception_set(bundle_id, project_id, channel, actor)
        success = f"{channel.capitalize()} aceptada para este mod"
    else:
        await modpackService.channel_exception_unset(bundle_id, project_id, actor)
        success = "El mod vuelve a los canales del pack"
    if contexto is None:
        await modpackService.resolve(bundle_id, actor)
        return await _resolved(request, actor, modpackService, updateCheckService, bundle_id, success,
                               vista=_vista_from(request))
    return await _acted(request, actor, modpackService, updateCheckService, bundle_id, contexto, success)

@router.delete("/modpacks/{bundle_id}/channel-exceptions/{project_id}")
@inject
async def channel_exception_remove(
        bundle_id: str,
        project_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """Remove a mod's channel exception: #mods, as the row's select does (A1)."""
    await modpackService.channel_exception_unset(bundle_id, project_id, actor)
    return await _mods(request, actor, modpackService, updateCheckService, bundle_id, vista=_vista_from(request))

@router.post("/modpacks/{bundle_id}/resolve")
@inject
async def modpack_resolve(
        bundle_id: str,
        request: Request,
        vista: Annotated[str, Query()] = "todos",
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """Resolve the bundle now and show the mods under it. The resolution is kept only to show it:
    a build resolves again and freezes that lock. The view it was asked from stays."""
    await modpackService.resolve(bundle_id, actor)
    return await _mods(request, actor, modpackService, updateCheckService, bundle_id, vista=vista)

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
    resolution is run again so the mod leaves "Por decidir"."""
    await modpackService.decision_set(bundle_id, project_id, server == "yes", actor)
    await modpackService.resolve(bundle_id, actor)
    return await _decided(request, actor, modpackService, updateCheckService, bundle_id)

@router.post("/modpacks/{bundle_id}/decisions/bulk")
@inject
async def modpack_decide_bulk(
        bundle_id: str,
        request: Request,
        vista: Annotated[str, Query()] = "todos",
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
    await modpackService.resolve(bundle_id, actor)
    return await _decided(request, actor, modpackService, updateCheckService, bundle_id, vista)

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
    first. Blocked (`BuildBlocked`), it answers the build dialog into #build-dialog: the problems
    with their actions, then the mods to decide, and the reason inside once the dialog was sent
    (its `dialog` field) or decisions came: the mods by title, or that none was decided. Any other
    409 is a notice; /v1 keeps its 409."""
    form = await request.form()
    decisions = _parse_decisions(form)
    from_dialog = form.get("dialog") == "1"
    try:
        if decisions:
            await modpackService.decisions_set(bundle_id, decisions, actor)
        job = await modpackService.build_request(bundle_id, actor)
    except BuildBlocked as blocked:
        nothing_decided = from_dialog and not decisions and blocked.only_undecided
        return await _build_dialog(request, actor, modpackService, modrinthService, bundle_id, blocked.lock,
                                   reason=NOTHING_DECIDED if nothing_decided else None,
                                   explain=from_dialog or bool(decisions))
    # The build is a job now (E9): the banner shows it and the page reloads when it ends. The
    # dialog, if this came from it, closes: its content is emptied.
    # The banner comes with the job, so even one that ends at once is seen ending (and opens the
    # dialog if blocked).
    response = render(request, "_notice_oob.html", actor, message=BUILD_QUEUED,
                      banner_jobs=[job] if job is not None else None, bundle_id=bundle_id)
    response.headers["HX-Trigger"] = JOB_STARTED
    response.headers["HX-Retarget"] = "#build-dialog"
    response.headers["HX-Reswap"] = "innerHTML"
    return response

@router.get("/modpacks/{bundle_id}/builds/dialogo")
@inject
async def build_dialog(
        bundle_id: str,
        request: Request,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """The build dialog after a build job that its check blocked (E9): from the pack resolved from the
    catalog, with why."""
    lock = await modpackService.resolve(bundle_id, actor)
    return await _build_dialog(request, actor, modpackService, modrinthService, bundle_id, lock, explain=True)

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
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        ):
    """The latest check of the modpack's recommended build: what has a newer version, with the
    changelogs. A read: Modrinth failing shows as "changelog no disponible", never as an error. Every
    mod it lists has its card: the catalog is filled once for all of them (the titles are the
    check's)."""
    bundle = await modpackService.bundle_get(bundle_id)
    check = await updateCheckService.latest(bundle_id)
    counted = [u for u in check.updates if not u.pinned] if check else []
    pinned = [u for u in check.updates if u.pinned] if check else []
    preview = await modpackService.update_preview(counted) if counted else None
    if counted or pinned:
        await modrinthService.projects_filled([u.project_id for u in counted + pinned])
    return render(request, "modpacks/updates.html", actor, bundle=bundle, bundle_id=bundle_id, check=check,
                  preview=preview, pinned=pinned, titles={u.project_id: u.title for u in counted},
                  kinds={u.project_id: u.kind for u in counted if u.kind}, nav_active="modpacks")

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
    await modpackService.build_request(bundle_id, actor)
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
        bundle_id: Annotated[Optional[str], Form(pattern=BUNDLE_REF)] = None,
        contexto: Annotated[Optional[Contexto], Form()] = None,
        actor: Actor = Depends(get_web_actor),
        modpackService: ModpackService = Depends(Provide[Dependences.modpackService]),
        modrinthService: ModrinthService = Depends(Provide[Dependences.modrinthService]),
        updateCheckService: UpdateCheckService = Depends(Provide[Dependences.updateCheckService]),
        ):
    """Set a rule; an empty replacement means the mod is dropped with nothing in its place. From a
    problem's card ("Ignorar esta dependencia": `bundle_id` and `contexto`, both or neither) the
    rule is still global; that pack is resolved again and its #mods (or its build dialog) answers
    instead."""
    if (bundle_id is None) != (contexto is None):
        raise HTTP_422_UNPROCESSABLE_ENTITY("Ignorar una dependencia necesita el modpack y desde dónde se pidió")
    await modpackService.replacement_set(project_id.strip(), replacement.strip() or None, actor)
    if bundle_id is not None:
        return await _acted(request, actor, modpackService, updateCheckService, bundle_id, contexto,
                            "Dependencia descartada en todos los packs")
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
