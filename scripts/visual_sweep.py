"""La revisión visual del panel: siembra datos de demo, levanta la app y captura cada página.

No se corre a mano: lo hace `scripts/visual-sweep.sh`, que antes compila el CSS y levanta un
MongoDB descartable. Corre desde un directorio temporal fuera del repositorio, con configuración
falsa, para que `.env.yaml` nunca se cargue. Modrinth es un servidor local con el sodium grabado de
`tests/fixtures/modrinth/` (ver `ModrinthStub`); Pterodactyl, OIDC y los loaders, un puerto muerto. Sale con 1 si alguna página no da 200, desborda en
horizontal, escribe errores en la consola o viola la CSP, o si falla una de sus verificaciones de
interacción (la ficha en el panel, las pestañas de Mods, el diálogo de build y sus acciones).
"""
import asyncio
import hashlib
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
from typing import Any, Optional

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
# Where the mod's card, the tabs and the build dialog with problems are checked and shot (the
# operator): narrow and wide in light, wide in dark.
MOD_CARD_CONTEXTS = {("light", 360), ("light", 1280), ("dark", 1280)}
# On every page: a CSP violation (an inline style or script the panel must not have) is written as
# a console error, so it fails the run like any other. Playwright adds it outside the page's CSP.
# The sweep waits with selectors, never `wait_for_function`: Playwright polls that one with an eval
# the panel's CSP refuses (a violation of the sweep's own, and a wait that never ends).
CSP_LISTENER = """document.addEventListener('securitypolicyviolation', (e) => {
    console.error('CSP: ' + e.violatedDirective + ' ' + (e.blockedURI || '') + ' ' + (e.sourceFile || '') + ':' + e.lineNumber);
});"""
LONG_NAME = ("Un nombre de servidor exageradamente largo para ver cómo se comporta la interfaz "
             "con textos de ciento veinte caracteres")
OLGA = ActorRef(iss=OPERATOR.iss, sub=OPERATOR.sub, name=OPERATOR.name)
EDDIE = ActorRef(iss=EDITOR.iss, sub=EDITOR.sub, name=EDITOR.name)


FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "modrinth"
STUB_PROJECT = "AANobbMI"  # sodium, from the recorded fixtures
# 1x1 transparent PNG: what a Modrinth icon answers with, so no request leaves the machine.
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f"
                    "0005fe02fea7d6050e0000000049454e44ae426082")


# Three made-up projects, built on sodium's recorded data, for the problems of a resolution:
# NEEDS_BETA (a release that fits, side left open) requires BETA_DEP, which has only a beta that
# fits (an `unresolvable` dependency with an alternative: "Usar la beta …"); PINNED_GONE is pinned
# to GONE_VERSION, which Modrinth no longer has (the pin's problem: "Quitar la fijación", "Fijar otra").
NEEDS_BETA, BETA_DEP, PINNED_GONE, GONE_VERSION = "needs-beta", "beta-dep", "pinned-gone", "pg-old"


