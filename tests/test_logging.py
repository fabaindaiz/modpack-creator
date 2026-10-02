"""Logs en JSON con el request_id de cada request (i-7985ee-90f944).

Cada request lleva un id: el que manda el proxy en `X-Request-ID` si es seguro, o uno nuevo. Se
devuelve en la respuesta y aparece en toda línea de log escrita mientras se atiende, para seguir
un request de punta a punta. La línea del request no incluye la query: puede llevar datos del
login.
"""
import json
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.library.logging import JsonFormatter, RequestIdMiddleware, request_id


class Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


def app_with_logs() -> tuple[TestClient, Collect]:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/work")
    async def work() -> dict[str, str | None]:
        logging.getLogger("modpack.test").info("doing work", extra={"bundle_id": "b1"})
        return {"request_id": request_id()}

    collect = Collect()
    for name in ("modpack.test", "modpack.request"):
        logger = logging.getLogger(name)
        logger.handlers = [collect]
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return TestClient(app), collect


def test_every_line_of_a_request_carries_its_id_and_the_response_returns_it() -> None:
    client, collect = app_with_logs()
    response = client.get("/work?code=secret-login-code")
    rid = response.headers["X-Request-ID"]
    assert response.json() == {"request_id": rid} and len(rid) == 32
    work, done = collect.lines
    assert (work["message"], work["request_id"], work["bundle_id"]) == ("doing work", rid, "b1")
    assert (done["request_id"], done["method"], done["path"], done["status"]) == (rid, "GET", "/work", 200)
    assert isinstance(done["duration_ms"], float)
    assert "secret-login-code" not in json.dumps(collect.lines)


def test_a_safe_incoming_id_is_kept_and_an_unsafe_one_is_replaced() -> None:
    client, _ = app_with_logs()
    assert client.get("/work", headers={"X-Request-ID": "proxy-abc_123"}).headers["X-Request-ID"] == "proxy-abc_123"
    for unsafe in ("x" * 200, "evil\nINJECTED", "a b"):
        assert client.get("/work", headers={"X-Request-ID": unsafe}).headers["X-Request-ID"] != unsafe


def test_two_requests_get_different_ids() -> None:
    client, _ = app_with_logs()
    assert client.get("/work").headers["X-Request-ID"] != client.get("/work").headers["X-Request-ID"]


def test_a_line_outside_a_request_has_no_id_and_an_exception_is_kept() -> None:
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed %s", ("here",), __import__("sys").exc_info())
    line = json.loads(formatter.format(record))
    assert (line["level"], line["message"], line["request_id"]) == ("ERROR", "failed here", None)
    assert "ValueError: boom" in line["exception"]


def test_python_warnings_become_json_lines_and_uvicorns_access_log_stays_off() -> None:
    import warnings

    from src.library.logging import configure_logging

    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        # pytest captures warnings itself; inside its own catch_warnings the hook that
        # captureWarnings installs is the one in force, as it is in the running app.
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            # Importing main already configured logging; captureWarnings(True) is a no-op twice.
            logging.captureWarnings(False)
            configure_logging("INFO")
            collect = Collect()
            root.handlers = [collect]
            warnings.warn("old api", DeprecationWarning, stacklevel=1)
        assert any("old api" in line["message"] for line in collect.lines)
        access = logging.getLogger("uvicorn.access")
        assert access.propagate is False and access.handlers == []
        record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "hi", None, None)
        record.color_message = "\x1b[32mhi\x1b[0m"
        assert "color_message" not in json.loads(JsonFormatter().format(record))
    finally:
        logging.captureWarnings(False)
        root.handlers, level = saved
        root.setLevel(level)


def test_an_unhandled_error_answers_500_with_the_request_id() -> None:
    """The panel shows a 500 with its id (static/app.js): the error response, made before
    Starlette's own outside this middleware, carries it too. The exception still propagates."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    response = TestClient(app, raise_server_exceptions=False).get("/boom", headers={"X-Request-ID": "abc-123"})
    assert response.status_code == 500
    assert response.headers["x-request-id"] == "abc-123"
