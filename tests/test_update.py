"""Actualizar un servidor a un build (i-7985ee-495708), contra un panel falso con estado.

Las decisiones del dueño que se prueban:
- solo a pedido, sin aviso a los jugadores: `save-all` y parar (d-7985ee-c2adc8);
- los jars nuevos los baja la API, verifica su sha512 y los sube a staging (d-7985ee-a13b33);
- 5 minutos estable para darlo por bueno; sin cupo de backups, abortar antes de parar
  (d-7985ee-5cc6ee);
- ante un fallo, parar, avisar y esperar a que un humano elija el rollback A o B (d-7985ee-5445a0);
- un jar que el build no explica impide actualizar: hay que importar (d-7985ee-c8a15d).
Y el modo simulación: lee todo, verifica las descargas y no escribe nada.
"""
import hashlib
from datetime import datetime, timezone
from typing import Any, cast

import pytest
from fastapi import HTTPException

from src.database.jobs import JobDatabase
from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.model.jobs import Job
from src.library.scan import LocalFile
from src.model.pterodactyl import PterodactylServer
from src.services.jobs import JobService, JobWorker
from src.services.pterodactyl import PterodactylService
from src.services.update import UpdateService, UpdateTiming
from tests._actors import OPERATOR, RecordingAudit
from tests._modrinth import FABRIC_API
from tests._panel import StatefulPanel, sha512
from tests.conftest import MongoRunner
from tests.test_notify import Recorder
from tests.test_server_scan import HashModrinthAPI, build_with_fabric_api

