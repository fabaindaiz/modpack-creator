"""La base del panel y el tablero (etapa 1): login, rol, assets, CSP y escapado.

Los servicios se sustituyen con dobles en el contenedor, como en `tests/test_health.py`, y la app
se arma sin `with`: el lifespan abriría MongoDB y el proveedor OIDC.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional, cast

import pytest
from beanie import PydanticObjectId
from dependency_injector import providers
from fastapi import Request
from fastapi.testclient import TestClient

from src.exceptions import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT
from src.model.jobs import Job
from src.model.modpack import ModpackBuild, ModpackBundle
from src.model.pterodactyl import PterodactylServer, ServerScan
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from tests._actors import EDITOR, OPERATOR, session_for
from tests._session import session_cookie
from tests.conftest import MongoRunner

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
BUNDLE_ID = PydanticObjectId()


def server(name: str, bundle_id: Optional[str] = None) -> PterodactylServer:
    # model_construct: un Document de Beanie no se instancia sin init_beanie.
    return PterodactylServer.model_construct(
        id=PydanticObjectId(), identifier="1a2b", internal_id=1, uuid="1a2b-0", name=name, node="n1",
        description="", status=None, is_suspended=False, is_installing=False, is_transferring=False,
        bundle_id=bundle_id)


class FakePterodactyl:
    def __init__(self, servers: list[PterodactylServer], drifts: dict[str, Optional[str]]) -> None:
        self.servers = servers
        self.drifts = drifts

    async def server_find(self) -> list[PterodactylServer]:
        return self.servers

    async def scan_latest(self, server_id: str, directory: str = "/mods") -> ServerScan:
        if server_id not in self.drifts:
            raise HTTP_404_NOT_FOUND(f"Server '{server_id}' has no scan of {directory}")
        return ServerScan.model_construct(id=PydanticObjectId(), server_id=PydanticObjectId(server_id),
                                          directory=directory, scanned_at=T0, full=False, files=[],
                                          drift=self.drifts[server_id])


class FakeModpack:
    def __init__(self, rows: list[tuple[ModpackBundle, Optional[ModpackBuild]]],
                 resolutions: Optional[dict[str, Any]] = None) -> None:
        self.rows = rows
        self.resolutions = resolutions or {}

    async def latest_builds(self) -> list[tuple[ModpackBundle, Optional[ModpackBuild]]]:
        return self.rows

    async def resolution_many(self, bundle_ids: list[str]) -> dict[str, Any]:
        return {b: self.resolutions[b] for b in bundle_ids if b in self.resolutions}


class FakeChecks:
    def __init__(self, checks: Optional[dict[PydanticObjectId, Any]] = None) -> None:
        self.checks = checks or {}

    async def latest_many(self, bundle_ids: list[str]) -> dict[PydanticObjectId, Any]:
        return self.checks


class FakeJobs:
    def __init__(self, jobs: list[Job], failed: Optional[list[Job]] = None, done: Optional[list[Job]] = None) -> None:
        self.jobs = jobs
        self.failed = failed or []
        self.done = done or []
        self.limits: list[int] = []

    async def recent(self, limit: int = 10) -> list[Job]:
        self.limits.append(limit)
        return self.jobs[:limit]

    async def find(self, *, server_id: Optional[str], kind: Optional[str], status: Optional[str],
                   before_id: Optional[str], limit: int) -> list[Job]:
        assert status in ("failed", "done") and server_id is None and kind is None and before_id is None
        return (self.failed if status == "failed" else self.done)[:limit]


def default_fakes(server_name: str = "Survival") -> tuple[FakePterodactyl, FakeModpack, FakeJobs]:
    bundle = ModpackBundle.model_construct(id=BUNDLE_ID, name="Pack Uno", description="", version="1.21.1",
                                           loader=MinecraftLoaders.fabric, latest_build=3, recommended_build=2)
    build = ModpackBuild.model_construct(id=PydanticObjectId(), bundle_id=BUNDLE_ID, number=3, created_at=T0)
    linked = server(server_name, bundle_id=str(BUNDLE_ID))
    other = server("Creativo")
    drifts: dict[str, Optional[str]] = {str(linked.id): "⚠️ Survival no coincide con Pack Uno, build 3:\n- sobra mods/x.jar"}
    jobs = [Job.model_construct(id=PydanticObjectId(), kind="server_scan", key=f"server:{linked.id}",
                                status="running", step="scan", created_at=T0, attempts=1, max_attempts=3)]
    return FakePterodactyl([linked, other], drifts), FakeModpack([(bundle, build)]), FakeJobs(jobs)


@pytest.fixture(autouse=True)
def reset_overrides() -> Any:
    yield
    import main

    for provider in (main.container.pterodactylService, main.container.modpackService, main.container.jobService,
                     main.container.updateCheckService):
        provider.reset_override()


def client(actor: Optional[Actor], fakes: Optional[tuple[FakePterodactyl, FakeModpack, FakeJobs]] = None,
           checks: Optional[FakeChecks] = None) -> TestClient:
    import main

    pterodactyl, modpack, jobs = fakes or default_fakes()
    main.container.pterodactylService.override(providers.Object(pterodactyl))
    main.container.modpackService.override(providers.Object(modpack))
    main.container.jobService.override(providers.Object(jobs))
    main.container.updateCheckService.override(providers.Object(checks or FakeChecks()))
    # Sin `with`: el lifespan abriría MongoDB y el proveedor OIDC.
    test_client = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
    if actor is not None:
        test_client.cookies.set("session", session_cookie(session_for(actor)))
    return test_client


def test_home_without_session_redirects_to_login() -> None:
    response = client(None).get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/v1/login?redirect=/"


def test_htmx_without_session_gets_hx_redirect() -> None:
    response = client(None).get("/", headers={"HX-Request": "true"})
    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/v1/login?redirect=/"


def test_htmx_redirect_returns_to_the_page_in_the_browser() -> None:
    response = client(None).get("/", headers={"HX-Request": "true",
                                               "HX-Current-URL": "https://testserver/servers/abc?x=1"})
    assert response.headers["hx-redirect"] == "/v1/login?redirect=/servers/abc%3Fx%3D1"


def test_home_renders_for_editor_with_role_badge() -> None:
    response = client(EDITOR).get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Editor" in response.text
    assert "Operador" not in response.text
    assert EDITOR.name in response.text
    assert "Survival" in response.text


def test_home_shows_operator_badge_for_operator() -> None:
    response = client(OPERATOR).get("/")
    assert response.status_code == 200
    assert "Operador" in response.text
    assert OPERATOR.name in response.text


def test_home_shows_bundle_drift_builds_and_jobs() -> None:
    fakes = default_fakes()
    response = client(EDITOR, fakes).get("/")
    text = response.text
    assert "Pack Uno" in text  # the server's bundle and the bundle's builds
    assert "Desviado" in text and "sobra mods/x.jar" in text
    assert "Sin escaneo" in text  # the server without a scan
    assert "Escaneo" in text and "En curso" in text
    assert f'href="/jobs/{fakes[2].jobs[0].id}"' in text and 'href="/jobs"' in text
    assert f'hx-get="/jobs/{fakes[2].jobs[0].id}/summary"' in text
    assert fakes[2].limits == [10]


def test_layout_has_nav_logout_notices_and_htmx_config() -> None:
    text = client(EDITOR).get("/").text
    for label in ("Tablero", "Modpacks", "Servidores", "Trabajos", "Auditoría", "Salir"):
        assert label in text
    assert 'href="/v1/logout"' in text
    assert re.search(r'<div id="notices"[^>]*aria-live="polite"', text)
    assert ('<meta name="htmx-config" '
            'content=\'{"allowEval":false,"selfRequestsOnly":true,"includeIndicatorStyles":false,'
            '"attributesToSettle":["class","width","height"]}\'>') in text


def test_htmx_never_settles_the_style_attribute() -> None:
    """htmx's settle step copies `attributesToSettle` from an old element to the new one with the
    same id; copying `style` is an inline style the CSP refuses (a console error on every live
    refresh of a section with inputs). The default list has it: ours must not."""
    text = client(EDITOR).get("/").text
    meta = re.search(r'<meta name="htmx-config" content=\'([^\']*)\'>', text)
    assert meta
    settle = json.loads(meta.group(1))["attributesToSettle"]
    assert "style" not in settle and "class" in settle
    assert re.search(r'<script src="/static/app\.js\?v=[0-9a-f]{12}" defer></script>', text)


def test_page_links_hashed_css() -> None:
    import src.web as web

    text = client(EDITOR).get("/").text
    assert "/static/dist/app.css" in text
    assert "/static/dist/htmx.min.js" in text
    if (web.DIST_DIR / "app.css").is_file():
        assert re.search(r'/static/dist/app\.css\?v=[0-9a-f]{12}"', text)


def test_app_js_is_linked_with_its_hash() -> None:
    # app.js is versioned in the repo, so it always exists: a deploy never serves a stale copy.
    text = client(EDITOR).get("/").text
    assert re.search(r'<script src="/static/app\.js\?v=[0-9a-f]{12}" defer></script>', text)


def test_page_has_no_inline_script_or_style() -> None:
    text = client(OPERATOR).get("/").text
    scripts = re.findall(r"<script\b[^>]*>", text)
    assert scripts and all(re.search(r'\bsrc="', tag) for tag in scripts)
    assert not re.search(r"<script\b[^>]*>(?!</script>)", text)  # nothing between <script> and </script>
    assert "<style" not in text
    assert not re.search(r"\sstyle=", text)
    assert not re.search(r"\shx-on", text)
    assert not re.search(r"\son[a-z]+=", text)


def test_names_are_escaped() -> None:
    text = client(EDITOR, default_fakes(server_name="<script>x</script>")).get("/").text
    assert "&lt;script&gt;x&lt;/script&gt;" in text
    assert "<script>x</script>" not in text


def test_app_starts_without_built_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.web as web

    monkeypatch.setattr(web, "ASSETS", web.AssetVersions(tmp_path / "dist"))
    response = client(EDITOR).get("/")
    assert response.status_code == 200
    assert 'href="/static/dist/app.css"' in response.text


def test_static_app_js_is_served_without_eval() -> None:
    response = client(None).get("/static/app.js")
    assert response.status_code == 200
    assert "eval(" not in response.text and "new Function" not in response.text
    assert "htmx:beforeSwap" in response.text
    # Only an HTML notice is swapped in; a JSON body (FastAPI's, Starlette's) never reaches innerHTML.
    assert "text/html" in response.text and "notice-client-error" in response.text


def test_app_js_shows_a_notice_for_every_other_client_error() -> None:
    """A 429 or 400 passed through from Modrinth or Pterodactyl has no notice fragment: without a
    notice the click would look like a no-op. A 401 with HX-Redirect is htmx's to follow."""
    text = client(None).get("/static/app.js").text
    handler = text[text.index("htmx:beforeSwap"):text.index("htmx:sendError")]
    assert "status >= 400 && status < 500" in handler
    assert "getResponseHeader('HX-Redirect')" in handler
    other = handler[handler.index("status >= 400 && status < 500"):]
    assert "shouldSwap = false" in other and "showTemplate('notice-client-error', null, elt)" in other


