"""The web panel's plumbing: templates, asset URLs, the login redirect and how errors render.

Pages are server-rendered with Jinja2 and enhanced with htmx. Under the CSP (`web_security.CSP`)
nothing inline runs: no `<script>` without `src`, no `style=`, no `hx-on`.
"""
import hashlib
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import quote, urlsplit

import jinja2
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from src.exceptions import CatalogPending, QuotaExceeded, UpstreamError
from src.library.api.limiter import CURRENT_USER
from src.library.utils import as_utc
from src.library.auth.redirect import is_local_path
from src.library.auth.roles import actor_from_session
from src.library.logging import request_id as current_request_id
from src.model.users import Actor
from src.services.users import OIDC_OPERATOR_GROUPS
from src.web import audit_labels

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
DIST_DIR = STATIC_DIR / "dist"

# Errors a page shows as a notice: htmx swaps these into #notices (static/app.js). 429 and 5xx
# are the upstream's (Modrinth, Pterodactyl) passed through by the clients: not a bug of ours.
# The event a page hears when an action left a job: the pack's banner and the bar reload on it.
JOB_STARTED = "job-started"
NOTICE_STATUSES = frozenset({403, 404, 409, 422, 429, 502, 503, 504})
UPSTREAM_STATUSES = frozenset({429, 502, 503, 504})
UPSTREAM_DOWN = "Un servicio externo no respondió; intenta de nuevo."
DETAIL_LIMIT = 500
_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]*")
# What a request that fails validation is told. Never FastAPI's error list: it echoes the input.
INVALID_FORM = "Revisa los datos del formulario."


class AssetVersions:
    """The URL of a built asset under `directory`, with a short hash of its content so a browser
    never keeps a stale copy. Each file is hashed once and cached; a file that is missing (a
    checkout without `npm run build`) gets its plain path, and the page renders unstyled."""

    def __init__(self, directory: Path, prefix: str = "/static/dist/") -> None:
        self.directory = directory
        self.prefix = prefix
        self._urls: dict[str, str] = {}

    def url(self, name: str) -> str:
        if name not in self._urls:
            path = self.directory / name
            url = self.prefix + name
            if path.is_file():
                url += "?v=" + hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            self._urls[name] = url
        return self._urls[name]


ASSETS = AssetVersions(DIST_DIR)
# The panel's own files under static/ (app.js), versioned in the repo: hashed the same way.
STATIC = AssetVersions(STATIC_DIR, prefix="/static/")


def asset_url(name: str) -> str:
    """`/static/dist/<name>?v=<sha256[:12]>`, or the path without `?v` when the file is missing."""
    return ASSETS.url(name)


def static_url(name: str) -> str:
    """`/static/<name>?v=<sha256[:12]>`: a file of static/ that is not built (app.js)."""
    return STATIC.url(name)


templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    undefined=jinja2.StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
))
# What a Modrinth project id or slug may look like; the routes and the templates share it.
MODRINTH_REF = r"^[A-Za-z0-9_\-]+$"
templates.env.tests["modrinth_ref"] = lambda value: (
    isinstance(value, str) and len(value) <= 200 and re.fullmatch(MODRINTH_REF, value) is not None)
# A bundle's id (a MongoDB ObjectId): what `?pack=` may carry to a mod's card.
BUNDLE_REF = r"^[0-9a-fA-F]{24}$"
templates.env.tests["bundle_ref"] = lambda value: isinstance(value, str) and re.fullmatch(BUNDLE_REF, value) is not None
templates.env.globals["asset_url"] = asset_url
templates.env.globals["static_url"] = static_url


def ago(value: Optional[datetime], now: Optional[datetime] = None) -> str:
    """How long ago, in words for the panel: "hace 3 s", "hace 4 min", "hace 2 h"."""
    if value is None:
        return ""
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - as_utc(value)).total_seconds()))
    if seconds < 60:
        return f"hace {seconds} s"
    if seconds < 3600:
        return f"hace {seconds // 60} min"
    return f"hace {seconds // 3600} h"