pytestmark = pytest.mark.network

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class FakeTime:
    """Un reloj que avanza solo cuando se duerme: cinco minutos de verificación, al instante."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeDownloader:
    def __init__(self, contents: dict[str, bytes]) -> None:
        self.contents = contents
        self.fetched: list[str] = []

    async def download_file(self, url: str) -> bytes:
        self.fetched.append(url)
        return self.contents[url]


class Setup:
    """Un servidor con la versión vieja de Fabric API, asociado a un bundle cuyo build recomendado
    pide la nueva. Las versiones grabadas se reescriben con hashes de contenidos conocidos, para
    que la descarga se pueda verificar."""

    def __init__(self, panel: StatefulPanel, update: UpdateService, server_id: str, recorder: Recorder,
                 downloader: FakeDownloader, newest: str, older: str, contents: dict[str, bytes]) -> None:
        self.panel, self.update, self.server_id, self.recorder = panel, update, server_id, recorder
        self.downloader, self.newest, self.older, self.contents = downloader, newest, older, contents

    def content(self, version_id: str) -> bytes:
        return f"fabric-api {version_id}".encode()

    async def run(self, **enqueue: Any) -> Job:
        job = await self.update.enqueue_update(self.server_id, actor=OPERATOR, **enqueue)
        worker = JobWorker(JobDatabase(), self.update.handlers(), owner="w1", clock=lambda: NOW)
        while await worker.run_once():
            pass
        stored = await Job.get(job.id)
        assert stored is not None
        return stored

    async def rollback(self, update_job: Job, mode: str) -> Job:
        job = await self.update.enqueue_rollback(self.server_id, str(update_job.id), mode, actor=OPERATOR)
        worker = JobWorker(JobDatabase(), self.update.handlers(), owner="w1", clock=lambda: NOW)
        while await worker.run_once():
            pass
        stored = await Job.get(job.id)
        assert stored is not None
        return stored


async def setup(extra_files: dict[str, bytes] | None = None, installed: str = "older", audit: Any = None,
                **panel_options: Any) -> Setup:
    modrinth = HashModrinthAPI({})
    contents: dict[str, bytes] = {}
    for version in modrinth.versions:
        if version["project_id"] == FABRIC_API:
            data = f"fabric-api {version['id']}".encode()
            file = version["files"][0]
            file["hashes"]["sha1"], file["hashes"]["sha512"], file["size"] = sha1(data), sha512(data), len(data)
            file["url"] = f"https://cdn.modrinth.com/data/{FABRIC_API}/versions/{version['id']}/{file['filename']}"
            contents[file["url"]] = data
    bundle_id, newest = await build_with_fabric_api(modrinth)
    older = next(v["id"] for v in modrinth.versions if v["project_id"] == FABRIC_API and v["id"] != newest)
    for version_id in (newest, older):
        modrinth.known[sha1(f"fabric-api {version_id}".encode())] = version_id
    on_server = f"fabric-api {newest if installed == 'newest' else older}".encode()
    panel = StatefulPanel({"/mods/fabric-api-installed.jar": on_server, **(extra_files or {})}, **panel_options)
    servers = PterodactylService(pterodactylAPI=cast(Any, panel), PterodactylDB=PterodactylDatabase(),
                                 modrinthAPI=cast(Any, modrinth), modrinthDB=ModrinthDatabase(), modpackDB=ModpackDatabase(),
                                 audit=RecordingAudit())
    server = await PterodactylDatabase().server_insert(PterodactylServer(
        identifier="1a2b", internal_id=1, uuid="1a2b-0", name="Survival", node="n1", description="", status=None,
        is_suspended=False, is_installing=False, is_transferring=False))
    await servers.server_link_bundle(str(server.id), bundle_id, actor=OPERATOR)
    recorder, downloader, time = Recorder(), FakeDownloader(contents), FakeTime()
    update = UpdateService(pterodactylAPI=cast(Any, panel), pterodactylService=servers, pterodactylDB=PterodactylDatabase(),
                           modpackDB=ModpackDatabase(), downloader=cast(Any, downloader), jobDB=JobDatabase(),
                           audit=audit or RecordingAudit(), notifier=cast(Any, recorder), timing=UpdateTiming(save_wait=5, poll=10, sleep=time.sleep, clock=time.clock))
    return Setup(panel, update, str(server.id), recorder, downloader, newest, older, contents)


def new_jar(s: Setup) -> str:
    return next(url.rsplit("/", 1)[-1] for url in s.contents if f"/{s.newest}/" in url)


# Simulación.


def test_a_dry_run_reads_and_verifies_everything_and_writes_nothing(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        s = await setup()
        job = await s.run()  # simulación por defecto
        linked = (await PterodactylDatabase().server_get(s.server_id)).bundle_id
        return (job.status, s.panel.writes, job.checkpoint["preflight"], job.checkpoint["log"]["lines"],
                s.downloader.fetched, s, linked)

    status, writes, plan, log, fetched, s, linked = mongo(scenario)
    assert status == "done" and writes == []
    # The plan names the bundle it was made for: the panel's real run is tied to it.
    assert linked and plan["bundle_id"] == linked
    assert [f["filename"] for f in plan["install"]] == [new_jar(s)] and plan["remove"] == ["/mods/fabric-api-installed.jar"]
    # U1 (owner, 2026-10-09): the dry run downloads nothing; it checks the build's own data and the
    # real run verifies each download before stopping the server.
    assert fetched == [] and any("power stop" in line for line in log) and any("backup" in line for line in log)
    # What it would write still lists the uploads, as before (the dry run's page shows it).
    assert any(line.startswith("upload /mods.staging/") and line.endswith(new_jar(s)) for line in log)


# El camino feliz.


def test_an_update_installs_the_build_keeps_the_old_jars_and_announces_it(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        s = await setup()
        job = await s.run(dry_run=False)
        return job.status, s.panel.mods(), s.panel.files, s.panel.writes, s.panel.state, s.panel.backup_list, s.recorder.sent, job, s

    status, mods, files, writes, state, backups, sent, job, s = mongo(scenario)
    assert status == "done" and state == "running"
    assert mods == {new_jar(s): sha512(s.content(s.newest))[:8]}
    assert f"/mods.old/{job.id}/fabric-api-installed.jar" in files
    # Staging antes de parar, save-all antes de stop, backup antes de tocar mods/.
    order = [w.split()[0] + (" " + w.split()[1] if w.startswith("power") else "") for w in writes]
    assert order.index("upload") < order.index("command") < order.index("power stop") < order.index("backup") < order.index("rename")
    [backup] = backups
    assert backup.is_successful and backup.is_locked is False  # bloqueado mientras duró, liberado al terminar
    assert sent and "Survival" in sent[-1] and "✅" in sent[-1]


def test_a_server_already_on_the_build_is_left_alone(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, list[str]]:
        s = await setup(installed="newest")
        job = await s.run(dry_run=False)
        return job.status, s.panel.writes

    assert mongo(scenario) == ("done", [])


# Lo que aborta antes de escribir.


def test_a_dry_run_refuses_a_jar_the_build_cannot_verify(mongo: MongoRunner) -> None:
    # U1: with no download, the dry run still refuses what the real run could not verify (here a
    # file from a host outside Modrinth's allowed ones).
    from src.model.modpack import ModpackBuild

    async def scenario() -> tuple[str, Any, list[str]]:
        s = await setup()
        builds = await ModpackBuild.find().to_list()
        for build in builds:
            for entry in build.lock.entries:
                if entry.file is not None:
                    entry.file.url = "https://evil.example/fabric-api.jar"
            await build.save()
        job = await s.run()
        return job.status, job.error, s.downloader.fetched

    status, error, fetched = mongo(scenario)
    assert status == "failed" and "allowed domain" in (error or "") and fetched == []


@pytest.mark.parametrize("case", ["unexplained jar", "no backup slot", "bad download", "server busy"])
def test_what_aborts_does_so_before_any_write(case: str, mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, int, list[str], list[str]]:
        extra = {"/mods/hecho-a-mano.jar": b"by hand"} if case == "unexplained jar" else {}
        s = await setup(extra_files=extra, backup_limit=0 if case == "no backup slot" else 2)
        if case == "bad download":
            s.downloader.contents = {url: b"tampered" for url in s.contents}
        if case == "server busy":
            s.panel.status = "installing"
        job = await s.run(dry_run=False)
        return job.status, job.attempts, s.panel.writes, s.recorder.sent

    status, attempts, writes, sent = mongo(scenario)
    assert (status, attempts, writes) == ("failed", 1, [])
    assert sent and "no se actualizó" in sent[-1]


def test_an_unexplained_jar_says_to_import_first(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, str]:
        s = await setup(extra_files={"/mods/hecho-a-mano.jar": b"by hand"})
        job = await s.run(dry_run=False)
        return job.status, job.error or ""

    status, error = mongo(scenario)
    assert status == "failed" and "importa el servidor" in error and "/mods/hecho-a-mano.jar" in error
    assert "crea un build nuevo" not in error


def test_unexplained_jar_message_points_to_a_new_build_when_the_pack_has_it(mongo: MongoRunner) -> None:
    """F26: the jar was imported (a local file of the bundle) after the build was made: the build
    does not explain it, but importing again would change nothing. A new build does."""
    async def scenario() -> tuple[str, str]:
        jar = b"by hand"
        s = await setup(extra_files={"/mods/hecho-a-mano.jar": jar})
        bundle_id = (await PterodactylDatabase().server_get(s.server_id)).bundle_id
        assert bundle_id is not None
        await ModpackDatabase().bundle_local_file_add(bundle_id, LocalFile(
            filename="hecho-a-mano.jar", sha512=sha512(jar), sha1=sha1(jar), size=len(jar)))
        job = await s.run(dry_run=True)
        return job.status, job.error or ""

    status, error = mongo(scenario)
    assert status == "failed" and "crea un build nuevo: el modpack ya lo incluye" in error
    assert "/mods/hecho-a-mano.jar" in error and "importa el servidor" not in error


def test_both_fixes_are_said_when_both_kinds_of_jar_are_there(mongo: MongoRunner) -> None:
    async def scenario() -> str:
        jar = b"by hand"
        s = await setup(extra_files={"/mods/hecho-a-mano.jar": jar, "/mods/ajeno.jar": b"stranger"})
        bundle_id = (await PterodactylDatabase().server_get(s.server_id)).bundle_id
        assert bundle_id is not None
        await ModpackDatabase().bundle_local_file_add(bundle_id, LocalFile(
            filename="hecho-a-mano.jar", sha512=sha512(jar), sha1=sha1(jar), size=len(jar)))
        return (await s.run(dry_run=True)).error or ""

    assert mongo(scenario) == ("importa el servidor: el modpack no explica /mods/ajeno.jar; crea un build nuevo: "
                               "el modpack ya lo incluye y el build no: /mods/hecho-a-mano.jar")


def test_a_real_update_ends_with_a_scan_compared_with_the_new_build(mongo: MongoRunner) -> None:
    """The preflight's scan is stale once the jars change (it records no drift): a done real update
    scans again, read-only, so the dashboard and the server page show the state it left (F37)."""
    async def scenario() -> tuple[str, int, str | None]:
        s = await setup()
        job = await s.run(dry_run=False)
        scans = await PterodactylDatabase().scan_find(s.server_id)
        return job.status, len(scans), scans[0].drift

    status, new_scans, drift = mongo(scenario)
    assert status == "done" and new_scans == 2  # the preflight's and the one after
    assert drift is not None and drift.startswith("✅") and "build 1" in drift


def test_a_real_update_with_nothing_to_change_still_records_the_state(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, str | None]:
        s = await setup(installed="newest")
        job = await s.run(dry_run=False)
        latest = await PterodactylDatabase().scan_latest(s.server_id, "/mods")
        assert latest is not None
        return job.status, latest.drift

    status, drift = mongo(scenario)
    assert status == "done" and drift is not None and drift.startswith("✅")


def test_a_failing_rescan_after_the_update_leaves_it_done_and_is_logged(mongo: MongoRunner,
                                                                          caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> tuple[str, list[str], str | None]:
        s = await setup()
        real_scan = s.update.servers.scan

        async def scan(server_id: str, actor: Any, record_drift: bool = True, **kwargs: Any) -> Any:
            if record_drift:  # the rescan after the update; the preflight's records none
                raise RuntimeError("panel down")
            return await real_scan(server_id, actor, record_drift=record_drift, **kwargs)

        s.update.servers.scan = scan  # type: ignore[method-assign, assignment]
        job = await s.run(dry_run=False)
        return job.status, s.recorder.sent, job.error

    with caplog.at_level("WARNING", logger="modpack.update"):
        status, sent, error = mongo(scenario)
    assert status == "done" and error is None and "✅" in sent[-1]
    assert any("scan after the update" in r.getMessage() for r in caplog.records)


def test_only_a_dry_runs_scan_records_its_drift(mongo: MongoRunner) -> None:
    """A real update changes the server right after its preflight scan: that scan's comparison
    would be stale at once, so it records none. A dry run changes nothing: its scan's is true."""
    async def scenario() -> tuple[str | None, str | None]:
        s = await setup()
        await s.run()  # dry run
        dry = (await PterodactylDatabase().scan_latest(s.server_id, "/mods"))
        await s.run(dry_run=False)
        # The real run's preflight scan: the one before the rescan that ends a done update.
        real = (await PterodactylDatabase().scan_find(s.server_id))[1]
        assert dry is not None and dry.id != real.id
        return dry.drift, real.drift

    dry, real = mongo(scenario)
    assert dry is not None and dry.startswith("⚠️") and real is None


