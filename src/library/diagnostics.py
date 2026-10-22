"""Usage and error diagnostics (O7): every request, every unhandled error and every job run leaves a
small event, buffered in memory and written to MongoDB in batches, that the operator downloads as
JSON Lines to hand to an agent.

Recording never breaks or slows a request: `add` only appends to a bounded buffer (the oldest event
is dropped, and counted, when it is full), and a background task writes it. A write that fails is
logged and its batch dropped: losing diagnostics is better than holding memory or retrying.

Privacy: the user is an HMAC of `iss` and `sub` under a key derived from SESSION_SECRET, never the
email or the name; a request keeps its route template (the path only when no route matched), never
its query string, body or headers; messages and tracebacks are bounded and pass through `redact`.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import re
import traceback
from collections import deque
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping, Optional
from starlette.types import Scope
from src.library.logging import request_id
from src.library.utils import utcnow

RETENTION_DAYS = 30
EXPORT_DAYS = (1, 7, 30)
DEFAULT_EXPORT_DAYS = 7
MAX_MESSAGE = 2_000
MAX_TRACEBACK = 16_000
MAX_PATH = 200
BUFFER_MAX = 5_000
BATCH_MAX = 500
FLUSH_EVERY = 2.0
SCHEMA_VERSION = 1
# Requests that say nothing about use: the probes and the static assets.
SKIPPED_PREFIXES = ("/health", "/static/")
# The only `extra` fields of a log record an error event keeps (renamed): anything else on a record
# could carry what the log line carries, which the diagnostics do not.
_LOG_EXTRAS = {"job_id": "job_id", "kind": "job_kind"}
_KEY_CONTEXT = b"modpack-creator diagnostics user hash v1"

Event = dict[str, Any]
Sink = Callable[[list[Event]], Awaitable[Any]]
logger = logging.getLogger("modpack.diagnostics")

# What each kind of event holds: the header of the downloaded file repeats it, so an agent reading
# the file cold knows what every field means.
SCHEMA: dict[str, dict[str, str]] = {
    "request": {
        "at": "UTC time the request ended (ISO 8601)",
        "kind": "\"request\"",
        "request_id": "the request's id (X-Request-ID); the same id is on its error event and in the server logs",
        "user": "16 hex chars: HMAC of the person's OIDC iss+sub; the same person keeps it until SESSION_SECRET rotates; null without a session",
        "method": "HTTP method",
        "route": "the route template (/modpacks/{bundle_id}), or the bare path when no route matched; never the query string",
        "status": "HTTP status answered (500 when the app raised)",
        "duration_ms": "time from the request's arrival to its end, in milliseconds",
    },
    "error": {
        "at": "UTC time of the error (ISO 8601)",
        "kind": "\"error\"",
        "source": "\"request\" (an exception no handler answered) or \"log\" (an error logged with its traceback: a job, the scheduler)",
        "request_id": "the request it happened in, if any",
        "user": "as in request events (only for source \"request\")",
        "route": "as in request events (only for source \"request\")",
        "logger": "the logger that wrote it (only for source \"log\")",
        "job_id": "the job it happened in (only for source \"log\", when known)",
        "job_kind": "that job's kind",
        "error_type": "the exception's class",
        "message": f"the exception's message (or the log line), at most {MAX_MESSAGE} chars, secrets and query strings redacted",
        "traceback": f"the traceback's last {MAX_TRACEBACK} chars, redacted",
    },
    "job": {
        "at": "UTC time the run ended (ISO 8601)",
        "kind": "\"job\"",
        "job_kind": "what the job does (server_scan, server_update, modpack_import, ...)",
        "job_id": "the job's id (the panel shows it at /jobs/{job_id})",
        "attempt": "which attempt this run was (1 = first)",
        "outcome": "\"done\", \"retry\" (failed, will run again), \"failed\" (for good) or \"lease_lost\" (another worker took it)",
        "duration_ms": "the run's duration in milliseconds",
        "message": "the error that ended the run, if any (redacted, bounded)",
    },
}

# Secrets that can show up in an exception's text: a bearer token, `key=value` pairs whose key names
# a secret, and any URL's query string.
_REDACTIONS = (
    (re.compile(r"(?i)(bearer\s+)[^\s\"',;]+"), r"\1[redacted]"),
    (re.compile(r"(?i)((?:token|secret|password|passwd|api[_-]?key|authorization|cookie|session)[\"']?\s*[:=]\s*[\"']?)"
                r"(?!bearer\b)[^\s\"'&,;]+"), r"\1[redacted]"),
    (re.compile(r"(https?://[^\s?#\"']+)\?[^\s#\"']*"), r"\1?[query]"),
)


def derive_key(secret: str) -> bytes:
    """The key of the user hash: an HMAC of a fixed context under SESSION_SECRET, so the secret itself
    is never the key and the hash says nothing about it. Rotating the secret renames everyone."""
    return hmac.new(secret.encode(), _KEY_CONTEXT, hashlib.sha256).digest()


def user_hash(key: bytes, iss: str, sub: str) -> str:
    """A person as 16 hex characters: enough to follow one person's use, never who they are."""
    return hmac.new(key, f"{iss}\n{sub}".encode(), hashlib.sha256).hexdigest()[:16]