def seconds_until(value: Optional[datetime]) -> int:
    """Whole seconds from now until `value`; 0 when it is past."""
    if value is None:
        return 0
    return max(0, math.ceil((as_utc(value) - datetime.now(timezone.utc)).total_seconds()))


_URL = re.compile(r"https?://[^\s<>\"'`]+")
# What usually ends a sentence or a Markdown link around a URL, not the URL itself.
_URL_TAIL = ".,;:!?'\")]}"


def linkify(text: str) -> Markup:
    """A changelog as plain text with its http(s) URLs as links (F29, d-7985ee-58228f): every piece
    is escaped, nothing else of the Markdown is rendered, and a link opens in another tab without
    the referrer nor a follow."""
    parts: list[str] = []
    last = 0
    for match in _URL.finditer(text):
        url = match.group(0)
        while url and url[-1] in _URL_TAIL and not (url[-1] == ")" and url.count("(") >= url.count(")")):
            url = url[:-1]
        end = match.start() + len(url)
        parts.append(str(escape(text[last:match.start()])))
        parts.append(f'<a href="{escape(url)}" rel="noopener noreferrer nofollow" target="_blank" '
                     f'class="text-emerald-700 underline dark:text-emerald-400">{escape(url)}</a>')
        last = end
    parts.append(str(escape(text[last:])))
    return Markup("".join(parts))


templates.env.filters["ago"] = ago
templates.env.globals["AUDIT_ACTIONS"] = audit_labels.ACTIONS
templates.env.globals["AUDIT_AREAS"] = audit_labels.AREAS
templates.env.globals["AUDIT_DETAILS"] = audit_labels.DETAILS
templates.env.globals["audit_detail"] = audit_labels.detail_text
templates.env.globals["audit_target"] = audit_labels.target_label
templates.env.globals["audit_target_option"] = audit_labels.target_option
templates.env.filters["seconds_until"] = seconds_until
templates.env.filters["linkify"] = linkify


class LoginRequired(Exception):
    """No session: the browser goes to the login, and comes back to `redirect`."""

    def __init__(self, redirect: str) -> None:
        super().__init__(redirect)
        self.redirect = redirect


def login_url(redirect: str) -> str:
    # The same rule as /v1/login (`is_local_path`): anything else goes back to "/".
    return "/v1/login?redirect=" + quote(redirect if is_local_path(redirect) else "/", safe="/")


async def login_required_handler(request: Request, exc: Exception) -> Response:
    """htmx cannot follow a redirect to another page: it gets a 401 with `HX-Redirect`. A
    browser gets a 303 to the login."""
    redirect = exc.redirect if isinstance(exc, LoginRequired) else "/"
    if request.headers.get("HX-Request"):
        return Response(status_code=401, headers={"HX-Redirect": login_url(redirect)})
    return RedirectResponse(login_url(redirect), status_code=303)


def _return_to(request: Request) -> str:
    """The page to come back to after the login: the one in the browser for htmx, this one for a
    GET, the dashboard otherwise (a form's POST cannot be replayed)."""
    if request.headers.get("HX-Request"):
        current = request.headers.get("HX-Current-URL")
        if current:
            parts = urlsplit(current)
            return parts.path + (f"?{parts.query}" if parts.query else "")
        return "/"
    if request.method == "GET":
        return request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return "/"


async def get_web_actor(request: Request) -> Actor:
    """Like `get_current_actor`, but without a session it raises `LoginRequired`: a page
    redirects to the login instead of answering 401."""
    try:
        actor = actor_from_session(request.session, OIDC_OPERATOR_GROUPS)
    except HTTPException as error:
        if error.status_code != 401:
            raise
        raise LoginRequired(_return_to(request)) from error
    # The error page of WebRoute renders the layout with it.
    request.state.actor = actor
    # The requests to Modrinth this request makes count against this user's quota (E4).
    CURRENT_USER.set(f"{actor.iss}|{actor.sub}")
    return actor


