"""Importar un archivo subido corre entero en el trabajo (O6, O1; dueño 2026-10-21): la petición solo
guarda el archivo y encola; el trabajo lo lee fuera del bucle de eventos, informa su avance, y lo
borra al terminar, bien o mal. Sin MongoDB: la base de trabajos y el contexto son dobles; lo que
pide a Modrinth (`run_content`, `run_curseforge`) también."""
import asyncio
import io
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, cast

import pytest
from beanie import PydanticObjectId

from src.exceptions import HTTP_409_CONFLICT
from src.library.cf_import import CfContent
from src.library.import_uploads import UploadStore
from src.library.mrpack_import import MrpackContent
from src.model.jobs import Job
from src.model.users import Actor
from src.services import modpack_import
from src.services.jobs import JobFailed
from src.services.modpack_import import ImportReport, ModpackImportService
from tests._actors import EDITOR, RecordingAudit
from tests.test_modpack_import import cf_pack, mrpack, prism_zip


class FakeJobs:
    def __init__(self) -> None:
        self.jobs: list[Job] = []

    async def enqueue(self, kind: str, key: Optional[str], payload: dict[str, Any], max_attempts: int = 3,
                      now: Optional[datetime] = None, requested_by: Any = None) -> Job:
        if any(j.key == key and j.active for j in self.jobs):
            raise HTTP_409_CONFLICT(f"There is already an active job for {key}")
        job = Job.model_construct(id=PydanticObjectId(), kind=kind, key=key, payload=payload, status="queued",
                                  active=True, attempts=1, max_attempts=max_attempts, checkpoint={},
                                  created_at=datetime.now(timezone.utc), requested_by=requested_by)
        self.jobs.append(job)
        return job

    async def active_for(self, key: str) -> Optional[Job]:
        return next((j for j in self.jobs if j.key == key and j.active), None)

    async def active_of_kind(self, kind: str) -> list[Job]:
        return [j for j in self.jobs if j.kind == kind and j.active]


