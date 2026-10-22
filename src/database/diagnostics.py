from datetime import datetime
from typing import Any, AsyncIterator
from src.library.utils import as_utc
from src.model.diagnostics import DiagnosticEvent

class DiagnosticsDatabase():
    async def insert_many(self, events: list[dict[str, Any]]) -> None:
        """Store a batch of events (O7), validated by the model."""
        await DiagnosticEvent.insert_many([DiagnosticEvent.model_validate(event) for event in events])

    async def since(self, at: datetime) -> AsyncIterator[dict[str, Any]]:
        """The events from `at` (inclusive, aware UTC) on, oldest first, as plain dicts without the
        unset fields nor the id; read with a cursor, never whole into memory."""
        async for event in DiagnosticEvent.find(DiagnosticEvent.at >= at).sort("+at"):
            line = event.model_dump(exclude_none=True, exclude={"id", "revision_id"})
            line["at"] = as_utc(event.at)
            yield line