def test_app_js_closes_a_dialog_only_for_its_own_form() -> None:
    """A dialog closes when its form is answered; a fragment it loads for itself (the update
    preview when it opens, the build select's change) must leave it open."""
    text = client(None).get("/static/app.js").text
    handler = text[text.index("htmx:afterRequest"):]
    handler = handler[:handler.index("});")]
    assert "HTMLFormElement" in handler and "dialog.close()" in handler
    assert handler.index("HTMLFormElement") < handler.index("dialog.close()")


def test_app_js_puts_a_row_select_back_when_its_change_fails() -> None:
    """A1 (owner, 2026-10-13): a mod's row selects apply on change. htmx swaps nothing for an
    error, so the select would show a value that was not saved: it goes back to `data-saved`."""
    text = client(None).get("/static/app.js").text
    restore = text[text.index("function restoreSaved"):]
    restore = restore[:restore.index("\n  }\n")]
    assert "select[data-saved]" in restore and "dataset.saved" in restore
    for event in ("htmx:responseError", "htmx:sendError"):
        handler = text[text.index(f"'{event}'"):]
        assert "restoreSaved(" in handler[:handler.index("});")], event


def test_app_js_puts_the_row_selects_back_when_the_pin_dialog_closes() -> None:
    """A pin that goes back opens #pin-dialog (F27); cancelling it leaves the row's select on what
    is saved. Confirming redraws #mods anyway."""
    text = client(None).get("/static/app.js").text
    handler = text[text.index("'close'"):]
    handler = handler[:handler.index("});")]
    assert "pin-dialog" in handler and "restoreSaved(" in handler


