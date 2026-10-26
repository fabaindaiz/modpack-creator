"""How much temporary disk the uploads in flight may take (owner, 2026-10-12).

Starlette spools every uploaded file to the temporary directory (/tmp, or TMPDIR) past 1 MiB, and
deletes it when the request ends. A request reserves its declared Content-Length before its body is
read (uvicorn refuses a body longer than declared); what does not fit is refused at once, with
nothing written. Since O6 (owner, 2026-10-21) an import's file outlives its request, stored for its
job (`src/library/import_uploads.py`): what is stored counts too, read from the disk, so it holds
across restarts, read in a thread: walking the directory blocks (review, 2026-10-22). In process: the
app runs as one uvicorn process.
"""
import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager


class UploadBusy(Exception):
    """The uploads in flight already take the temporary disk this one would need."""


class UploadBudget:
    def __init__(self, max_bytes: int, stored: Callable[[], int] = lambda: 0) -> None:
        self.max_bytes = max_bytes
        self.in_use = 0
        # Every byte ever released: what is released while the disk is read is counted as stored.
        self.released = 0
        # What the uploads kept for their jobs take on disk now.
        self.stored = stored

    @asynccontextmanager
    async def reserve(self, size: int) -> AsyncIterator[None]:
        """Hold `size` bytes while the upload is read and used; UploadBusy if they do not fit."""
        released = self.released
        stored = await asyncio.to_thread(self.stored)
        # A hold released during the walk may be a copy that landed where the walk had passed
        # (review, 2026-10-26): it counts as stored. No await between the check and the hold: two
        # uploads never both take the last room.
        stored += self.released - released
        if self.in_use + stored + size > self.max_bytes:
            raise UploadBusy(f"{self.in_use} of {self.max_bytes} bytes in use")
        self.in_use += size
        try:
            yield
        finally:
            self.in_use -= size
            self.released += size
