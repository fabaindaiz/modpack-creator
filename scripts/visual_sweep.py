"""La revisión visual del panel: siembra datos de demo, levanta la app y captura cada página.

No se corre a mano: lo hace `scripts/visual-sweep.sh`, que antes compila el CSS y levanta un
MongoDB descartable. Corre desde un directorio temporal fuera del repositorio, con configuración
falsa, para que `.env.yaml` nunca se cargue. Modrinth es un servidor local con el sodium grabado de
`tests/fixtures/modrinth/` (ver `ModrinthStub`); Pterodactyl, OIDC y los loaders, un puerto muerto. Sale con 1 si alguna página no da 200, desborda en
horizontal o escribe errores en la consola.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pymongo import AsyncMongoClient

from src.model import init_databases
from src.model.audit import AuditEvent, AuditTarget
from src.model.jobs import Job
from src.library.scan import LocalFile, ScanFile
from src.library.resolution import Lock, LockEntry, ResolveRequest, Target
from src.model.modpack import AvailableUpdate, ModpackBuild, ModpackBundle, ModpackReplacement, PackProject, ProjectOrigin, UpdateCheck
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.model.pterodactyl import PterodactylServer, ServerScan
from src.model.users.actor import ActorRef
from src.model.utils import MinecraftLoaders
from tests._actors import EDITOR, OPERATOR, session_for
from tests._session import session_cookie

SECRET = "sweep-secret"
WIDTHS = (360, 768, 1280)
LONG_NAME = ("Un nombre de servidor exageradamente largo para ver cómo se comporta la interfaz "
             "con textos de ciento veinte caracteres")
OLGA = ActorRef(iss=OPERATOR.iss, sub=OPERATOR.sub, name=OPERATOR.name)
EDDIE = ActorRef(iss=EDITOR.iss, sub=EDITOR.sub, name=EDITOR.name)


FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "modrinth"
STUB_PROJECT = "AANobbMI"  # sodium, from the recorded fixtures
# 1x1 transparent PNG: what a Modrinth icon answers with, so no request leaves the machine.
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f"
                    "0005fe02fea7d6050e0000000049454e44ae426082")


class ModrinthStub:
    """A tiny local Modrinth with the recorded sodium and nothing else, so the pages that need
    Modrinth (a build, adding a mod) can be driven offline. `/projects` answers the known ones of
    those asked (an unknown slug is simply absent, as in the API); `/project/<id>/version` answers
    sodium's versions as listed, with no `environment` (so the mod is "Sin decidir"), and a 503 for
    any other project: the version picker's retry fragment."""

    def __init__(self) -> None:
        self.project = json.loads((FIXTURES / "project-sodium.json").read_text())
        self.versions = json.loads((FIXTURES / "versions-sodium.json").read_text())
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                parts = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parts.query)
                path = parts.path.removeprefix("/v2/")
                if path == "projects":
                    asked = json.loads(query.get("ids", ["[]"])[0])
                    self._send(200, [stub.project] if {stub.project["id"], stub.project["slug"]} & set(asked) else [])
                elif path.startswith("project/") and path.endswith("/version") and \
                        path.split("/")[1] in (stub.project["id"], stub.project["slug"]):
                    self._send(200, [{**v, "environment": None, "version_type": "release"} for v in stub.versions
                                     if "fabric" in v["loaders"] and "26.3" in v["game_versions"]][:5])
                else:
                    self._send(503, {"error": "stub: unavailable"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v2/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def app_env(mongo_uri: str, modrinth_url: str) -> dict[str, str]:
    """The fake configuration of the app: Pterodactyl and OIDC point at a dead port, Modrinth at
    the local stub (`ModrinthStub`)."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
        "SESSION_SECRET": SECRET,
        "SESSION_HTTPS_ONLY": "false",
        "MONGODB_URI": mongo_uri,
        "PTERODACTYL_API_URL": "http://127.0.0.1:1/api/",
        # Nothing the sweep does may reach a real host: Modrinth is the local stub, the loader
        # metadata a dead port.
        "MODRINTH_API_URL": modrinth_url,
        "FABRIC_META_URL": "http://127.0.0.1:1/fabric/",
        "QUILT_META_URL": "http://127.0.0.1:1/quilt/",
        "OIDC_CLIENT_ID": "x",
        "OIDC_CLIENT_SECRET": "x",
        "OIDC_METADATA_URL": "http://127.0.0.1:1/.well-known/openid-configuration",
        "OIDC_ALLOWED_GROUPS": "modpack-admins",
        "OIDC_OPERATOR_GROUPS": "modpack-operators",
        "JOBS_WORKER": "off",
    }


def _entry(project_id: str, version_id: str) -> LockEntry:
    return LockEntry(project_id=project_id, origin="pack", version_id=version_id, version_number=version_id,
                     file=None, environment="client_and_server", server="include", reason="en el pack",
                     requested_by=[])


async def seed_builds(bundle: ModpackBundle) -> None:
    """Two builds whose locks differ, and the changelogs of what arrives in the catalog, so the
    comparison page needs no network: one long, one with HTML, one without text."""
    request = ResolveRequest(target=Target(game_version="26.3", loader="fabric", channels=frozenset({"release"})),
                             projects=[])
    locks = {1: [_entry("sodium", "sod-1"), _entry("gone-mod", "gone-1"), _entry("steady", "st-1")],
             2: [_entry("sodium", "sod-2"), _entry("new-mod", "new-1"), _entry("steady", "st-1")]}
    for number, entries in locks.items():
        await ModpackBuild(bundle_id=bundle.id, number=number, created_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
                           loader_version="0.16.0", request=request,
                           lock=Lock(entries=entries, problems=[], suggestions=[], needs_projects=set(),
                                     needs_types=set(), needs_versions=set())).insert()
    texts = {"sod-2": "## Arreglos\n" + "- un cambio con un texto bastante largo para ver cómo se parte la línea\n" * 120,
             "new-1": "<script>alert(1)</script> <b>no es HTML</b> " + "x" * 5000, "gone-1": ""}
    for version_id, text in texts.items():
        await ModrinthVersion.parse(
            id=version_id, project_id=version_id.split("-")[0], author_id="a", name=version_id,
            version_number=version_id, version_type="release", status="listed", downloads=1,
            game_versions=["26.3"], loaders=["fabric"], featured=False, date_published="2026-01-01T00:00:00Z",
            dependencies=[], files=[], changelog=text).insert()


async def seed(mongo_uri: str) -> dict[str, str]:
    """Demo data through the app's own models; returns the ids the sweep visits."""
    now = datetime.now(timezone.utc)
    client: AsyncMongoClient = AsyncMongoClient(mongo_uri)
    await init_databases(client)
    big = ModpackBundle(
        name="Survival Plus — pack con un nombre bastante largo para probar el ancho",
        description="Pack principal", version="26.3", loader=MinecraftLoaders.fabric,
        projects=[PackProject(project_id=f"mod-{i:03d}-{'x' * (i % 40)}", origin=ProjectOrigin.admin if i % 7 == 0 else ProjectOrigin.pack)
                  for i in range(300)],
        server_decisions={"mod-001-x": True},
        pins={"mod-001-x": "v4PSXean"},
        local_files=[LocalFile(filename="custom-datapack-helper-con-un-nombre-largo.jar", sha512="d" * 128, sha1="e" * 40, size=2_621_440)])
    small = ModpackBundle(name="Creativo", description="", version="26.2", loader=MinecraftLoaders.neoforge)
    # Two packs for the interactions: one with a mod whose side Modrinth leaves open (the build
    # dialog), one empty (adding by slug).
    pending = ModpackBundle(name="Con pendientes", description="", version="26.3", loader=MinecraftLoaders.fabric,
                            projects=[PackProject(project_id=STUB_PROJECT, origin=ProjectOrigin.admin)])
    empty = ModpackBundle(name="Para agregar", description="", version="26.3", loader=MinecraftLoaders.fabric)
    await big.insert()
    await small.insert()
    await pending.insert()
    await empty.insert()
    # The catalog knows the first mods of the big pack, with an icon (the sweep answers the CDN
    # itself) and a card: description, categories, loaders, versions and links.
    sodium = json.loads((FIXTURES / "project-sodium.json").read_text())
    for i in (1, 2, 3):
        await ModrinthProject.parse(**{**sodium, "id": f"mod-{i:03d}-{'x' * i}", "slug": f"mod-{i}",
                                       "title": f"Mod de ejemplo {i} con un título algo largo",
                                       "downloads": 12_345 * i}).insert()
    await seed_builds(big)
    big.latest_build = big.recommended_build = 2
    await big.save()
    # A check of the recommended build: two updates (their changelogs are in the catalog, so the page
    # needs no network; sod-2's is the long one) and a pinned one.
    await ModrinthVersion.parse(
        id="lith-2", project_id="lith", author_id="a", name="lith-2", version_number="0.15.2", version_type="release",
        status="listed", downloads=1, game_versions=["26.3"], loaders=["fabric"], featured=False,
        date_published="2026-01-01T00:00:00Z", dependencies=[], files=[], changelog="Un arreglo pequeño").insert()
    await UpdateCheck(bundle_id=big.id, build_number=2, checked_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
                      updates=[AvailableUpdate(project_id="sodium", title="Sodium", from_version="sod-1", to_version="sod-2",
                                               from_version_id="sod-1", to_version_id="sod-2"),
                               AvailableUpdate(project_id="lith", title="Lithium con un nombre bastante largo",
                                               from_version="0.15.1", to_version="0.15.2",
                                               from_version_id="lith-1", to_version_id="lith-2"),
                               AvailableUpdate(project_id="iris", title="Iris", from_version="1.0", to_version="1.1",
                                               pinned=True)]).insert()
    await ModpackReplacement(project_id="optifine", replacement="sodium").insert()
    servers = []
    for i, (name, bundle) in enumerate([("Survival", big), (LONG_NAME, big), ("Creativo", small)]):
        server = PterodactylServer(
            identifier=f"ab{i}cd", internal_id=i, uuid=f"uuid-{i}", name=name, node="node-1", description="",
            status=None, is_suspended=False, is_installing=False, is_transferring=False, bundle_id=str(bundle.id))
        await server.insert()
        assert server.id is not None
        servers.append(server)
        # Three jars are the ones of the builds' locks (sodium changes, one goes, one stays), so the
        # update preview of the server has something to show.
        known = {1: ("sodium", "sod-1"), 2: ("gone-mod", "gone-1"), 3: ("steady", "st-1")}
        files = [ScanFile(path=f"/mods/mod-{j}.jar", size=1000 + j, modified_at=now,
                          status="identified" if j % 5 else "unknown", sha1="a" * 40, sha512="b" * 128,
                          project_id=known[j][0] if j in known else f"mod-{j:03d}" if j % 5 else None,
                          version_id=known[j][1] if j in known else "v1" if j % 5 else None)
                 for j in range(12)]
        await ServerScan(server_id=server.id, directory="/mods", scanned_at=now - timedelta(hours=i), full=False,
                         files=files, drift="2 jars de más y 1 versión distinta" if i == 0 else None).insert()
    # A server without a pack whose scan identified jars of the catalog (fabric, 26.3): the form of
    # "Nuevo modpack para este servidor" deduces the loader and the version from them.
    bare = PterodactylServer(
        identifier="ab9cd", internal_id=9, uuid="uuid-9", name="Sin modpack", node="node-1", description="",
        status=None, is_suspended=False, is_installing=False, is_transferring=False, bundle_id=None)
    await bare.insert()
    assert bare.id is not None
    await ServerScan(
        server_id=bare.id, directory="/mods", scanned_at=now, full=False,
        files=[ScanFile(path=f"/mods/{v}.jar", size=1000, modified_at=now, status="identified", sha1="a" * 40,
                        sha512="b" * 128, project_id=v.split("-")[0], version_id=v) for v in ("sod-2", "new-1", "gone-1")]
        + [ScanFile(path="/mods/raro.jar", size=1000, modified_at=now, status="unknown", sha1="a" * 40, sha512="b" * 128)],
        drift=None).insert()
    survival = str(servers[0].id)
    plan = {"build": 2, "bundle_id": str(big.id), "bundle": big.name, "remove": ["/mods/old-1.jar", "/mods/old-2.jar"],
            "install": [{"project_id": "lithium", "filename": "lithium-fabric-0.15.1+mc26.3.jar",
                         "url": "https://cdn.modrinth.com/x", "sha512": "c" * 128, "size": 1}]}
    failed = Job(kind="server_update", key=f"server:{servers[1].id}",
                 payload={"server_id": str(servers[1].id), "build": 3, "dry_run": False}, status="failed",
                 active=False, error="the server did not stay up for 300 s", created_at=now - timedelta(hours=3),
                 finished_at=now - timedelta(hours=2), requested_by=OLGA,
                 checkpoint={"preflight": plan, "swap": {}, "backup": {"uuid": "b"}})
    jobs = [
        Job(kind="server_update", key=f"server:{survival}",
            payload={"server_id": survival, "build": 2, "dry_run": True}, status="done", active=False,
            created_at=now - timedelta(minutes=10), finished_at=now - timedelta(minutes=9), requested_by=OLGA,
            checkpoint={"preflight": plan, "log": {"lines": ["stage lithium-fabric-0.15.1+mc26.3.jar", "save-all",
                                                              "stop", "backup update-x"]}}),
        Job(kind="server_scan", key=f"server:{servers[2].id}",
            payload={"server_id": str(servers[2].id), "full": False}, status="running", active=True, step="scan",
            created_at=now - timedelta(minutes=1), requested_by=EDDIE),
        Job(kind="server_scan", key=f"server:{servers[0].id}-queued", payload={"server_id": survival}, status="queued",
            active=True, created_at=now - timedelta(seconds=20), requested_by=EDDIE),
        failed,
        Job(kind="server_scan", key=None, payload={"server_id": survival}, status="done", active=False,
            created_at=now - timedelta(days=1), finished_at=now - timedelta(days=1)),
    ]
    for job in jobs:
        await job.insert()
    for k in range(60):
        await AuditEvent(at=now - timedelta(minutes=k), actor=OLGA if k % 3 == 0 else EDDIE,
                         action=["build.create", "bundle.project.add", "server.update", "server.scan.queue"][k % 4],
                         target=AuditTarget(kind="bundle" if k % 2 else "server", id=str(big.id)),
                         details={"refs": ["sodium", "lithium", "iris"], "origin": "admin"} if k % 4 == 1
                         else {"number": k},
                         request_id=f"req-{k:04d}").insert()
    await client.close()
    return {"server": survival, "server_new": str(bare.id), "bundle": str(big.id), "pending": str(pending.id),
            "empty": str(empty.id), "job": str(failed.id), "dryrun": str(jobs[0].id)}


def wait_ready(base: str, app: "subprocess.Popen[bytes]") -> None:
    for _ in range(80):
        if app.poll() is not None:
            raise RuntimeError("la app terminó antes de responder; ver app.log")
        try:
            with urllib.request.urlopen(base + "/health/live", timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("la app no respondió a /health/live")


def shoot(base: str, out: Path, ids: dict[str, str], cookies: dict[str, str]) -> dict[str, dict[str, Any]]:
    # Imported here: playwright lives only in the `visual` hatch env, not in the gate's.
    from playwright.sync_api import sync_playwright

    pages = {"home": "/", "modpacks": "/modpacks", "modpack": f"/modpacks/{ids['bundle']}",
             "compare": f"/modpacks/{ids['bundle']}/builds/compare?de=1&a=2",
             "updates": f"/modpacks/{ids['bundle']}/actualizaciones",
             "modpack-pending": f"/modpacks/{ids['pending']}",
             "replacements": "/replacements", "servers": "/servers", "server": f"/servers/{ids['server']}",
             "server-new": f"/servers/{ids['server_new']}",
             "jobs": "/jobs", "job": f"/jobs/{ids['job']}", "dryrun": f"/jobs/{ids['dryrun']}", "audit": "/audit"}
    report: dict[str, dict[str, Any]] = {}
    chromium = os.environ.get("PLAYWRIGHT_CHROMIUM") or None
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium)
        try:
            for who in ("operator", "editor"):
                for scheme in ("light", "dark"):
                    for width in WIDTHS:
                        if who == "editor" and (scheme == "dark" or width != 1280):
                            continue
                        ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
                        ctx.add_cookies([{"name": "session", "value": cookies[who], "url": base}])
                        # Icons come from Modrinth's CDN: answered here, nothing leaves the machine.
                        ctx.route("https://cdn.modrinth.com/**",
                                  lambda route: route.fulfill(status=200, content_type="image/png", body=PNG))
                        page = ctx.new_page()
                        errors: list[str] = []
                        page.on("console", lambda m, errors=errors: errors.append(m.text) if m.type == "error" else None)
                        for name, path in pages.items():
                            response = page.goto(base + path)
                            if not page.url.split("?")[0].endswith(path.split("?")[0]):
                                raise RuntimeError(f"{path} terminó en {page.url}: una redirección no es una página revisada")
                            page.wait_for_load_state("networkidle")  # lazy fragments (change previews) swap in
                            page.wait_for_timeout(300)
                            overflow = page.evaluate(
                                "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                            key = f"{who}-{scheme}-{width}-{name}"
                            page.screenshot(path=str(out / f"{key}.png"), full_page=width != 1280 or name != "modpack")
                            report[key] = {"status": response.status if response else None, "overflow_px": overflow,
                                           "console_errors": list(errors)}
                            errors.clear()
                        report.update(shoot_open_card(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
                        if who == "operator":
                            report.update(shoot_update_dialog(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
                            report.update(shoot_build_dialog(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
                            if scheme == "light" and width == 1280:
                                # These change data (a mod is added): once, not in every context.
                                report.update(check_notices(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
                        ctx.close()
        finally:
            browser.close()
    return report


def _report(response: Any, errors: list[str], problems: list[str]) -> dict[str, Any]:
    entry = {"status": response.status if response else None, "overflow_px": 0, "console_errors": list(errors),
             "problems": problems}
    errors.clear()
    return entry


def shoot_open_card(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
                    errors: list[str]) -> dict[str, dict[str, Any]]:
    """The first mod's card opened (a <details>, so no JS of ours): the catalog data shows, and the
    version picker, which loads on open (`toggle once`), answers its retry fragment because the
    stub has no versions for that mod, never a console error."""
    response = page.goto(base + f"/modpacks/{ids['bundle']}")
    page.wait_for_load_state("networkidle")
    first = page.locator("#mods details").first
    first.locator("summary").click()
    page.wait_for_selector("#mods details[open] [id^=versions-] :text('Modrinth no respondió')", timeout=10_000)
    key = f"{prefix}-modpack-card"
    page.screenshot(path=str(out / f"{key}.png"))
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    problems = [p for p, bad in (("la ficha no se abrió", not first.evaluate("d => d.open")),
                                 ("la ficha no muestra la descripción", not first.locator("p.break-words").count()),
                                 ("la página desborda", overflow > 0)) if bad]
    return {key: _report(response, errors, problems)}


def shoot_build_dialog(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
                       errors: list[str]) -> dict[str, dict[str, Any]]:
    """"Crear build" on a pack with a mod left undecided: the stub resolves it, the build is
    refused, and the answer is the decision dialog in #build-dialog. Submitted with no decision
    it is answered again and must stay open: the form that sent it is gone from the page by then
    (app.js closes the dialog of the form that sent the request, not the new one)."""
    response = page.goto(base + f"/modpacks/{ids['pending']}")
    page.wait_for_load_state("networkidle")
    page.click('#builds button[hx-post$="/builds"]')
    page.wait_for_selector("#build-dialog dialog[open]", timeout=10_000)
    page.wait_for_timeout(300)
    opened = page.evaluate("document.querySelector('#build-dialog dialog')?.open === true")
    page.screenshot(path=str(out / f"{prefix}-build-dialog.png"))
    page.locator("#build-dialog dialog button[type=submit]").click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)
    stays = page.evaluate("document.querySelector('#build-dialog dialog')?.open === true")
    key = f"{prefix}-build-dialog-again"
    page.screenshot(path=str(out / f"{key}.png"))
    problems = [p for p, bad in (("el diálogo de decisiones no se abrió", not opened),
                                 ("el diálogo se cerró al enviarlo sin decidir", not stays)) if bad]
    return {key: _report(response, errors, problems)}


def check_notices(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
                  errors: list[str]) -> dict[str, dict[str, Any]]:
    """The two places of a notice: an error goes right after the form that sent it (inside
    `[data-form-notice]`, not in the corner), and a success in the corner goes by itself in 6 s."""
    problems: list[str] = []
    response = page.goto(base + f"/modpacks/{ids['empty']}")
    page.wait_for_load_state("networkidle")
    form = page.locator("form:has(#add-ref)")
    page.fill("#add-ref", "no-existe-zz")
    form.locator("button[type=submit]").click()
    page.wait_for_selector("[data-form-notice] [data-notice]", timeout=10_000)
    page.screenshot(path=str(out / f"{prefix}-notice-form.png"))
    # The 404 we asked for is logged by the browser and by htmx: expected, anything else is not.
    errors[:] = [e for e in errors if "404" not in e]
    after_form = page.evaluate("""() => {
        const form = document.querySelector('#add-ref').closest('form');
        const slot = form.nextElementSibling;
        return !!slot && slot.hasAttribute('data-form-notice') && slot.textContent.includes('no-existe-zz');
    }""")
    if not after_form:
        problems.append("el aviso de error no quedó junto al formulario")
    if page.locator("#notices [data-notice]").count():
        problems.append("el aviso de error también salió en la esquina")

    page.fill("#add-ref", "sodium")
    form.locator("button[type=submit]").click()
    page.wait_for_selector('#notices [data-notice-tone="success"]', timeout=10_000)
    page.screenshot(path=str(out / f"{prefix}-notice-success.png"))
    started = time.monotonic()
    try:
        page.wait_for_selector('#notices [data-notice-tone="success"]', state="detached", timeout=6_000)
    except Exception:
        problems.append("el aviso de éxito siguió a los 6 s")
    print(f"  aviso de éxito: se fue a los {time.monotonic() - started:.1f} s", file=sys.stderr)
    return {f"{prefix}-notices": _report(response, errors, problems)}


def shoot_update_dialog(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
                        errors: list[str]) -> dict[str, dict[str, Any]]:
    """The direct update dialog, opened: its preview loads only then (`intersect once`), and the
    dialog must stay open when the preview answers (app.js closes it only for its own form)."""
    path = f"/servers/{ids['server']}"
    response = page.goto(base + path)
    page.wait_for_load_state("networkidle")
    loaded_before = page.evaluate("!document.querySelector('#update-preview')?.textContent.includes('Cargando cambios')")
    page.click('[data-dialog-open="update-dialog-direct"]')
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    still_open = page.evaluate("document.getElementById('update-dialog-direct').open")
    loaded = page.evaluate("!document.querySelector('#update-preview').textContent.includes('Cargando cambios')")
    key = f"{prefix}-server-dialog"
    page.screenshot(path=str(out / f"{key}.png"))
    problems = [p for p, bad in (("la vista previa cargó con la página", loaded_before),
                                 ("el diálogo se cerró", not still_open),
                                 ("la vista previa no cargó al abrir", not loaded)) if bad]
    entry = {"status": response.status if response else None, "overflow_px": 0, "console_errors": list(errors),
             "problems": problems}
    errors.clear()
    return {key: entry}


def main() -> int:
    # The app runs from this directory and must never load the repository's `.env.yaml`.
    repo = Path(__file__).resolve().parent.parent
    if Path.cwd().resolve() == repo or Path(".env.yaml").exists():
        print("visual_sweep.py: se corre con `scripts/visual-sweep.sh`, desde un directorio fuera del "
              "repositorio y sin `.env.yaml`", file=sys.stderr)
        return 2
    mongo_uri, out_arg = sys.argv[1], sys.argv[2]
    out = Path(out_arg)
    out.mkdir(parents=True, exist_ok=True)
    ids = asyncio.run(seed(mongo_uri))
    cookies = {"operator": session_cookie(session_for(OPERATOR), secret=SECRET),
               "editor": session_cookie(session_for(EDITOR), secret=SECRET)}
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(out / "app.log", "wb")
    stub = ModrinthStub()
    stub.start()
    app = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port),
                            "--no-access-log"], env=app_env(mongo_uri, stub.url), stdout=log, stderr=subprocess.STDOUT)
    try:
        wait_ready(base, app)
        report = shoot(base, out, ids, cookies)
    finally:
        stub.stop()
        app.terminate()
        try:
            app.wait(timeout=10)
        except subprocess.TimeoutExpired:
            app.kill()
        log.close()
    (out / "report.json").write_text(json.dumps(report, indent=1))
    bad = {k: v for k, v in report.items()
           if v["status"] != 200 or v["overflow_px"] > 0 or v["console_errors"] or v.get("problems")}
    print(f"{len(report)} capturas; {len(bad)} con problemas")
    for key, value in bad.items():
        print(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