def test_app_js_loads_a_job_card_s_detail_on_open_and_drops_it_on_close() -> None:
    """A13 and the final review (minor 5): an open card shows its job's detail, which polls while the
    job runs; a closed card holds nothing, so nothing polls behind it."""
    text = client(None).get("/static/app.js").text
    handler = text[text.index("data-job-detail"):]
    handler = handler[:handler.index("}, true);")]
    assert "htmx.ajax('GET'" in handler and ".open" in handler and "innerHTML = ''" in handler
    assert "addEventListener('toggle'" in text


def test_app_js_keeps_the_undo_notice_while_its_undo_asks_to_confirm() -> None:
    """Final review of batch A (minor 3): "Deshacer" back to a version before the floor answers the
    downgrade dialog; the notice must stay, or cancelling the dialog loses the undo."""
    text = client(None).get("/static/app.js").text
    handler = text[text.index("The notice that undoes a row's pin"):]
    handler = handler[:handler.index("});")]
    assert "HX-Retarget" in handler and "notice.remove()" in handler
    assert handler.index("HX-Retarget") < handler.index("notice.remove()")


def test_app_js_keeps_the_mod_filters_when_the_pack_reloads_for_a_job() -> None:
    """Final review of batch A (minor 4): #pack-main reloads itself when a job starts or ends, with a
    bare GET of the pack: the mod list came back unfiltered under a URL that still had the filters."""
    text = client(None).get("/static/app.js").text
    handler = text[text.index("htmx:configRequest"):]
    handler = handler[:handler.index("});")]
    assert "pack-main" in handler and "location.search" in handler and "parameters" in handler