class FakeContext:
    def __init__(self, job: Job) -> None:
        self.job = job
        self.progressed: list[str] = []

    async def progress(self, label: str, done: int, total: int) -> None:
        self.progressed.append(label)

    async def step(self, name: str, action: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        result = await action()
        self.job.checkpoint[name] = result
        return result


class FakeCurseForge:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled


def service(tmp_path: Path, curseforge: bool = False) -> tuple[ModpackImportService, FakeJobs, RecordingAudit]:
    jobs, audit = FakeJobs(), RecordingAudit()
    built = ModpackImportService(cast(Any, None), cast(Any, None), cast(Any, None), cast(Any, None), cast(Any, None),
                                 cast(Any, audit), jobDB=cast(Any, jobs), curseforge=cast(Any, FakeCurseForge(curseforge)),
                                 uploads=UploadStore(tmp_path / "imports"))
    return built, jobs, audit


def report(source: str) -> ImportReport:
    return ImportReport(bundle_id="b1", created=True, source=source)


def test_an_upload_is_stored_and_queued_without_being_read(tmp_path: Path) -> None:
    importer, jobs, audit = service(tmp_path)
    job = asyncio.run(importer.import_upload(io.BytesIO(b"not even a zip"), "pack.mrpack", EDITOR, into=None))
    name = job.payload["upload"]
    # Nothing is read in the request: even a file that is not a pack is queued, and the job says why.
    assert job.kind == "modpack_import" and job.key == f"import:{EDITOR.iss}|{EDITOR.sub}" and job.max_attempts == 1
    assert "content" not in job.payload and job.payload["into"] is None
    assert importer.uploads.path_of(name).read_bytes() == b"not even a zip"
    assert [(e[0], e[2]) for e in audit.events] == [("bundle.import.request", {"source": "pack.mrpack", "job_id": str(job.id)})]


def test_asking_again_while_an_import_runs_answers_that_one_and_keeps_no_second_file(tmp_path: Path) -> None:
    importer, jobs, audit = service(tmp_path)

    async def scenario() -> tuple[Job, Job]:
        first = await importer.import_upload(io.BytesIO(b"one"), "a.zip", EDITOR, into=None)
        again = await importer.import_upload(io.BytesIO(b"two"), "b.zip", EDITOR, into=None)
        return first, again

    first, again = asyncio.run(scenario())
    assert again.id == first.id and importer.uploads.names() == [first.payload["upload"]]
    assert len(audit.events) == 1


def run_job(importer: ModpackImportService, job: Job) -> FakeContext:
    context = FakeContext(job)

    async def run() -> None:
        await importer.handlers()["modpack_import"](job, cast(Any, context))

    try:
        asyncio.run(run())
    finally:
        job.active = False  # the worker finishes it, done or failed (one attempt)
    return context


def queued(importer: ModpackImportService, data: bytes | io.BytesIO, into: Optional[str] = None) -> Job:
    file = data if isinstance(data, io.BytesIO) else io.BytesIO(data)
    return asyncio.run(importer.import_upload(file, "subido.zip", EDITOR, into=into))


def test_the_job_reads_the_stored_mrpack_off_the_event_loop_and_removes_it(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    importer, _, _ = service(tmp_path)
    job = queued(importer, mrpack(), into="b1")
    threads: list[bool] = []
    real_read = modpack_import.read_upload

    def read(path: Path, *args: Any) -> Any:
        threads.append(threading.current_thread() is threading.main_thread())
        return real_read(path, *args)

    seen: list[tuple[Any, ...]] = []

    async def run_content(content: MrpackContent, actor: Actor, into: Optional[str], source: str) -> ImportReport:
        seen.append((content.game_version, actor.sub, into, source))
        return report(source)

    monkeypatch.setattr(modpack_import, "read_upload", read)
    monkeypatch.setattr(importer, "run_content", run_content)
    context = run_job(importer, job)
    assert threads == [False]  # read in a thread, never on the event loop
    assert seen == [("1.20.1", EDITOR.sub, "b1", "Aventura 2.0")]
    assert context.progressed[:2] == ["Leyendo el archivo", "Identificando los archivos en Modrinth"]
    assert job.checkpoint["import"]["source"] == "Aventura 2.0" and importer.uploads.names() == []


def test_a_prism_zip_and_a_curseforge_zip_are_read_in_the_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importer, _, _ = service(tmp_path, curseforge=True)
    seen: list[tuple[str, str]] = []

    async def run_content(content: MrpackContent, actor: Actor, into: Optional[str], source: str) -> ImportReport:
        seen.append(("content", source))
        return report(source)

    async def run_curseforge(content: CfContent, actor: Actor, into: Optional[str], source: str) -> ImportReport:
        seen.append(("curseforge", source))
        return report(source)

    monkeypatch.setattr(importer, "run_content", run_content)
    monkeypatch.setattr(importer, "run_curseforge", run_curseforge)
    run_job(importer, queued(importer, prism_zip()))
    run_job(importer, queued(importer, cf_pack()))
    assert seen == [("content", "Aventura"), ("curseforge", "Aventura CF 3.0")]
    assert importer.uploads.names() == []


def test_a_file_that_is_not_a_pack_fails_the_job_with_the_reason_and_is_removed(tmp_path: Path) -> None:
    importer, _, _ = service(tmp_path)
    job = queued(importer, b"not a zip")
    with pytest.raises(JobFailed, match="zip"):
        run_job(importer, job)
    assert importer.uploads.names() == []


def test_a_curseforge_zip_without_the_key_fails_the_job_saying_to_export_it(tmp_path: Path) -> None:
    importer, _, _ = service(tmp_path, curseforge=False)
    job = queued(importer, cf_pack())
    with pytest.raises(JobFailed) as failed:
        run_job(importer, job)
    assert str(failed.value) == modpack_import.NO_CURSEFORGE_KEY and importer.uploads.names() == []


def test_a_job_whose_file_is_gone_fails_saying_to_upload_it_again(tmp_path: Path) -> None:
    # A container recreated between the upload and the job takes its temporary disk with it.
    importer, _, _ = service(tmp_path)
    job = queued(importer, mrpack())
    importer.uploads.remove(job.payload["upload"])
    with pytest.raises(JobFailed) as failed:
        run_job(importer, job)
    assert str(failed.value) == modpack_import.UPLOAD_GONE


def test_an_unexpected_failure_still_removes_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importer, _, _ = service(tmp_path)
    job = queued(importer, mrpack())

    async def broken(*args: Any) -> ImportReport:
        raise RuntimeError("mongo went away")

    monkeypatch.setattr(importer, "run_content", broken)
    with pytest.raises(RuntimeError):
        run_job(importer, job)
    assert importer.uploads.names() == []


def test_the_sweep_keeps_what_an_active_import_holds_and_removes_the_rest(tmp_path: Path) -> None:
    importer, jobs, _ = service(tmp_path)
    held = queued(importer, b"held").payload["upload"]
    orphan = importer.uploads.save(io.BytesIO(b"left by a restart"))
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    assert asyncio.run(importer.uploads_sweep(later)) == [orphan]
    assert importer.uploads.names() == [held]
    jobs.jobs[0].active = False  # the job ended without removing it (the process died)
    assert asyncio.run(importer.uploads_sweep(later)) == [held]


def test_the_app_sweeps_at_start_and_stops_when_asked(tmp_path: Path) -> None:
    importer, _, _ = service(tmp_path)
    orphan = importer.uploads.save(io.BytesIO(b"left by a restart"))
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    os.utime(importer.uploads.path_of(orphan), (old, old))

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(importer.run_sweeper(stop, every=3600))
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())
    assert importer.uploads.names() == []