def _made_up(project: dict[str, Any], version: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The made-up projects and their versions, in Modrinth's shape (sodium's, renamed)."""
    def make_version(version_id: str, project_id: str, number: str, channel: str, environment: Optional[str],
                     dependencies: list[dict[str, Any]]) -> dict[str, Any]:
        filename = f"{project_id}-{number}.jar"
        file = {**version["files"][0], "id": f"file-{version_id}", "filename": filename,
                "url": f"https://cdn.modrinth.com/data/{project_id}/versions/{version_id}/{filename}",
                "hashes": {"sha1": hashlib.sha1(version_id.encode()).hexdigest(),
                           "sha512": hashlib.sha512(version_id.encode()).hexdigest()}}
        return {**version, "id": version_id, "project_id": project_id, "name": f"{project_id} {number}",
                "version_number": number, "version_type": channel, "environment": environment,
                "dependencies": dependencies, "files": [file], "changelog": "", "game_versions": ["26.3"],
                "loaders": ["fabric"]}

    versions = [
        make_version("nb-1", NEEDS_BETA, "1.4.0", "release", None,
                     [{"project_id": BETA_DEP, "version_id": None, "file_name": None, "dependency_type": "required"}]),
        make_version("bd-1b", BETA_DEP, "2.0.0-beta.1", "beta", "client_and_server", []),
        make_version("pg-2", PINNED_GONE, "3.1.0", "release", "client_and_server", []),
    ]
    titles = {NEEDS_BETA: ("Necesita una beta", "Un mod que pide una dependencia que solo tiene beta."),
              BETA_DEP: ("Dependencia solo en beta", "Solo publica betas para esta versión de Minecraft."),
              PINNED_GONE: ("Fijado a una versión borrada", "Su versión fijada ya no está en Modrinth.")}
    projects = [{**project, "id": project_id, "slug": project_id, "title": title, "description": description,
                 "versions": [v["id"] for v in versions if v["project_id"] == project_id]}
                for project_id, (title, description) in titles.items()]
    return projects, versions


class ModrinthStub:
    """A tiny local Modrinth with the recorded sodium and the made-up projects (`_made_up`), so the
    pages that need Modrinth (a build, adding a mod, a problem's actions) can be driven offline.
    `/projects` and `/versions` answer the known ones of those asked (an unknown one is simply
    absent, as in the API: GONE_VERSION is gone); `/project/<id>/version` answers sodium's versions
    as listed, with no `environment` (so the mod is "Sin decidir"), the made-up ones' versions, and a
    503 for any other project: the version picker's retry fragment."""

    def __init__(self) -> None:
        self.project = json.loads((FIXTURES / "project-sodium.json").read_text())
        self.versions = json.loads((FIXTURES / "versions-sodium.json").read_text())
        self.extra_projects, self.extra_versions = _made_up(self.project, self.versions[0])
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
                ref = path.split("/")[1] if path.count("/") >= 1 else ""
                made_up = [v for v in stub.extra_versions if v["project_id"] == ref]
                if path == "projects":
                    asked = set(json.loads(query.get("ids", ["[]"])[0]))
                    self._send(200, [p for p in [stub.project, *stub.extra_projects] if {p["id"], p["slug"]} & asked])
                elif path == "versions":
                    asked = set(json.loads(query.get("ids", ["[]"])[0]))
                    self._send(200, [v for v in stub.extra_versions if v["id"] in asked])
                elif path.startswith("version/"):
                    found = [v for v in stub.extra_versions if v["id"] == ref]
                    if found:
                        self._send(200, found[0])
                    else:
                        self._send(404, {"error": "not_found"})
                elif path.startswith("project/") and path.endswith("/version") and \
                        ref in (stub.project["id"], stub.project["slug"]):
                    self._send(200, [{**v, "environment": None, "version_type": "release"} for v in stub.versions
                                     if "fabric" in v["loaders"] and "26.3" in v["game_versions"]][:5])
                elif path.startswith("project/") and path.endswith("/version") and made_up:
                    self._send(200, made_up)
                elif path == "tag/game_version":  # F09: the releases a new pack chooses from
                    self._send(200, [{"version": v, "version_type": t, "date": "2026-01-01T00:00:00Z", "major": False}
                                     for v, t in (("26.3", "release"), ("26.3-pre1", "snapshot"), ("26.2", "release"))])
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
        # On: builds, imports and the catalog are jobs (spec 2026-10-09-economia-de-requests). The seeded
        # jobs are kept out of its reach (a live lease, a later availability).
        "JOBS_WORKER": "on",
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
    # Two packs with the problems of the made-up projects (ModrinthStub) and two mods to decide
    # (sodium and NEEDS_BETA): one only looked at (the tabs, the cards, the dialog in every
    # context), one whose problems are acted on (once).
    def with_problems(name: str) -> ModpackBundle:
        return ModpackBundle(name=name, description="", version="26.3", loader=MinecraftLoaders.fabric,
                             projects=[PackProject(project_id=p, origin=ProjectOrigin.admin)
                                       for p in (STUB_PROJECT, NEEDS_BETA, PINNED_GONE)],
                             pins={PINNED_GONE: GONE_VERSION})
    problems, fix = with_problems("Con problemas"), with_problems("Para arreglar")
    await big.insert()
    await small.insert()
    await pending.insert()
    await empty.insert()
    await problems.insert()
    await fix.insert()
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
    # sod-1, the version the servers run, published after sod-2: the comparison, the updates and the
    # server's update dialog mark sodium as a step back (F27).
    await ModrinthVersion.parse(
        id="sod-1", project_id="sod", author_id="a", name="sod-1", version_number="sod-1", version_type="release",
        status="listed", downloads=1, game_versions=["26.3"], loaders=["fabric"], featured=False,
        date_published="2026-02-01T00:00:00Z", dependencies=[], files=[], changelog="").insert()
    # A pack whose saved pin goes back from its build (F27): the list marks it, and pinning it again
    # opens the "Fijar igual" dialog.
    downgrade = ModpackBundle(name="Con baja", description="", version="26.3", loader=MinecraftLoaders.fabric,
                              projects=[PackProject(project_id="jei", origin=ProjectOrigin.admin)], pins={"jei": "jei-1951"})
    await downgrade.insert()
    await ModrinthProject.parse(**{**sodium, "id": "jei", "slug": "jei", "title": "Just Enough Items"}).insert()
    for version_id, number, day in (("jei-1957", "19.57.0.450", 20), ("jei-1951", "19.51.0.418", 10)):
        await ModrinthVersion.parse(
            id=version_id, project_id="jei", author_id="a", name=number, version_number=number, version_type="release",
            status="listed", downloads=1, game_versions=["26.3"], loaders=["fabric"], featured=False,
            date_published=f"2025-09-{day}T00:00:00Z", dependencies=[], files=[], changelog="").insert()
    await ModpackBuild(bundle_id=downgrade.id, number=1, created_at=now, loader_version="0.16.0",  # type: ignore[arg-type]
                       request=ResolveRequest(target=Target(game_version="26.3", loader="fabric", channels=frozenset({"release"})),
                                              projects=[]),
                       lock=Lock(entries=[_entry("jei", "jei-1957").model_copy(update={"version_number": "19.57.0.450"})],
                                 problems=[], suggestions=[], needs_projects=set(), needs_types=set(),
                                 needs_versions=set())).insert()
    downgrade.latest_build = downgrade.recommended_build = 1
    await downgrade.save()
    # A check of the recommended build: two updates (their changelogs are in the catalog, so the page
    # needs no network; sod-2's is the long one) and a pinned one.
    await ModrinthVersion.parse(
        id="lith-2", project_id="lith", author_id="a", name="lith-2", version_number="0.15.2", version_type="release",
        status="listed", downloads=1, game_versions=["26.3"], loaders=["fabric"], featured=False,
        date_published="2026-01-01T00:00:00Z", dependencies=[], files=[], changelog="Un arreglo pequeño").insert()
    await UpdateCheck(bundle_id=big.id, build_number=2, checked_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
                      updates=[AvailableUpdate(project_id="sodium", title="Sodium", from_version="sod-1", to_version="sod-2",
                                               from_version_id="sod-1", to_version_id="sod-2", kind="leaves_beta"),
                               AvailableUpdate(project_id="lith", title="Lithium con un nombre bastante largo",
                                               from_version="0.15.1", to_version="0.15.2",
                                               from_version_id="lith-1", to_version_id="lith-2", kind="beta"),
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
            created_at=now - timedelta(minutes=1), requested_by=EDDIE,
            # The sweep's worker runs (the build is a job): a live lease keeps it from taking this one.
            owner="seed", lease_until=now + timedelta(days=1)),
        Job(kind="server_scan", key=f"server:{servers[0].id}-queued", payload={"server_id": survival}, status="queued",
            active=True, created_at=now - timedelta(seconds=20), requested_by=EDDIE,
            available_at=now + timedelta(days=1)),  # shown queued, never taken by the sweep's worker
        failed,
        Job(kind="server_scan", key=None, payload={"server_id": survival}, status="done", active=False,
            created_at=now - timedelta(days=1), finished_at=now - timedelta(days=1)),
        # The server's last import (T1): its row says what it did; the server's page, what waits.
        Job(kind="server_import", key=f"server:{survival}", payload={"server_id": survival}, status="done", active=False,
            created_at=now - timedelta(minutes=30), finished_at=now - timedelta(minutes=29), requested_by=OLGA,
            checkpoint={"import": {
                "added": ["sodium", "lithium"], "local": ["/mods/own-helper.jar"], "too_large": [],
                "on_server": ["sodium", "lithium"], "channel_exceptions": {"lithium": "beta"},
                "candidates": [{"path": "/mods/create-addon-con-un-nombre-largo-1.2.3.jar", "sha512": "f" * 128,
                                "metadata": None,
                                "candidates": [{"project_id": "iris", "slug": "iris", "title": "Iris Shaders",
                                                "via": "slug"}]}]}}),
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
            "empty": str(empty.id), "job": str(failed.id), "dryrun": str(jobs[0].id), "problems": str(problems.id),
            "fix": str(fix.id), "import_job": str(jobs[-1].id), "downgrade": str(downgrade.id)}


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
             "modpack-pending": f"/modpacks/{ids['pending']}", "modpack-downgrade": f"/modpacks/{ids['downgrade']}",
             # Batch A (2026-10-13): the filter bar with a filter on, and the suggestions tab.
             "modpack-filtered": f"/modpacks/{ids['bundle']}?vista=todos&lado=servidor&nombre=s",
             "modpack-suggestions": f"/modpacks/{ids['bundle']}?vista=sugerencias",
             "replacements": "/replacements", "servers": "/servers", "server": f"/servers/{ids['server']}",
             "server-new": f"/servers/{ids['server_new']}",
             "jobs": "/jobs", "job": f"/jobs/{ids['job']}", "dryrun": f"/jobs/{ids['dryrun']}", "audit": "/audit",
             "import": "/modpacks/importar", "import-job": f"/jobs/{ids['import_job']}"}
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
                        ctx.add_init_script(CSP_LISTENER)
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
                            # B4 (d-7985ee-937ca8): at 360 px a table stacks its rows; one that still
                            # scrolls sideways inside its box does not widen the page, so it is counted here.
                            tables = page.evaluate(
                                "[...document.querySelectorAll('main table')]"
                                ".filter(t => t.parentElement.scrollWidth > t.parentElement.clientWidth + 1).length")
                            report[key] = {"status": response.status if response else None, "overflow_px": overflow,
                                           "console_errors": list(errors),
                                           "problems": [f"{tables} tablas desbordan su caja"] if width == 360 and tables else []}
                            errors.clear()
                        report.update(shoot_open_card(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
                        report.update(shoot_open_cards(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
                        report.update(check_live_refresh(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
                        if who == "operator":
                            report.update(shoot_update_dialog(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
                            report.update(shoot_build_dialog(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
                            report.update(shoot_pin_dialog(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
                            if (scheme, width) in MOD_CARD_CONTEXTS:
                                prefix = f"{who}-{scheme}-{width}"
                                # The tabs first: they resolve the pack, so the catalog has the
                                # made-up projects the card and the dialog show.
                                report.update(check_tabs(page, base, out, ids, prefix, errors))
                                report.update(check_mod_drawer(page, base, out, ids, prefix, errors))
                                report.update(shoot_mod_page(page, base, out, ids, prefix, errors))
                                report.update(shoot_problem_dialog(page, base, out, ids, prefix, errors))
                            if scheme == "light" and width == 1280:
                                # These change data (a mod is added, a pin is set): once, not in every context.
                                report.update(check_notices(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
                                report.update(check_problem_actions(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
                                report.update(check_row_selects(page, base, out, ids, f"{who}-{scheme}-{width}", errors))
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


def shoot_open_cards(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
                     errors: list[str]) -> dict[str, dict[str, Any]]:
    """Batch A (A8, A13): the first audit card and the first job card opened. The audit card's
    details are in the page; the job card loads its fragment when it opens. Neither may widen the
    page."""
    found: dict[str, dict[str, Any]] = {}
    for name, path in (("audit-card", "/audit"), ("job-card", "/jobs")):
        response = page.goto(base + path)
        page.wait_for_load_state("networkidle")
        card = page.locator("main details").first
        card.locator("summary").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        key = f"{prefix}-{name}"
        page.screenshot(path=str(out / f"{key}.png"))
        problems = [p for p, bad in (("la tarjeta no se abrió", not card.evaluate("d => d.open")),
                                     ("la página desborda", _overflow(page) > 0)) if bad]
        found[key] = _report(response, errors, problems)
    return found


def check_row_selects(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
                      errors: list[str]) -> dict[str, dict[str, Any]]:
    """Batch A (A1, A14): a row's select that fails goes back to its saved value; "sin fijar" from
    the row unpins and offers "Deshacer"; undoing back to a version before the floor opens the
    confirmation, and cancelling it leaves the row as saved. The pin is set again at the end, so
    the pack is as seeded for the contexts after this one."""
    problems: list[str] = []
    pack = ids["downgrade"]
    response = page.goto(base + f"/modpacks/{pack}")
    page.wait_for_load_state("networkidle")
    side = "#mod-row-jei select[name=server]"
    before = page.eval_on_selector(side, "s => s.value")
    page.route("**/decisions", lambda route: route.fulfill(status=500, body="no"))
    page.select_option(side, "no")
    page.wait_for_timeout(800)
    page.unroute("**/decisions")
    if page.eval_on_selector(side, "s => s.value") != before:
        problems.append("el select de lado no volvió a su valor tras el error")
    errors[:] = [e for e in errors if "500" not in e]  # the failure we asked for
    page.goto(base + f"/modpacks/{pack}")
    page.wait_for_load_state("networkidle")
    page.select_option("#mod-row-jei select[name=version_id]", "")
    page.wait_for_selector("#notices [data-notice] button:text('Deshacer')", timeout=10_000)
    page.wait_for_timeout(300)
    page.screenshot(path=str(out / f"{prefix}-row-undo.png"))
    page.click("#notices [data-notice] button:text('Deshacer')")
    page.wait_for_selector("#pin-dialog dialog[open]", timeout=10_000)
    page.locator("#pin-dialog dialog button[data-dialog-close]").click()
    page.wait_for_timeout(300)
    if page.eval_on_selector("#mod-row-jei select[name=version_id]", "s => s.value") != "":
        problems.append("al cancelar la baja, el select no quedó en lo guardado")
    page.evaluate(f"htmx.ajax('POST', '/modpacks/{pack}/pins', {{target: '#mods', swap: 'outerHTML', "
                  "values: {project_id: 'jei', version_id: 'jei-1951', confirm_downgrade: '1'}})")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    if not page.locator("#mods :text('baja (piso 19.57.0.450)')").count():
        problems.append("el pin no quedó como estaba sembrado")
    return {f"{prefix}-row-selects": _report(response, errors, problems)}


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


def shoot_pin_dialog(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
                     errors: list[str]) -> dict[str, dict[str, Any]]:
    """F27: pinning a version before the build's opens the "Fijar igual" dialog in #pin-dialog
    (the pin is not saved), and "Cancelar" closes it. The pin is sent as the version picker sends it."""
    response = page.goto(base + f"/modpacks/{ids['downgrade']}")
    page.wait_for_load_state("networkidle")
    marked = page.locator("#mods :text('baja (piso 19.57.0.450)')").count() > 0
    page.evaluate(f"htmx.ajax('POST', '/modpacks/{ids['downgrade']}/pins', "
                  "{target: '#mods', swap: 'outerHTML', values: {project_id: 'jei', version_id: 'jei-1951'}})")
    page.wait_for_selector("#pin-dialog dialog[open]", timeout=10_000)
    page.wait_for_timeout(300)
    key = f"{prefix}-pin-dialog"
    page.screenshot(path=str(out / f"{key}.png"))
    page.locator("#pin-dialog dialog button[data-dialog-close]").click()
    page.wait_for_timeout(300)
    closed = page.evaluate("document.querySelector('#pin-dialog dialog')?.open === false")
    problems = [p for p, bad in (("la lista no marca el pin que baja", not marked),
                                 ("«Cancelar» no cerró el diálogo", not closed)) if bad]
    return {key: _report(response, errors, problems)}


def check_live_refresh(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
                       errors: list[str]) -> dict[str, dict[str, Any]]:
    """A job ending (`job-finished`) reloads #server-main of a server without a modpack, whose form
    has inputs with ids. An input that got a style from outside (autofill, an extension: here set
    through the CSSOM, which the CSP allows) must not have it copied by htmx's settle step: that is
    an inline style attribute the CSP refuses, and it shows as a console error."""
    response = page.goto(base + f"/servers/{ids['server_new']}")
    page.wait_for_load_state("networkidle")
    page.evaluate("""() => {
        document.getElementById('server-main').dataset.old = '1';
        document.getElementById('new-modpack-name').style.outline = '0px';
    }""")
    page.evaluate("htmx.trigger(document.body, 'job-finished')")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)
    reloaded = page.evaluate("!document.querySelector('#server-main[data-old]') && !!document.getElementById('new-modpack-name')")
    key = f"{prefix}-server-new-refresh"
    page.screenshot(path=str(out / f"{key}.png"))
    return {key: _report(response, errors, ["#server-main no se recargó al terminar un trabajo"] if not reloaded else [])}


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


def _mods_swapped(page: Any) -> None:
    """Mark #mods, so a wait for `#mods:not([data-old])` sees it drawn again."""
    page.evaluate("document.getElementById('mods').dataset.old = '1'")


def _overflow(page: Any) -> int:
    return int(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"))


def check_tabs(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
               errors: list[str]) -> dict[str, dict[str, Any]]:
    """The pack's three views (spec §3), after resolving it: each tab redraws #mods without
    reloading the page and puts its `?vista=` in the URL. In "Por decidir", "Todos al servidor"
    marks every select and sends nothing: the form's own button saves."""
    response = page.goto(base + f"/modpacks/{ids['problems']}")
    page.wait_for_load_state("networkidle")
    _mods_swapped(page)
    page.click('#mods [hx-post*="/resolve"]')
    page.wait_for_selector("#mods:not([data-old]) [data-pending-summary]", timeout=15_000)
    page.wait_for_load_state("networkidle")
    page.evaluate("window.sweepSamePage = true")
    problems: list[str] = []
    for tab in ("decidir", "problemas", "todos"):
        _mods_swapped(page)
        page.click(f'#mods [data-tab="{tab}"]')
        try:
            page.wait_for_selector(f'#mods:not([data-old]) [data-vista="{tab}"]', timeout=10_000)
        except Exception:
            problems.append(f"la pestaña {tab} no redibujó #mods")
            continue
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(200)
        same_page, search = page.evaluate("[window.sweepSamePage === true, location.search]")
        if not same_page:
            problems.append(f"la pestaña {tab} recargó la página")
        if f"vista={tab}" not in search:
            problems.append(f"la pestaña {tab} no puso ?vista={tab} en la URL ({search or 'sin query'})")
        if tab == "decidir":
            problems += _set_all_sends_nothing(page)
        page.screenshot(path=str(out / f"{prefix}-tab-{tab}.png"), full_page=True)
        if _overflow(page) > 0:
            problems.append(f"la pestaña {tab} desborda")
    return {f"{prefix}-tabs": _report(response, errors, problems)}


def _set_all_sends_nothing(page: Any) -> list[str]:
    """"Todos al servidor" in "Por decidir": every select says "si" and no request leaves (an icon
    scrolled into view aside)."""
    sent: list[str] = []

    def on_request(request: Any) -> None:
        # The bar's and the banner's polls run on their own timer (E8): not the button's doing.
        if request.resource_type != "image" and "/trabajos/mios" not in request.url and "/trabajo?" not in request.url \
                and not request.url.endswith("/trabajo"):
            sent.append(f"{request.method} {request.url}")
    page.on("request", on_request)
    page.click('#mods [data-vista="decidir"] [data-set-all="si"]')
    page.wait_for_timeout(500)
    page.remove_listener("request", on_request)
    values = page.evaluate("Array.from(document.querySelectorAll('#mods [data-vista=\"decidir\"] select'), s => s.value)")
    problems = []
    if len(values) < 2 or any(v != "si" for v in values):
        problems.append(f"«Todos al servidor» no marcó los selectores: {values}")
    if sent:
        problems.append(f"«Todos al servidor» hizo peticiones: {sent}")
    return problems


def check_mod_drawer(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
                     errors: list[str]) -> dict[str, dict[str, Any]]:
    """A mod's name in the pack's list (spec §2): Ctrl/Cmd+click and the middle button open its
    page in another tab and leave the panel closed; a plain click opens #mod-drawer as a modal with
    its card; a mod clicked inside it replaces the card and offers "← {the first}"; Escape closes it
    and the focus goes back to the name clicked."""
    path = f"/modpacks/{ids['problems']}?vista=todos"
    response = page.goto(base + path)
    page.wait_for_load_state("networkidle")
    link = page.locator(f'#mod-row-{NEEDS_BETA} summary a[data-mod-link]')
    problems: list[str] = []
    clicks: tuple[tuple[str, dict[str, Any]], ...] = (("Ctrl/Cmd+clic", {"modifiers": ["ControlOrMeta"]}),
                                                      ("el clic central", {"button": "middle"}))
    for label, options in clicks:
        try:
            with page.context.expect_page(timeout=10_000) as opened:
                link.click(**options)
            tab = opened.value
            tab.wait_for_load_state()
            if urllib.parse.urlsplit(tab.url).path != f"/mods/{NEEDS_BETA}" or \
                    not tab.locator(f'[data-mod-card="{NEEDS_BETA}"]').count():
                problems.append(f"{label} abrió {tab.url}, no la ficha de /mods/{NEEDS_BETA}")
            tab.close()
        except Exception:
            problems.append(f"{label} no abrió /mods/{NEEDS_BETA} en otra pestaña")
        page.wait_for_timeout(500)
        if page.evaluate("document.getElementById('mod-drawer').open"):
            problems.append(f"{label} abrió el panel")
            page.keyboard.press("Escape")
        if page.url != base + path:
            problems.append(f"{label} cambió la página a {page.url}")

    link.click()
    page.wait_for_selector(f'#mod-drawer[open] [data-mod-card="{NEEDS_BETA}"]', timeout=10_000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    if not page.evaluate("document.getElementById('mod-drawer').matches(':modal')"):
        problems.append("el panel no se abrió como modal")
    page.screenshot(path=str(out / f"{prefix}-mod-drawer.png"))

    page.locator(f'#mod-drawer-body a[data-mod-link][href^="/mods/{BETA_DEP}"]').first.click()
    try:
        page.wait_for_selector(f'#mod-drawer[open] [data-mod-card="{BETA_DEP}"]', timeout=10_000)
    except Exception:
        problems.append("un mod abierto dentro del panel no reemplazó la ficha")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    back = page.locator("#mod-drawer-body button", has_text="←")
    if not back.count() or "Necesita una beta" not in back.first.inner_text():
        problems.append("el panel no ofrece «← volver» a la ficha anterior")
    page.screenshot(path=str(out / f"{prefix}-mod-drawer-back.png"))

    page.keyboard.press("Escape")
    try:
        page.wait_for_selector("#mod-drawer[open]", state="detached", timeout=5_000)
    except Exception:
        problems.append("Escape no cerró el panel")
    page.wait_for_timeout(200)
    if not link.evaluate("el => el === document.activeElement"):
        focused = page.evaluate("document.activeElement?.outerHTML.slice(0, 120)")
        problems.append(f"al cerrar el panel el foco no volvió al nombre del mod (está en {focused})")
    return {f"{prefix}-mod-drawer": _report(response, errors, problems)}


def shoot_mod_page(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
                   errors: list[str]) -> dict[str, dict[str, Any]]:
    """A mod's own page, opened from a pack: its card, where it is used and its versions."""
    response = page.goto(base + f"/mods/{NEEDS_BETA}?pack={ids['problems']}")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    key = f"{prefix}-mod-page"
    page.screenshot(path=str(out / f"{key}.png"), full_page=True)
    problems = [p for p, bad in (("la ficha no se dibujó", not page.locator(f'[data-mod-card="{NEEDS_BETA}"]').count()),
                                 ("la página desborda", _overflow(page) > 0)) if bad]
    return {key: _report(response, errors, problems)}


def _dialog_state(page: Any) -> dict[str, Any]:
    return dict(page.evaluate("""() => {
        const d = document.querySelector('#build-dialog dialog');
        const beta = d?.querySelector('[data-problem="%s"]');
        return {open: !!d && d.open, modal: !!d && d.matches(':modal'),
                problems: d ? d.querySelectorAll('[data-problem]').length : 0,
                beta: !!beta && Array.from(beta.querySelectorAll('button')).some(b => b.textContent.includes('Usar la beta')),
                pinned: !!d?.querySelector('[data-problem="%s"]'), text: d ? d.textContent : ''};
    }""" % (BETA_DEP, PINNED_GONE)))


def _escape_to_build_create(page: Any, when: str) -> list[str]:
    """Escape closes the build dialog and leaves the focus on "Crear build" (#build-create)."""
    page.keyboard.press("Escape")
    try:
        page.wait_for_selector("#build-dialog dialog[open]", state="detached", timeout=5_000)
    except Exception:
        return [f"Escape no cerró el diálogo de build ({when})"]
    page.wait_for_timeout(200)
    focused = page.evaluate("document.activeElement?.id || document.activeElement?.tagName")
    return [] if focused == "build-create" else [f"al cerrar el diálogo ({when}) el foco quedó en {focused}, no en #build-create"]


def shoot_problem_dialog(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
                         errors: list[str]) -> dict[str, dict[str, Any]]:
    """"Crear build" on a pack with problems: the build dialog opens as a modal with each problem's
    card, the dependency that only has a beta offering "Usar la beta …" and the pinned version that
    is gone offering "Fijar otra"; Escape closes it with the focus on #build-create. Nothing is
    changed here (see check_problem_actions)."""
    response = page.goto(base + f"/modpacks/{ids['problems']}?vista=todos")
    page.wait_for_load_state("networkidle")
    page.click("#build-create")
    page.wait_for_selector("#build-dialog dialog[open]", timeout=15_000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    state = _dialog_state(page)
    key = f"{prefix}-build-dialog-problems"
    page.screenshot(path=str(out / f"{key}.png"))
    problems = [p for p, bad in (("el diálogo de build no se abrió como modal", not state["modal"]),
                                 ("la dependencia con beta no ofrece «Usar la beta …»", not state["beta"]),
                                 ("falta la tarjeta de la versión fijada que ya no está", not state["pinned"]),
                                 ("el diálogo no ofrece «Fijar otra»", "Fijar otra" not in state["text"])) if bad]
    problems += _escape_to_build_create(page, "sin acciones")
    return {key: _report(response, errors, problems)}


def check_problem_actions(page: Any, base: str, out: Path, ids: dict[str, str], prefix: str,
                          errors: list[str]) -> dict[str, dict[str, Any]]:
    """The build dialog's actions, on a pack of its own (they change it; once). "Usar la beta …"
    pins the dependency: the dialog stays open as a modal, recalculated without that problem, with
    #mods redrawn behind it, and Escape leaves the focus on the new #build-create. Then, on a page
    that already is `?vista=todos`, "Fijar otra" closes the dialog and opens that mod's row
    (#mod-row-<id>) with the focus on its summary (the fix of cdf5890: hashchange)."""
    path = f"/modpacks/{ids['fix']}?vista=todos"
    response = page.goto(base + path)
    page.wait_for_load_state("networkidle")
    problems: list[str] = []
    page.click("#build-create")
    page.wait_for_selector(f'#build-dialog dialog[open] [data-problem="{BETA_DEP}"]', timeout=15_000)
    page.wait_for_load_state("networkidle")
    before = _dialog_state(page)
    _mods_swapped(page)
    page.locator(f'#build-dialog [data-problem="{BETA_DEP}"] button', has_text="Usar la beta").click()
    try:
        page.wait_for_selector(f'#build-dialog [data-problem="{BETA_DEP}"]', state="detached", timeout=15_000)
    except Exception:
        problems.append("«Usar la beta …» no recalculó el diálogo (la dependencia sigue como problema)")
    page.wait_for_load_state("networkidle")
    after = _dialog_state(page)
    page.screenshot(path=str(out / f"{prefix}-build-dialog-after-action.png"))
    if not after["modal"]:
        problems.append("después de la acción el diálogo no sigue abierto como modal")
    if after["problems"] != before["problems"] - 1 or not after["pinned"]:
        problems.append(f"el diálogo recalculado tiene {after['problems']} problemas (antes {before['problems']})")
    if "Beta aceptada para este mod" not in after["text"]:
        problems.append("el diálogo recalculado no dice «Beta aceptada para este mod»")
    if page.locator("#mods[data-old]").count() or not page.locator("#mods").count():
        problems.append("#mods no se redibujó detrás del diálogo")
    problems += _escape_to_build_create(page, "después de «Usar la beta»")

    page.click("#build-create")
    page.wait_for_selector(f'#build-dialog dialog[open] [data-problem="{PINNED_GONE}"]', timeout=15_000)
    page.wait_for_load_state("networkidle")
    page.locator(f'#build-dialog [data-problem="{PINNED_GONE}"] a', has_text="Fijar otra").click()
    try:
        page.wait_for_selector(f"#mod-row-{PINNED_GONE} details[open]", timeout=10_000)
    except Exception:
        problems.append("«Fijar otra» no abrió la fila del mod")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    state = page.evaluate(f"""() => ({{
        dialog: !!document.querySelector('dialog[open]'),
        focus: document.activeElement === document.querySelector('#mod-row-{PINNED_GONE} summary'),
        focused: document.activeElement?.outerHTML.slice(0, 120),
        url: location.pathname + location.search + location.hash}})""")
    page.screenshot(path=str(out / f"{prefix}-fijar-otra.png"))
    if state["dialog"]:
        problems.append("«Fijar otra» dejó el diálogo abierto")
    if not state["focus"]:
        problems.append(f"«Fijar otra» no dejó el foco en la fila del mod (está en {state['focused']})")
    if state["url"] != f"{path}#mod-row-{PINNED_GONE}":
        problems.append(f"«Fijar otra» terminó en {state['url']}")
    return {f"{prefix}-problem-actions": _report(response, errors, problems)}


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