def render(request: Request, name: str, actor: Actor, **context: Any) -> HTMLResponse:
    """Render a page (or a fragment) for `actor`; the layout reads the actor for the nav."""
    return templates.TemplateResponse(request, name, {"actor": actor, **context})


def upstream_message(service: str, status: int) -> str:
    """What the person reads when `service` failed with `status` (never the English detail)."""
    if status == 404:
        return f"{service} no encontró lo pedido."
    if status in (401, 403):
        return f"{service} rechazó la credencial del panel."
    if status in (502, 504):
        return f"{service} no respondió; intenta de nuevo."
    return f"{service} respondió con un error ({status})."


# A credential written outside a URL: a bearer token, or a key=value / key: value whose key names one.
_BEARER = re.compile(r"(?i)\b(bearer)\s+[^\s,;]+")
_SECRET_PAIR = re.compile(r"(?i)\b((?:x-)?(?:api[-_]?key|token|secret|password|passwd|authorization)\s*[=:]\s*)(?!bearer\b)[^\s,;&]+")


def sanitize_detail(text: str) -> str:
    """The upstream's text without the query of any URL and without a credential written outside one
    (a bearer token, a `token=` or `api-key:` value), cut to 500 characters."""
    text = _URL_QUERY.sub(r"\1", text)
    text = _BEARER.sub(r"\1 [oculto]", text)
    return _SECRET_PAIR.sub(r"\1[oculto]", text)[:DETAIL_LIMIT]


def _notice_response(request: Request, message: str, status: int, detail: Optional[str] = None,
                     request_id: Optional[str] = None) -> Optional[Response]:
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request, "_notice.html",
            {"message": message, "tone": "error", "detail": detail, "request_id": request_id},
            status_code=status)
    actor = getattr(request.state, "actor", None)
    if not isinstance(actor, Actor):
        return None
    return templates.TemplateResponse(request, "error.html",
                                      {"actor": actor, "message": message, "status": status,
                                       "detail": detail, "request_id": request_id},
                                      status_code=status)


class WebRoute(APIRoute):
    """A panel route: a 403, 404, 409 or 422 from a service (or a 429/502/503/504 from upstream), or a request that fails validation
    (422, with a fixed message), is shown as a notice: a fragment for htmx (static/app.js swaps
    it into #notices), a page with the layout otherwise."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def web_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except UpstreamError as error:
                # An upstream's 400 or 500 is not ours to show as such: the panel answers 502.
                status = error.status_code if error.status_code in NOTICE_STATUSES else 502
                detail = f"{error.service} · HTTP {error.upstream_status} · {sanitize_detail(error.raw)}"
                response = _notice_response(request, upstream_message(error.service, error.upstream_status),
                                            status, detail, current_request_id())
                if response is None:
                    raise
                return response
            except CatalogPending as pending:
                # Not an error: the work is queued (E6). The notice says so, and the pack's banner
                # comes with the job, so even one that ends at once is seen ending.
                if not request.headers.get("HX-Request"):
                    raise
                actor = getattr(request.state, "actor", None)
                job = pending.job
                response = templates.TemplateResponse(request, "_notice_oob.html", {
                    "actor": actor, "message": str(pending.detail), "tone": "info", "banner_jobs": [job],
                    "bundle_id": job.payload.get("bundle_id")})
                response.headers["HX-Trigger"] = JOB_STARTED
                response.headers["HX-Reswap"] = "none"
                return response
            except HTTPException as error:
                if error.status_code not in NOTICE_STATUSES:
                    raise
                message = str(error.detail) if error.detail else "No se pudo completar la acción."
                if error.status_code in UPSTREAM_STATUSES and not isinstance(error, QuotaExceeded):
                    message = f"{UPSTREAM_DOWN} {error.detail}" if error.detail else UPSTREAM_DOWN
                response = _notice_response(request, message, error.status_code)
                if response is None:
                    raise
                return response
            except RequestValidationError:
                response = _notice_response(request, INVALID_FORM, 422)
                if response is None:
                    raise
                return response

        return web_handler