def session_user(scope: Scope, key: Optional[bytes]) -> Optional[str]:
    """The hashed identity of the request's session (iss + sub, as `actor_from_session` reads it), or
    None: no key, no session, or one without an identity."""
    session = scope.get("session")
    if key is None or not isinstance(session, Mapping):
        return None
    try:
        provider = json.loads(session.get("user") or "")["provider"]
        iss, sub = provider["iss"], provider["sub"]
    except (TypeError, ValueError, KeyError):
        return None
    if not (isinstance(iss, str) and isinstance(sub, str) and iss and sub):
        return None
    return user_hash(key, iss, sub)


def clip(text: str, limit: int) -> str:
    """At most `limit` characters from the start, saying how many were cut."""
    return text if len(text) <= limit else f"{text[:limit]}…[+{len(text) - limit}]"


def clip_tail(text: str, limit: int) -> str:
    """At most `limit` characters from the end (a traceback's last frames and its error)."""
    return text if len(text) <= limit else f"[-{len(text) - limit}]…{text[-limit:]}"


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def route_of(scope: Scope) -> str:
    """The matched route's template; the path (never the query string, which ASGI keeps apart)
    when none matched, bounded."""
    template = getattr(scope.get("route"), "path", None)
    return (template if isinstance(template, str) else str(scope.get("path", "")))[:MAX_PATH]


def request_event(scope: Scope, status: int, duration_ms: float, key: Optional[bytes], at: datetime,
                  rid: Optional[str]) -> Event:
    return {"at": at, "kind": "request", "request_id": rid, "user": session_user(scope, key),
            "method": str(scope.get("method", "")), "route": route_of(scope), "status": status,
            "duration_ms": round(duration_ms, 2)}


def _error_text(error: BaseException) -> tuple[str, str]:
    message = clip(redact(str(error)), MAX_MESSAGE)
    trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    return message, clip_tail(redact(trace), MAX_TRACEBACK)