def test_app_js_redraws_the_mods_when_a_row_change_fails_after_writing() -> None:
    """Final review of batch A (minor 2): a row change can be saved and then fail (the resolution
    after it); putting the select back would show what is no longer saved. On an answered error,
    #mods is asked again; on a network error (nothing answered) the select goes back."""
    text = client(None).get("/static/app.js").text
    handler = text[text.index("'htmx:responseError'"):]
    handler = handler[:handler.index("});")]
    assert "redrawMods(" in handler
    redraw = text[text.index("function redrawMods"):]
    redraw = redraw[:redraw.index("\n  }\n")]
    assert "htmx.ajax('GET'" in redraw and "#mods" in redraw


class FailingPterodactyl(FakePterodactyl):
    def __init__(self, error: Exception) -> None:
        super().__init__([], {})
        self.error = error

    async def server_find(self) -> list[PterodactylServer]:
        raise self.error


def test_htmx_error_from_a_service_is_a_notice_fragment() -> None:
    _, modpack, jobs = default_fakes()
    fakes = (FailingPterodactyl(HTTP_409_CONFLICT("El servidor <b>ocupado</b>")), modpack, jobs)
    response = client(EDITOR, fakes).get("/", headers={"HX-Request": "true"})
    assert response.status_code == 409
    assert "data-notice" in response.text
    assert "El servidor &lt;b&gt;ocupado&lt;/b&gt;" in response.text
    assert "<html" not in response.text