def test_the_installed_build_is_the_last_real_update_that_finished(mongo: MongoRunner) -> None:
    async def scenario() -> list[int | None]:
        s = await setup()
        jobs = JobService(jobDB=JobDatabase(), pterodactylService=s.update.servers, pterodactylDB=PterodactylDatabase(),
                          audit=RecordingAudit())
        seen = [await jobs.installed_build(s.server_id)]
        await s.run()  # a dry run installs nothing
        seen.append(await jobs.installed_build(s.server_id))
        await s.run(dry_run=False)
        seen.append(await jobs.installed_build(s.server_id))
        return seen

    assert mongo(scenario) == [None, None, 1]


# Lo que sale mal con el servidor.


def test_a_server_that_ignores_stop_is_killed(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, list[str]]:
        s = await setup(ignores_stop=True)
        job = await s.run(dry_run=False)
        return job.status, [w for w in s.panel.writes if w.startswith("power")]

    status, power = mongo(scenario)
    assert status == "done" and power[:2] == ["power stop", "power kill"]


def test_a_crash_after_start_stops_the_server_and_waits_for_a_human(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, int, str, list[str], str]:
        s = await setup(crash_after_start=3)
        job = await s.run(dry_run=False)
        return job.status, job.attempts, s.panel.state, s.recorder.sent, job.error or ""

    status, attempts, state, sent, error = mongo(scenario)
    assert (status, attempts, state) == ("failed", 1, "offline")
    assert "rollback" in sent[-1] and "rollback" in error


