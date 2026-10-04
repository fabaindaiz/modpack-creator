"""La revisión visual del panel: siembra datos de demo, levanta la app y captura cada página.

No se corre a mano: lo hace `scripts/visual-sweep.sh`, que antes compila el CSS y levanta un
MongoDB descartable. Corre desde un directorio temporal fuera del repositorio, con configuración
falsa, para que `.env.yaml` nunca se cargue. Sale con 1 si alguna página no da 200, desborda en
horizontal o escribe errores en la consola.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pymongo import AsyncMongoClient

from src.model import init_databases
from src.model.audit import AuditEvent, AuditTarget
from src.model.jobs import Job
from src.library.scan import LocalFile, ScanFile
from src.library.resolution import Lock, LockEntry, ResolveRequest, Target
from src.model.modpack import ModpackBuild, ModpackBundle, ModpackReplacement, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthVersion
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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def app_env(mongo_uri: str) -> dict[str, str]:
    """The fake configuration of the app: Pterodactyl and OIDC point at a dead port."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
        "SESSION_SECRET": SECRET,
        "SESSION_HTTPS_ONLY": "false",
        "MONGODB_URI": mongo_uri,
        "PTERODACTYL_API_URL": "http://127.0.0.1:1/api/",
        # Nothing the sweep does may reach a real host: Modrinth and the loader metadata too.
        "MODRINTH_API_URL": "http://127.0.0.1:1/v2/",
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
    await big.insert()
    await small.insert()
    await seed_builds(big)
    big.latest_build = big.recommended_build = 2
    await big.save()
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
    return {"server": survival, "bundle": str(big.id), "job": str(failed.id), "dryrun": str(jobs[0].id)}


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
             "replacements": "/replacements", "servers": "/servers", "server": f"/servers/{ids['server']}",
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
                        page = ctx.new_page()
                        errors: list[str] = []
                        page.on("console", lambda m, errors=errors: errors.append(m.text) if m.type == "error" else None)
                        for name, path in pages.items():
                            response = page.goto(base + path)
                            if not page.url.split("?")[0].endswith(path.split("?")[0]):
                                raise RuntimeError(f"{path} terminó en {page.url}: una redirección no es una página revisada")
                            page.wait_for_timeout(300)
                            overflow = page.evaluate(
                                "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                            key = f"{who}-{scheme}-{width}-{name}"
                            page.screenshot(path=str(out / f"{key}.png"), full_page=width != 1280 or name != "modpack")
                            report[key] = {"status": response.status if response else None, "overflow_px": overflow,
                                           "console_errors": list(errors)}
                            errors.clear()
                        ctx.close()
        finally:
            browser.close()
    return report


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
    app = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port),
                            "--no-access-log"], env=app_env(mongo_uri), stdout=log, stderr=subprocess.STDOUT)
    try:
        wait_ready(base, app)
        report = shoot(base, out, ids, cookies)
    finally:
        app.terminate()
        try:
            app.wait(timeout=10)
        except subprocess.TimeoutExpired:
            app.kill()
        log.close()
    (out / "report.json").write_text(json.dumps(report, indent=1))
    bad = {k: v for k, v in report.items() if v["status"] != 200 or v["overflow_px"] > 0 or v["console_errors"]}
    print(f"{len(report)} capturas; {len(bad)} con problemas")
    for key, value in bad.items():
        print(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