class DiagnosticsRecorder:
    """The events not yet written, in a bounded buffer, and the task that writes them."""
    def __init__(self, key: Optional[bytes] = None, clock: Callable[[], datetime] = utcnow,
                 maxlen: int = BUFFER_MAX) -> None:
        self.key = key
        self.clock = clock
        self._buffer: deque[Event] = deque(maxlen=maxlen)
        self.dropped = 0

    def configure(self, key: bytes) -> None:
        self.key = key

    def add(self, event: Event) -> None:
        """Keep an event to write; when the buffer is full the oldest goes, counted in `dropped`."""
        if len(self._buffer) == self._buffer.maxlen:
            self.dropped += 1
        self._buffer.append(event)

    def drain(self, limit: int) -> list[Event]:
        """Take up to `limit` events, oldest first."""
        return [self._buffer.popleft() for _ in range(min(limit, len(self._buffer)))]

    def observe_request(self, scope: Scope, status: int, duration_ms: float,
                        error: Optional[BaseException] = None) -> None:
        """RequestIdMiddleware calls this when a request ends: a request event, and an error event
        when the app raised. The probes and static assets leave no request event."""
        now = self.clock()
        rid = request_id()
        path = str(scope.get("path", ""))
        if error is not None:
            message, trace = _error_text(error)
            self.add({"at": now, "kind": "error", "source": "request", "request_id": rid,
                      "user": session_user(scope, self.key), "route": route_of(scope),
                      "error_type": type(error).__name__, "message": message, "traceback": trace})
        if not path.startswith(SKIPPED_PREFIXES):
            self.add(request_event(scope, status, duration_ms, self.key, now, rid))

    def job(self, *, job_kind: str, job_id: str, attempt: int, outcome: str, duration_ms: float,
            error: Optional[BaseException] = None) -> None:
        event: Event = {"at": self.clock(), "kind": "job", "job_kind": job_kind, "job_id": job_id,
                        "attempt": attempt, "outcome": outcome, "duration_ms": round(duration_ms, 2)}
        if error is not None:
            event["message"] = clip(redact(f"{type(error).__name__}: {error}"), MAX_MESSAGE)
        self.add(event)

    async def flush(self, sink: Sink, batch: int = BATCH_MAX) -> int:
        """Write everything buffered, in batches; how many were written. A batch that fails is
        logged and dropped."""
        written = 0
        while True:
            events = self.drain(batch)
            if not events:
                return written
            try:
                await sink(events)
                written += len(events)
            except Exception as error:
                self.dropped += len(events)
                logger.warning(f"writing diagnostics failed, {len(events)} events dropped: {type(error).__name__}: {error}")

    async def run(self, sink: Sink, stop: asyncio.Event, every: float = FLUSH_EVERY) -> None:
        """Write every `every` seconds until `stop` is set, and once more after."""
        while not stop.is_set():
            await self.flush(sink)
            try:
                await asyncio.wait_for(stop.wait(), every)
            except asyncio.TimeoutError:
                pass
        await self.flush(sink)


class DiagnosticsLogHandler(logging.Handler):
    """An error logged with its traceback (`logger.exception`) is an error event: a job that failed,
    the scheduler's loop. Uvicorn's are left out (the middleware already recorded that error), and
    so are this module's (a failing write must not feed itself)."""
    def __init__(self, recorder: DiagnosticsRecorder) -> None:
        super().__init__(level=logging.ERROR)
        self.recorder = recorder

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not record.exc_info or record.exc_info[1] is None:
                return
            if record.name.startswith("uvicorn") or record.name == logger.name:
                return
            error = record.exc_info[1]
            _, trace = _error_text(error)
            event: Event = {"at": self.recorder.clock(), "kind": "error", "source": "log", "logger": record.name,
                            "request_id": request_id(), "error_type": type(error).__name__,
                            "message": clip(redact(f"{record.getMessage()}: {error}"), MAX_MESSAGE),
                            "traceback": trace}
            for name, field in _LOG_EXTRAS.items():
                value = getattr(record, name, None)
                if isinstance(value, (str, int)):
                    event[field] = str(value)
            self.recorder.add(event)
        except Exception:
            self.handleError(record)


def header(version: str, days: int, since: datetime, now: datetime) -> Event:
    """The file's first line: what made it and what each event means."""
    return {"type": "header", "format": "modpack-creator diagnostics", "schema_version": SCHEMA_VERSION,
            "app_version": version, "generated_at": now.isoformat(), "days": days, "since": since.isoformat(),
            "retention_days": RETENTION_DAYS,
            "notes": ("One JSON object per line, oldest first: this header, then one line per event (its "
                      "`kind` says which schema applies; fields without a value are left out), then an "
                      "`end` line with the count. `complete: false` on it means reading stopped early. "
                      "`dropped_since_start` counts events this process lost (buffer full or a failed "
                      "write) since it started. No query strings, bodies, headers, emails or names are "
                      "recorded; users are pseudonymous hashes."),
            "schema": SCHEMA}


def jsonl(line: Mapping[str, Any]) -> str:
    """One line of the file: datetimes as ISO 8601, anything else odd as its text."""
    return json.dumps({k: v.isoformat() if isinstance(v, datetime) else v for k, v in line.items()},
                      default=str, ensure_ascii=False) + "\n"


# The process's recorder: the middleware, the log handler and the job wrapper write to it, and the
# lifespan writes it to MongoDB (main.py).
RECORDER = DiagnosticsRecorder()