def test_rollback_a_brings_the_old_jars_back(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, dict[str, str], str, Setup]:
        s = await setup(crash_after_start=3)
        failed = await s.run(dry_run=False)
        s.panel.crash_after_start = None
        rollback = await s.rollback(failed, "A")
        return rollback.status, s.panel.mods(), s.panel.state, s

    status, mods, state, s = mongo(scenario)
    assert (status, state) == ("done", "running")
    assert mods == {"fabric-api-installed.jar": sha512(s.content(s.older))[:8]}


def test_rollback_b_restores_the_backup(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, dict[str, bytes], dict[str, bytes], list[str]]:
        s = await setup(crash_after_start=3)
        before = dict(s.panel.files)
        failed = await s.run(dry_run=False)
        s.panel.crash_after_start = None
        rollback = await s.rollback(failed, "B")
        restored = {p: d for p, d in s.panel.files.items() if p.startswith("/mods/")}
        return rollback.status, restored, {p: d for p, d in before.items() if p.startswith("/mods/")}, s.panel.writes

    status, restored, before, writes = mongo(scenario)
    assert status == "done" and restored == before
    assert any(w.startswith("restore") and "truncate=True" in w for w in writes)


def test_a_rollback_needs_a_failed_update_of_that_server(mongo: MongoRunner) -> None:
    async def scenario() -> list[int]:
        s = await setup()
        done = await s.run(dry_run=False)
        codes = []
        for update_id, mode in ((str(done.id), "A"), ("000000000000000000000000", "A"), (str(done.id), "C")):
            with pytest.raises(HTTPException) as refused:
                await s.update.enqueue_rollback(s.server_id, update_id, mode, actor=OPERATOR)
            codes.append(refused.value.status_code)
        return codes

    assert mongo(scenario) == [409, 404, 422]