def test_error_from_a_service_without_htmx_is_a_page_with_the_notice() -> None:
    _, modpack, jobs = default_fakes()
    fakes = (FailingPterodactyl(HTTP_404_NOT_FOUND("No existe")), modpack, jobs)
    response = client(EDITOR, fakes).get("/")
    assert response.status_code == 404
    assert "<html" in response.text and "No existe" in response.text and "Editor" in response.text


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_an_upstream_error_is_a_notice_not_a_500(status: int) -> None:
    from fastapi import HTTPException

    _, modpack, jobs = default_fakes()
    fakes = (FailingPterodactyl(HTTPException(status, "Modrinth <b>caído</b>")), modpack, jobs)
    response = client(EDITOR, fakes).get("/", headers={"HX-Request": "true"})
    assert response.status_code == status
    assert "data-notice" in response.text and "<html" not in response.text
    assert "Un servicio externo no respondió; intenta de nuevo." in response.text
    assert "Modrinth &lt;b&gt;caído&lt;/b&gt;" in response.text
    page = client(EDITOR, fakes).get("/")
    assert page.status_code == status and "<html" in page.text and "Un servicio externo no respondió" in page.text


def test_app_js_swaps_the_upstream_notices_too() -> None:
    text = client(None).get("/static/app.js").text
    line = next(l for l in text.splitlines() if "NOTICE_STATUSES =" in l)
    for status in ("429", "502", "503", "504"):
        assert status in line


# Lecturas nuevas para el tablero, contra MongoDB real.


@pytest.mark.network
def test_recent_jobs_are_newest_first_and_limited(mongo: MongoRunner) -> None:
    from datetime import timedelta

    from src.database.jobs import JobDatabase
    from src.services.jobs import JobService

    async def scenario() -> list[str]:
        jobs = JobDatabase()
        for minutes in (5, 1, 3, 2, 4):
            await jobs.enqueue("server_scan", key=None, payload={"m": minutes}, now=T0 + timedelta(minutes=minutes))
        service = JobService(jobs, cast(Any, None), cast(Any, None), cast(Any, None))
        return [job.payload["m"] for job in await service.recent(limit=3)]

    assert mongo(scenario) == [5, 4, 3]


@pytest.mark.network
def test_latest_builds_pairs_each_bundle_with_its_newest_build(mongo: MongoRunner) -> None:
    from src.database.modpack import ModpackDatabase
    from src.library.resolution import Lock, ResolveRequest, Target
    from src.services.modpack import ModpackService

    async def scenario() -> list[tuple[str, Optional[int]]]:
        db = ModpackDatabase()
        built = await db.bundle_insert(ModpackBundle(name="con builds", description="", version="1.21.1",
                                                     loader=MinecraftLoaders.fabric))
        await db.bundle_insert(ModpackBundle(name="sin builds", description="", version="1.21.1",
                                             loader=MinecraftLoaders.fabric))
        request = ResolveRequest(target=Target(game_version="1.21.1", loader="fabric"), projects=[])
        lock = Lock(entries=[], problems=[], suggestions=[], needs_projects=set(), needs_types=set(), needs_versions=set())
        for number in (1, 2):
            assert built.id is not None
            await db.build_insert(ModpackBuild(bundle_id=built.id, number=number, created_at=T0, request=request, lock=lock))
            await db.bundle_latest_build(str(built.id), number)
        service = ModpackService(db, cast(Any, None), cast(Any, None), cast(Any, None), cast(Any, None))
        return sorted((bundle.name, build.number if build else None) for bundle, build in await service.latest_builds())

    assert mongo(scenario) == [("con builds", 2), ("sin builds", None)]


# Validation errors: a form route missing a field answers a notice, never FastAPI's JSON (which
# echoes the submitted input).


