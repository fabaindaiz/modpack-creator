"""Diagnóstico de uso y errores (O7, d-7985ee-ea4185): cada petición, cada error no atendido y cada
trabajo deja un evento que el operador baja como JSON Lines para dárselo a un agente.

Lo que se prueba sin MongoDB: armar cada evento (la plantilla de la ruta, nunca la query; el usuario
como hash), acotar y limpiar mensajes y tracebacks, que grabar nunca rompa ni frene una petición, el
búfer acotado, el manejador de logs, el envoltorio de los trabajos y el archivo (encabezado, eventos,
cierre). Lo que toca la base está en `tests/test_diagnostics_db.py`.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional, cast

import pytest
from dependency_injector import providers
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.library.diagnostics import (
    DEFAULT_EXPORT_DAYS,
    EXPORT_DAYS,
    MAX_MESSAGE,
    MAX_TRACEBACK,
    DiagnosticsLogHandler,
    DiagnosticsRecorder,
    clip,
    clip_tail,
    derive_key,
    redact,
    request_event,
    user_hash,
)
from src.library.logging import RequestIdMiddleware
from src.model.jobs import Job
from src.services.diagnostics import DiagnosticsService, observed
from src.services.jobs import JobContext, JobFailed, LeaseLost, RetryLater
from tests._actors import EDITOR, OPERATOR, session_for
from tests._session import session_cookie

KEY = derive_key("test-session-secret")
T0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def session(iss: str = "https://idp", sub: str = "u-1", email: str = "ana@example.org") -> dict[str, Any]:
    return {"user": json.dumps({"provider": {"iss": iss, "sub": sub}, "email": email, "name": "Ana"})}


# El usuario va como hash.

def test_the_user_hash_is_stable_short_and_depends_on_the_key() -> None:
    first = user_hash(KEY, "https://idp", "u-1")
    assert first == user_hash(KEY, "https://idp", "u-1")
    assert len(first) == 16 and int(first, 16) >= 0
    assert first != user_hash(KEY, "https://idp", "u-2")
    assert first != user_hash(KEY, "https://other", "u-1")
    assert first != user_hash(derive_key("another-secret"), "https://idp", "u-1")


def test_the_key_is_derived_and_is_not_the_secret() -> None:
    assert KEY == derive_key("test-session-secret")
    assert b"test-session-secret" not in KEY and len(KEY) == 32


def test_a_request_event_has_the_route_template_and_never_the_query_nor_the_identity() -> None:
    scope = {"type": "http", "method": "GET", "path": "/modpacks/abc123", "query_string": b"code=secret-login",
             "route": type("R", (), {"path": "/modpacks/{bundle_id}"})(), "session": session()}
    event = request_event(scope, 200, 12.345, KEY, T0, "rid-1")
    assert event == {"at": T0, "kind": "request", "request_id": "rid-1", "user": user_hash(KEY, "https://idp", "u-1"),
                     "method": "GET", "route": "/modpacks/{bundle_id}", "status": 200, "duration_ms": 12.35}
    text = json.dumps(event, default=str)
    for private in ("secret-login", "ana@example.org", "Ana", "u-1", "abc123"):
        assert private not in text


def test_without_a_route_the_path_is_kept_bounded_and_without_a_session_there_is_no_user() -> None:
    scope = {"type": "http", "method": "GET", "path": "/nada/" + "x" * 500, "query_string": b"a=1"}
    event = request_event(scope, 404, 1.0, KEY, T0, None)
    assert event["user"] is None and event["route"].startswith("/nada/") and len(event["route"]) <= 200


# Acotar y limpiar.

def test_clip_keeps_the_head_and_says_how_much_it_cut() -> None:
    assert clip("abc", 10) == "abc"
    cut = clip("a" * 50, 10)
    assert cut.startswith("a" * 10) and cut.endswith("[+40]")


def test_clip_tail_keeps_the_end_of_a_traceback() -> None:
    cut = clip_tail("x" * 100 + "ValueError: boom", 20)
    assert cut.endswith("ValueError: boom") and cut.startswith("[-") and len(cut) < 40


def test_redact_hides_tokens_and_query_strings() -> None:
    text = redact("GET https://panel/api/x?token=abc&b=2 failed; Authorization: Bearer s3cr3t; api_key=k1 password=p")
    for secret in ("abc", "s3cr3t", "k1", "=p", "b=2"):
        assert secret not in text
    assert "https://panel/api/x" in text


# El búfer y la escritura: nunca rompen ni frenan.

def test_the_buffer_is_bounded_and_counts_what_it_drops() -> None:
    recorder = DiagnosticsRecorder(KEY, maxlen=3)
    for n in range(5):
        recorder.add({"at": T0, "kind": "request", "n": n})
    assert [e["n"] for e in recorder.drain(10)] == [2, 3, 4]
    assert recorder.dropped == 2


def test_flush_writes_in_batches_and_a_failing_sink_is_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    recorder = DiagnosticsRecorder(KEY)
    written: list[list[dict[str, Any]]] = []

    async def sink(events: list[dict[str, Any]]) -> None:
        written.append(events)

    async def broken(events: list[dict[str, Any]]) -> None:
        raise RuntimeError("mongo down")

    for n in range(3):
        recorder.add({"at": T0, "kind": "request", "n": n})
    assert asyncio.run(recorder.flush(sink, batch=2)) == 3
    assert [len(batch) for batch in written] == [2, 1]
    recorder.add({"at": T0, "kind": "request"})
    with caplog.at_level(logging.WARNING, logger="modpack.diagnostics"):
        assert asyncio.run(recorder.flush(broken)) == 0
    assert recorder.dropped == 1 and "mongo down" in caplog.text


def test_run_flushes_until_stopped_and_once_more_at_the_end() -> None:
    recorder = DiagnosticsRecorder(KEY)
    written: list[dict[str, Any]] = []

    async def sink(events: list[dict[str, Any]]) -> None:
        written.extend(events)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(recorder.run(sink, stop, every=0.01))
        recorder.add({"at": T0, "kind": "request", "n": 1})
        await asyncio.sleep(0.05)
        recorder.add({"at": T0, "kind": "request", "n": 2})
        stop.set()
        await task

    asyncio.run(scenario())
    assert [e["n"] for e in written] == [1, 2]


def app_with(recorder: DiagnosticsRecorder, on_finish: Any = None) -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware, on_finish=on_finish or recorder.observe_request)

    @app.get("/items/{item_id}")
    async def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    @app.get("/boom")
    async def boom() -> None:
        raise ValueError("it broke here")

    @app.get("/missing")
    async def missing() -> None:
        raise HTTPException(status_code=404, detail="no")

    return TestClient(app, raise_server_exceptions=False)


def test_every_request_leaves_an_event_with_its_route_and_id() -> None:
    recorder = DiagnosticsRecorder(KEY)
    response = app_with(recorder).get("/items/42?token=zq-secret")
    [event] = recorder.drain(10)
    assert (event["kind"], event["route"], event["status"], event["method"]) == ("request", "/items/{item_id}", 200, "GET")
    assert event["request_id"] == response.headers["X-Request-ID"]
    # Not hex, so a random request id cannot contain it.
    assert "zq-secret" not in json.dumps(event, default=str) and "42" not in event["route"]


def test_a_handled_error_is_only_a_request() -> None:
    recorder = DiagnosticsRecorder(KEY)
    app_with(recorder).get("/missing")
    assert [(e["kind"], e["status"]) for e in recorder.drain(10)] == [("request", 404)]


def test_an_unhandled_error_leaves_its_traceback_with_the_request_id_and_route() -> None:
    recorder = DiagnosticsRecorder(KEY)
    response = app_with(recorder).get("/boom")
    assert response.status_code == 500
    events = recorder.drain(10)
    error = next(e for e in events if e["kind"] == "error")
    request = next(e for e in events if e["kind"] == "request")
    assert (error["error_type"], error["message"], error["route"], error["source"]) == (
        "ValueError", "it broke here", "/boom", "request")
    assert error["request_id"] == request["request_id"] == response.headers["X-Request-ID"]
    assert "raise ValueError" in error["traceback"] and request["status"] == 500


def test_an_error_s_message_and_traceback_are_bounded() -> None:
    recorder = DiagnosticsRecorder(KEY)
    try:
        raise RuntimeError("m" * (MAX_MESSAGE * 3))
    except RuntimeError as error:
        recorder.observe_request({"type": "http", "method": "GET", "path": "/x"}, 500, 1.0, error)
    error_event = next(e for e in recorder.drain(10) if e["kind"] == "error")
    assert len(error_event["message"]) < MAX_MESSAGE + 20
    assert len(error_event["traceback"]) <= MAX_TRACEBACK + 20


def test_a_failing_recorder_never_breaks_the_request(caplog: pytest.LogCaptureFixture) -> None:
    def broken(*args: Any) -> None:
        raise RuntimeError("recorder broke")

    with caplog.at_level(logging.WARNING, logger="modpack.request"):
        response = app_with(DiagnosticsRecorder(KEY), on_finish=broken).get("/items/1")
    assert response.status_code == 200 and "recorder broke" in caplog.text


def test_the_middleware_without_a_recorder_works_as_before() -> None:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    assert TestClient(app).get("/ok").status_code == 200


# Los errores que se registran con traceback fuera de una petición (un trabajo, el programador).

def test_an_error_logged_with_its_traceback_is_an_event_and_warnings_or_uvicorn_are_not() -> None:
    recorder = DiagnosticsRecorder(KEY)
    handler = DiagnosticsLogHandler(recorder)
    logger = logging.getLogger("modpack.test.diagnostics")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        try:
            raise KeyError("server_id")
        except KeyError:
            logger.exception("job failed", extra={"job_id": "j1", "kind": "server_scan", "email": "ana@example.org"})
            logger.warning("only a warning", exc_info=True)
        logger.error("an error without traceback")
        uvicorn = logging.getLogger("uvicorn.error")
        uvicorn.addHandler(handler)
        try:
            raise ValueError("seen by the middleware already")
        except ValueError:
            uvicorn.exception("Exception in ASGI application")
        uvicorn.removeHandler(handler)
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
    [event] = recorder.drain(10)
    assert (event["kind"], event["source"], event["logger"], event["error_type"], event["job_id"], event["job_kind"]) == (
        "error", "log", "modpack.test.diagnostics", "KeyError", "j1", "server_scan")
    assert event["message"].startswith("job failed") and "raise KeyError" in event["traceback"]
    assert "ana@example.org" not in json.dumps(event, default=str)


# Los trabajos.

def job(kind: str = "server_scan", attempts: int = 1, max_attempts: int = 3) -> Job:
    return Job.model_construct(id="j1", kind=kind, attempts=attempts, max_attempts=max_attempts, payload={},
                               checkpoint={}, created_at=T0)


@pytest.mark.parametrize("raised, attempts, outcome, error", [
    (None, 1, "done", None),
    (JobFailed("no bundle"), 1, "failed", "JobFailed: no bundle"),
    (RetryLater(timedelta(seconds=5), "429"), 1, "retry", "RetryLater: 429"),
    (RetryLater(timedelta(seconds=5), "429"), 3, "failed", "RetryLater: 429"),
    (ValueError("bad"), 2, "retry", "ValueError: bad"),
    (ValueError("bad"), 3, "failed", "ValueError: bad"),
    (LeaseLost("gone"), 1, "lease_lost", None),
])
def test_each_job_run_leaves_an_event_with_its_outcome(raised: Optional[Exception], attempts: int, outcome: str,
                                                       error: Optional[str]) -> None:
    recorder = DiagnosticsRecorder(KEY)

    async def handler(job: Job, context: Any) -> None:
        if raised is not None:
            raise raised

    wrapped = observed({"server_scan": handler}, recorder)["server_scan"]

    async def scenario() -> None:
        try:
            await wrapped(job(attempts=attempts), cast(JobContext, None))
        except Exception as caught:
            assert caught is raised  # the worker still sees what the handler raised
        else:
            assert raised is None

    asyncio.run(scenario())
    [event] = recorder.drain(10)
    assert (event["kind"], event["job_kind"], event["job_id"], event["attempt"], event["outcome"]) == (
        "job", "server_scan", "j1", attempts, outcome)
    assert event.get("message") == error and isinstance(event["duration_ms"], float)


# El archivo.

class FakeDiagnosticsDB:
    def __init__(self, events: list[dict[str, Any]], fail_after: Optional[int] = None) -> None:
        self.events = events
        self.fail_after = fail_after
        self.asked: list[datetime] = []

    async def insert_many(self, events: list[dict[str, Any]]) -> None:
        self.events.extend(events)

    async def since(self, at: datetime) -> AsyncIterator[dict[str, Any]]:
        self.asked.append(at)
        for n, event in enumerate(self.events):
            if self.fail_after is not None and n >= self.fail_after:
                raise RuntimeError("cursor lost")
            yield event


async def collect(lines: AsyncIterator[str]) -> list[dict[str, Any]]:
    return [json.loads(line) async for line in lines]


def export(db: FakeDiagnosticsDB, days: int, actor: Any = OPERATOR) -> list[dict[str, Any]]:
    service = DiagnosticsService(db, version="0.3.2", clock=lambda: T0, recorder=DiagnosticsRecorder(KEY))

    async def scenario() -> list[dict[str, Any]]:
        return await collect(await service.export(days, actor=actor))
    return asyncio.run(scenario())


def test_the_file_starts_with_a_header_that_explains_each_event_and_ends_with_a_count() -> None:
    events: list[dict[str, Any]] = [{"at": T0 - timedelta(hours=1), "kind": "request", "method": "GET", "route": "/", "status": 200,
               "duration_ms": 3.0, "request_id": "r1", "user": "abcd"}]
    db = FakeDiagnosticsDB(events)
    header, line, end = export(db, 7)
    assert db.asked == [T0 - timedelta(days=7)]
    assert header["type"] == "header" and header["app_version"] == "0.3.2" and header["days"] == 7
    assert header["since"] == (T0 - timedelta(days=7)).isoformat() and header["generated_at"] == T0.isoformat()
    assert set(header["schema"]) == {"request", "error", "job"}
    for kind in ("request", "error", "job"):
        assert {"at", "kind"} <= set(header["schema"][kind])
    assert "status" in header["schema"]["request"] and "traceback" in header["schema"]["error"]
    assert "outcome" in header["schema"]["job"] and "user" in header["schema"]["request"]
    assert line == {**events[0], "at": events[0]["at"].isoformat()}
    assert end == {"type": "end", "events": 1, "complete": True, "dropped_since_start": 0}


def test_a_cursor_that_fails_midway_ends_the_file_saying_it_is_incomplete() -> None:
    events = [{"at": T0, "kind": "request"}, {"at": T0, "kind": "request"}]
    lines = export(FakeDiagnosticsDB(events, fail_after=1), 1)
    assert [line.get("type") for line in lines] == ["header", None, "end"]
    assert lines[-1]["complete"] is False and lines[-1]["events"] == 1


def test_only_an_operator_downloads_and_the_check_runs_before_any_read() -> None:
    db = FakeDiagnosticsDB([])
    with pytest.raises(HTTPException) as denied:
        export(db, 7, actor=EDITOR)
    assert denied.value.status_code == 403 and db.asked == []


@pytest.mark.parametrize("days", [0, 2, 31, -1])
def test_the_days_are_one_of_the_offered(days: int) -> None:
    with pytest.raises(HTTPException) as refused:
        export(FakeDiagnosticsDB([]), days)
    assert refused.value.status_code == 422


def test_the_offered_days() -> None:
    assert EXPORT_DAYS == (1, 7, 30) and DEFAULT_EXPORT_DAYS == 7


# El panel.

class FakeDiagnostics:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def export(self, days: int, actor: Any) -> AsyncIterator[str]:
        if not actor.is_operator:  # as `operator_only` does in the real service
            raise HTTPException(status_code=403, detail="This action requires the operator role")
        self.calls.append(days)

        async def lines() -> AsyncIterator[str]:
            yield '{"type": "header"}\n'
            yield '{"type": "end"}\n'
        return lines()


@pytest.fixture
def panel() -> Any:
    import main
    from tests.test_web_activity import setup

    def make(actor: Any) -> tuple[TestClient, FakeDiagnostics]:
        client, *_ = setup(actor)
        fake = FakeDiagnostics()
        main.container.diagnosticsService.override(providers.Object(fake))
        return client, fake
    yield make
    for name in ("diagnosticsService", "jobService", "auditService", "pterodactylService", "modpackService",
                 "modrinthService"):
        getattr(main.container, name).reset_override()


def test_an_operator_downloads_the_file_as_an_attachment(panel: Any) -> None:
    client, fake = panel(OPERATOR)
    response = client.get("/audit/diagnostico", params={"dias": 30})
    assert response.status_code == 200 and fake.calls == [30]
    assert response.headers["content-type"].startswith("application/x-ndjson")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="diagnostico-') and disposition.endswith('-30d.jsonl"')
    assert response.headers["cache-control"] == "no-store"
    assert response.text.splitlines() == ['{"type": "header"}', '{"type": "end"}']


def test_the_default_is_seven_days(panel: Any) -> None:
    client, fake = panel(OPERATOR)
    assert client.get("/audit/diagnostico").status_code == 200 and fake.calls == [7]


def test_an_editor_gets_a_403(panel: Any) -> None:
    client, fake = panel(EDITOR)
    assert client.get("/audit/diagnostico").status_code == 403 and fake.calls == []


def test_the_audit_page_offers_the_download_to_an_operator_only(panel: Any) -> None:
    client, _ = panel(OPERATOR)
    page = client.get("/audit").text
    assert "Descargar diagnóstico (.jsonl)" in page and 'action="/audit/diagnostico"' in page
    for days in EXPORT_DAYS:
        assert f'value="{days}"' in page
    client, _ = panel(EDITOR)
    page = client.get("/audit").text
    assert "Descargar diagnóstico" not in page and "/audit/diagnostico" not in page