def test_job_without_dry_run_is_a_simulation_everywhere(mongo: MongoRunner) -> None:
    """A job that lost its `dry_run` key is a simulation for the worker, the rollback and the
    panel's idea of the last real update: failing closed, never a real run by omission."""
    async def scenario() -> tuple[Any, ...]:
        s = await setup()
        jobs = JobDatabase()
        queued = await jobs.enqueue("server_update", key=f"server:{s.server_id}",
                                    payload={"server_id": s.server_id, "build": 1})
        worker = JobWorker(jobs, s.update.handlers(), owner="w1", clock=lambda: NOW)
        while await worker.run_once():
            pass
        ran = await Job.get(queued.id)
        assert ran is not None
        await Job.get_pymongo_collection().update_one(
            {"_id": ran.id}, {"$set": {"status": "failed", "checkpoint.swap": {}, "checkpoint.backup": {}}})
        with pytest.raises(HTTPException) as refused:
            await s.update.enqueue_rollback(s.server_id, str(ran.id), "A", actor=OPERATOR)
        return s.panel.writes, refused.value.status_code, await jobs.last_real_update(f"server:{s.server_id}")

    writes, code, last = mongo(scenario)
    assert writes == [] and code == 409 and last is None


def test_a_retry_after_the_backup_does_not_make_a_second_one(mongo: MongoRunner) -> None:
    """La entrega es al menos una vez: si el proceso muere después de crear el backup y antes de
    anotarlo, el reintento lo encuentra por su nombre (retry-over-irreversible-effect)."""
    async def scenario() -> tuple[str, int]:
        s = await setup()
        original = s.panel.backup_get
        calls = {"n": 0}

        async def dies_once(server_id: str, backup_id: str) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("the process died after creating the backup")
            return await original(server_id, backup_id)

        s.panel.backup_get = dies_once  # type: ignore[method-assign]
        job = await s.update.enqueue_update(s.server_id, actor=OPERATOR, dry_run=False)
        clock = {"now": NOW}
        worker = JobWorker(JobDatabase(), s.update.handlers(), owner="w1", clock=lambda: clock["now"])
        await worker.run_once()
        clock["now"] = NOW.replace(hour=13)
        await worker.run_once()
        stored = await Job.get(job.id)
        assert stored is not None
        return stored.status, len(s.panel.backup_list)

    assert mongo(scenario) == ("done", 1)


def test_jars_go_up_in_batches_that_never_pass_the_limit() -> None:
    # U2: one signed URL and one POST per batch; a jar bigger than the limit goes alone.
    from src.services.update import upload_batches

    sizes = [("a", 40), ("b", 50), ("c", 30), ("d", 120), ("e", 10)]
    batches = upload_batches(sizes, limit=100)
    assert [[name for name, _ in batch] for batch in batches] == [["a", "b"], ["c"], ["d"], ["e"]]