def form_app() -> TestClient:
    from fastapi import APIRouter, Depends, FastAPI, Form
    from starlette.middleware.sessions import SessionMiddleware

    from src.web import WebRoute, get_web_actor, render

    router = APIRouter(route_class=WebRoute)

    @router.post("/form")
    async def form(request: Request, name: Annotated[str, Form()], count: Annotated[int, Form()],
                   actor: Actor = Depends(get_web_actor)) -> Any:
        return render(request, "_notice.html", actor, message=name, tone="success")

    application = FastAPI()
    application.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    application.include_router(router)
    test_client = TestClient(application, base_url="https://testserver")
    test_client.cookies.set("session", session_cookie(session_for(EDITOR)))
    return test_client


INJECTED = "<img src=x>zz-injected"


def test_htmx_form_missing_a_field_gets_a_notice_without_the_input() -> None:
    response = form_app().post("/form", data={"count": INJECTED}, headers={"HX-Request": "true"})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "Revisa los datos del formulario." in response.text and "data-notice" in response.text
    assert "zz-injected" not in response.text
    assert "<html" not in response.text


def test_form_missing_a_field_without_htmx_gets_the_error_page() -> None:
    response = form_app().post("/form", data={"count": INJECTED})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text and "Revisa los datos del formulario." in response.text
    assert "zz-injected" not in response.text


def test_a_valid_form_still_reaches_the_route() -> None:
    response = form_app().post("/form", data={"name": "ok", "count": "2"}, headers={"HX-Request": "true"})
    assert response.status_code == 200 and "ok" in response.text


def _upstream_client(error: Exception) -> TestClient:
    _, modpack, jobs = default_fakes()
    return client(EDITOR, (FailingPterodactyl(error), modpack, jobs))


def test_upstream_404_reads_in_spanish_with_a_sanitized_detail() -> None:
    from src.exceptions import UpstreamError

    error = UpstreamError("Pterodactyl", 404, raw="Request has failed at https://node/x?token=abc now")
    response = _upstream_client(error).get("/", headers={"HX-Request": "true", "Sec-Fetch-Site": "same-origin"})
    body = response.text
    visible = re.sub(r"<[^>]+>", " ", body)
    assert response.status_code == 404
    assert "Pterodactyl no encontró lo pedido." in body
    assert "Request has failed with" not in visible
    assert "token=abc" not in body and "https://node/x" in body
    assert "Ver detalle" in body and "Pterodactyl · HTTP 404" in body
    assert 'data-notice-tone="error"' in body
    request_id = response.headers["X-Request-ID"]
    assert request_id in body


def test_upstream_messages_by_status() -> None:
    from src.web import upstream_message

    assert upstream_message("Modrinth", 401) == "Modrinth rechazó la credencial del panel."
    assert upstream_message("Modrinth", 403) == "Modrinth rechazó la credencial del panel."
    assert upstream_message("Modrinth", 502) == "Modrinth no respondió; intenta de nuevo."
    assert upstream_message("Modrinth", 504) == "Modrinth no respondió; intenta de nuevo."
    assert upstream_message("Modrinth", 400) == "Modrinth respondió con un error (400)."


def test_upstream_500_from_above_is_a_502_notice() -> None:
    from src.exceptions import UpstreamError

    response = _upstream_client(UpstreamError("Modrinth", 500, raw="boom")).get("/", headers={"HX-Request": "true"})
    assert response.status_code == 502
    assert "Modrinth respondió con un error (500)." in response.text


def test_upstream_error_without_htmx_is_a_page_with_the_notice() -> None:
    from src.exceptions import UpstreamError

    response = _upstream_client(UpstreamError("Pterodactyl", 404, raw="x")).get("/")
    assert response.status_code == 404 and "<html" in response.text
    assert "Pterodactyl no encontró lo pedido." in response.text


def test_sanitize_detail_cuts_queries_and_length() -> None:
    from src.web import sanitize_detail

    assert sanitize_detail("GET https://a.b/c?token=s&x=1 failed") == "GET https://a.b/c failed"
    assert len(sanitize_detail("x" * 900)) == 500


def _render_macro(call: str) -> str:
    from src.web import templates
    return templates.env.from_string('{% import "_macros.html" as ui %}' + call).render()


