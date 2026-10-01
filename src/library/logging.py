"""Structured logs (i-7985ee-90f944): one JSON object per line, with the id of the request being
served, so a request can be followed from the proxy to every line it caused."""
import json
import logging
import re
import time
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
# An incoming id is kept only if it cannot break a log line or a header.
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# LogRecord's own attributes; anything else on a record is an `extra` field, logged as is.
_RECORD_FIELDS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "color_message"}

def request_id() -> Optional[str]:
    """The id of the request being served, or None outside a request."""
    return _request_id.get()

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id(),
        }
        line.update({k: v for k, v in vars(record).items() if k not in _RECORD_FIELDS})
        if record.exc_info:
            line["exception"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(line, default=str, ensure_ascii=False)

def configure_logging(level: str = "INFO") -> None:
    """JSON on stderr for the app's loggers and uvicorn's. Uvicorn's access log is replaced by
    the request line of RequestIdMiddleware (run uvicorn with --no-access-log)."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Python warnings (a deprecated library, say) as JSON lines too, not loose text on stderr.
    logging.captureWarnings(True)
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False

class RequestIdMiddleware:
    """Gives every HTTP request an id (the proxy's X-Request-ID if safe, else a new one), returns
    it in the response, and logs one line per request: method, path, status and duration. The
    query string is left out: it can carry login data."""
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = logging.getLogger("modpack.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if SAFE_ID.match(incoming) else uuid4().hex
        token = _request_id.set(rid)
        status = 500
        start = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            self.logger.info("request", extra={
                "method": scope["method"], "path": scope["path"], "status": status,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2)})
            _request_id.reset(token)
