import logging
import time
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Callable, Optional, Protocol, runtime_checkable
from src.exceptions import HTTP_422_UNPROCESSABLE_ENTITY
from src.library.auth.roles import operator_only
from src.library.diagnostics import EXPORT_DAYS, DiagnosticsRecorder, header, jsonl
from src.library.utils import utcnow
from src.model.jobs import Job
from src.model.users import Actor
from src.services.jobs import Handler, JobContext, JobFailed, LeaseLost

logger = logging.getLogger("modpack.diagnostics")

@runtime_checkable
class DiagnosticsSource(Protocol):
    """What the export reads: DiagnosticsDatabase, or a test double."""
    def since(self, at: datetime) -> AsyncIterator[dict[str, Any]]: ...

class DiagnosticsService():
    """The diagnostics file (O7): the operator downloads the last days of events as JSON Lines."""
    def __init__(self,
            diagnosticsDB: DiagnosticsSource,
            version: str = "dev",
            clock: Callable[[], datetime] = utcnow,
            recorder: Optional[DiagnosticsRecorder] = None,
            ) -> None:
        self.diagnosticsDB = diagnosticsDB
        self.version = version
        self.clock = clock
        self.recorder = recorder

    @operator_only
    async def export(self, days: int, actor: Actor) -> AsyncIterator[str]:
        """The file's lines, to stream: a header (version and schema), each event of the last `days`
        days oldest first, and an `end` line with the count. The role and the days are checked
        here, before anything is read; a read that fails midway ends the file with `complete:
        false` instead of cutting it (the response has already started)."""
        if days not in EXPORT_DAYS:
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"Los días del diagnóstico son {', '.join(map(str, EXPORT_DAYS))}")
        now = self.clock()
        since = now - timedelta(days=days)

        async def lines() -> AsyncIterator[str]:
            yield jsonl(header(self.version, days, since, now))
            count, complete = 0, True
            try:
                async for event in self.diagnosticsDB.since(since):
                    yield jsonl(event)
                    count += 1
            except Exception:
                logger.warning("reading diagnostics failed midway", exc_info=True)
                complete = False
            yield jsonl({"type": "end", "events": count, "complete": complete,
                         "dropped_since_start": self.recorder.dropped if self.recorder is not None else 0})
        return lines()

def observed(handlers: dict[str, Handler], recorder: DiagnosticsRecorder) -> dict[str, Handler]:
    """The job handlers, each leaving a job event per run with its outcome and duration (O7). The
    worker is untouched: what a handler raises reaches it as before. A job ends failed for good on
    JobFailed, or on any other error at its last attempt; before that it is retried."""
    def wrap(handler: Handler) -> Handler:
        async def run(job: Job, context: JobContext) -> None:
            start = time.perf_counter()
            outcome: str = "done"
            error: Optional[BaseException] = None
            try:
                await handler(job, context)
            except LeaseLost:
                outcome = "lease_lost"
                raise
            except JobFailed as failed:
                outcome, error = "failed", failed
                raise
            except Exception as other:
                outcome = "failed" if job.attempts >= job.max_attempts else "retry"
                error = other
                raise
            finally:
                try:
                    recorder.job(job_kind=job.kind, job_id=str(job.id), attempt=job.attempts, outcome=outcome,
                                 duration_ms=(time.perf_counter() - start) * 1000, error=error)
                except Exception:
                    logger.warning("recording a job run failed", exc_info=True)
        return run
    return {kind: wrap(handler) for kind, handler in handlers.items()}