def test_htmx_buttons_disable_themselves_while_busy() -> None:
    for verb in ("hx-post", "hx-put", "hx-delete"):
        assert 'hx-disabled-elt="this"' in _render_macro('{{ ui.button("X", attrs={"%s": "/x"}) }}' % verb)
    assert "hx-disabled-elt" not in _render_macro('{{ ui.button("Y", attrs={"href": "/y"}) }}')
    assert "hx-disabled-elt" not in _render_macro('{{ ui.button("Z", attrs={"hx-get": "/z"}) }}')


def test_success_notice_has_the_success_tone() -> None:
    text = _render_macro('{{ ui.success("Listo <b>") }}')
    assert 'data-notice-tone="success"' in text and "Listo &lt;b&gt;" in text


def test_only_ui_success_closes_itself() -> None:
    """A success notice from an action closes itself; a status that only reads green (the server
    in sync) is a success-tone notice too, and must stay when a live refresh brings it in."""
    assert "data-notice-autoclose" in _render_macro('{{ ui.success("Listo") }}')
    status = _render_macro('{{ ui.notice("El servidor coincide", "success") }}')
    assert 'data-notice-tone="success"' in status and "data-notice-autoclose" not in status


def test_notices_container_is_fixed_and_outside_main() -> None:
    text = client(EDITOR).get("/").text
    tag = re.search(r'<div id="notices"[^>]*>', text)
    assert tag and "fixed" in tag.group(0) and "z-50" in tag.group(0)
    assert text.index('id="notices"') < text.index("<main")


def test_app_js_places_notices_and_closes_successes() -> None:
    text = client(None).get("/static/app.js").text
    assert "data-form-notice" in text and "MutationObserver" in text and "5000" in text
    # Only the notices ui.success marks: never every success-tone notice a swap brings in.
    assert "[data-notice-autoclose]" in text and 'data-notice-tone="success"' not in text
    # Also the ones the page is drawn with (a notice after a redirect): no mutation brings them in.
    assert "scheduleClose(document.body)" in text


def test_mod_link_points_to_the_card_and_opens_the_panel() -> None:
    text = _render_macro('{{ ui.mod_link("AANobbMI", "Sodium <b>") }}')
    assert 'href="/mods/AANobbMI"' in text and 'hx-get="/mods/AANobbMI/panel"' in text
    assert 'hx-target="#mod-drawer-body"' in text and 'hx-swap="innerHTML"' in text
    assert "data-mod-link" in text and "Sodium &lt;b&gt;" in text and "?desde" not in text


def test_mod_link_inside_the_panel_asks_to_go_back() -> None:
    text = _render_macro('{{ ui.mod_link("AANobbMI", "Sodium", desde="P7dR8mSH") }}')
    assert 'hx-get="/mods/AANobbMI/panel?desde=P7dR8mSH"' in text
    assert 'href="/mods/AANobbMI"' in text
    # an invalid back id is dropped, not written into the URL
    assert "desde" not in _render_macro('{{ ui.mod_link("AANobbMI", "Sodium", desde="a/b") }}')


def test_mod_link_from_a_pack_carries_it_to_the_page_and_the_panel() -> None:
    pack = "0123456789abcdef01234567"
    text = _render_macro('{{ ui.mod_link("AANobbMI", "Sodium", pack="%s") }}' % pack)
    assert f'href="/mods/AANobbMI?pack={pack}"' in text and f'hx-get="/mods/AANobbMI/panel?pack={pack}"' in text
    both = _render_macro('{{ ui.mod_link("AANobbMI", "Sodium", desde="PREV", pack="%s") }}' % pack)
    assert f'hx-get="/mods/AANobbMI/panel?desde=PREV&amp;pack={pack}"' in both
    # Anything but an ObjectId is dropped, not written into the URL.
    for bad in ("zzz", pack + "0", "a\"><script>", ""):
        assert "pack" not in _render_macro('{{ ui.mod_link("AANobbMI", "Sodium", pack=%s) }}' % json.dumps(bad)), bad


