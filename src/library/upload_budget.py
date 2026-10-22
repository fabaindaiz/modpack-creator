"""How much temporary disk the uploads in flight may take (owner, 2026-10-12).

Starlette spools every uploaded file to the temporary directory (/tmp, or TMPDIR) past 1 MiB, and
deletes it when the request ends. A request reserves its declared Content-Length before its body is
read (uvicorn refuses a body longer than declared); what does not fit is refused at once, with
nothing written. Since O6 (owner, 2026-10-21) an import's file outlives its request, stored for its
job (`src/library/import_uploads.py`): what is stored counts too, read from the disk, so it holds
across restarts. In process: the app runs as one uvicorn process.
"""
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class UploadBusy(Exception):
    """The uploads in flight already take the temporary disk this one would need."""


class UploadBudget:
    def __init__(self, max_bytes: int, stored: Callable[[], int] = lambda: 0) -> None:
        self.max_bytes = max_bytes
        self.in_use = 0
        # What the uploads kept for their jobs take on disk now.
        self.stored = stored

    @contextmanager
    def reserve(self, size: int) -> Iterator[None]:
        """Hold `size` bytes while the upload is read and used; UploadBusy if they do not fit."""
        if self.in_use + self.stored() + size > self.max_bytes:
            raise UploadBusy(f"{self.in_use} of {self.max_bytes} bytes in use")
        self.in_use += size
        try:
            yield
        finally:
            self.in_use -= size
