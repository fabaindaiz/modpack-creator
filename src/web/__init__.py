"""The web panel's plumbing: templates, asset URLs, the login redirect and how errors render.

Pages are server-rendered with Jinja2 and enhanced with htmx. Under the CSP (`web_security.CSP`)
nothing inline runs: no `<script>` without `src`, no `style=`, no `hx-on`.
"""
import hashlib
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import quote, urlsplit

import jinja2
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates

from src.library.auth.redirect import is_local_path
from src.library.auth.roles import actor_from_session
from src.model.users import Actor
from src.services.users import OIDC_OPERATOR_GROUPS

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
DIST_DIR = STATIC_DIR / "dist"

# Errors a page shows as a notice: htmx swaps these into #notices (static/app.js). 429 and 5xx
# are the upstream's (Modrinth, Pterodactyl) passed through by the clients: not a bug of ours.
NOTICE_STATUSES = frozenset({403, 404, 409, 422, 429, 502, 503, 504})
UPSTREAM_STATUSES = frozenset({429, 502, 503, 504})
UPSTREAM_DOWN = "Un servicio externo no respondió; intenta de nuevo."
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
templates.env.globals["asset_url"] = asset_url
templates.env.globals["static_url"] = static_url


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
    return actor


def render(request: Request, name: str, actor: Actor, **context: Any) -> HTMLResponse:
    """Render a page (or a fragment) for `actor`; the layout reads the actor for the nav."""
    return templates.TemplateResponse(request, name, {"actor": actor, **context})


def _notice_response(request: Request, message: str, status: int) -> Optional[Response]:
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "_notice.html", {"message": message, "tone": "error"},
                                          status_code=status)
    actor = getattr(request.state, "actor", None)
    if not isinstance(actor, Actor):
        return None
    return templates.TemplateResponse(request, "error.html",
                                      {"actor": actor, "message": message, "status": status},
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
            except HTTPException as error:
                if error.status_code not in NOTICE_STATUSES:
                    raise
                message = str(error.detail) if error.detail else "No se pudo completar la acción."
                if error.status_code in UPSTREAM_STATUSES:
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