def test_mod_link_with_an_invalid_id_is_plain_text() -> None:
    for bad in ("a/b", "", "x" * 201, "a b\"><script>"):
        text = _render_macro('{{ ui.mod_link(%s, "Sodium") }}' % json.dumps(bad))
        assert "<a" not in text and "Sodium" in text, bad


def test_base_has_the_empty_side_panel_with_a_labelled_close_button() -> None:
    text = client(EDITOR).get("/").text
    dialog = text[text.index('<dialog id="mod-drawer"'):]
    dialog = dialog[:dialog.index("</dialog>")]
    assert 'aria-label="Ficha del mod"' in dialog and "ml-auto" in dialog and "h-dvh" in dialog
    assert 'data-dialog-close' in dialog and "Cerrar" in dialog and "aria-label=\"Cerrar" in dialog
    body = re.search(r'<div id="mod-drawer-body"[^>]*>(.*?)</div>', dialog, re.S)
    assert body and body.group(1).strip() == "" and "overflow-y-auto" in dialog


def test_app_js_opens_the_panel_and_leaves_modified_clicks_to_the_browser() -> None:
    text = client(None).get("/static/app.js").text
    assert "mod-drawer" in text and "showModal" in text and "htmx:afterSwap" in text
    # htmx only ignores ctrl/cmd on boosted links and cancels the click of any other link, so a
    # ctrl, cmd or shift click, and the middle button, are stopped before htmx sees them: the
    # browser then follows the href itself.
    assert "data-mod-link" in text and "stopImmediatePropagation" in text
    for marker in ("ctrlKey", "metaKey", "shiftKey", "button"):
        assert marker in text, marker
    assert "focus()" in text


def test_sanitize_detail_hides_credentials_outside_urls_too() -> None:
    # Roadmap (round 1 review): a bearer token or a key=value credential outside a URL reached the
    # folding detail; it shows as [oculto].
    from src.web import sanitize_detail

    text = sanitize_detail("401: Authorization: Bearer ptlc_abc123 failed; token=xyz api_key=k1 x-api-key: k2 ok=1")
    assert "ptlc_abc123" not in text and "xyz" not in text and "k1" not in text and "k2" not in text
    assert "Bearer [oculto]" in text and "token=[oculto]" in text and "ok=1" in text


def test_linkify_links_only_http_urls_and_escapes_the_rest() -> None:
    # F29 (d-7985ee-58228f): a changelog stays plain text; only its http(s) URLs become links, in
    # another tab, without the referrer or a follow.
    from src.web import linkify

    html = str(linkify('Fixed <b>crash</b> (see https://github.com/x/y/issues/1). Docs: [here](https://x.org/a?b=1&c=2), '
                       'javascript:alert(1) ftp://f.org "https://q.org/"'))
    assert "&lt;b&gt;crash&lt;/b&gt;" in html and "<b>" not in html
    link = '<a href="https://github.com/x/y/issues/1" rel="noopener noreferrer nofollow" target="_blank"'
    assert link in html and 'issues/1</a>).' in html
    assert 'href="https://x.org/a?b=1&amp;c=2"' in html and "c=2</a>)," in html
    assert 'href="https://q.org/"' in html and "&#34;" in html
    assert "javascript:" in html and 'href="javascript' not in html and 'href="ftp' not in html
    assert html.count("<a ") == 3


def test_a_form_disables_its_submit_buttons_while_it_is_sent() -> None:
    # Roadmap (round 1 review): a form's submit button showed no busy state and a double submit was
    # possible. app.js disables them while an htmx form's request runs (and gives them back after),
    # and once a plain form is sent (after the browser read the button's value).
    import main
    from fastapi.testclient import TestClient

    text = TestClient(main.app, base_url="https://testserver").get("/static/app.js").text
    assert "htmx:beforeRequest" in text and "button[type=submit]" in text and "data-busy" in text
    assert "addEventListener('submit'" in text and "setTimeout" in text
