"""Uploaded import files waiting for their job (O6, owner 2026-10-21).

The request only stores the upload and queues the import; the `modpack_import` job reads it and
removes it when it ends, done or failed. Starlette's own copy of the upload is an unnamed temporary
file (`tempfile.TemporaryFile`, past 1 MiB) closed with the request, so the request copies it here
first. What no active job holds, left by a restart or by a request that never queued its job, is
removed by the sweep once past a grace that covers a request between storing and queuing.

Everything here is blocking file work: the service calls it in a thread, never on the event loop.
"""
import re
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO, Literal, Optional, Union
from uuid import uuid4

from src.library.cf_import import CfContent, is_curseforge, read_cf_zip
from src.library.mrpack_import import MrpackContent, MrpackError, MrpackLimits, read_mrpack
from src.library.prism_import import PrismLimits, is_prism, read_prism

# Beside Starlette's spooled uploads: the same temporary disk IMPORT_TMP_MAX_MIB bounds.
DEFAULT_DIR = Path(tempfile.gettempdir()) / "modpack-imports"
NAME = re.compile(r"[0-9a-f]{32}\.upload")
UploadKind = Literal["mrpack", "prism", "curseforge"]


class UploadStore:
    def __init__(self, directory: Path = DEFAULT_DIR) -> None:
        self.directory = directory

    def save(self, file: IO[bytes]) -> str:
        """Copy the upload, from its start, into the store; the name its job carries."""
        self.directory.mkdir(parents=True, exist_ok=True)
        name = f"{uuid4().hex}.upload"
        file.seek(0)
        try:
            with open(self.directory / name, "xb") as out:
                shutil.copyfileobj(file, out, 1024 * 1024)
        except BaseException:
            self.remove(name)
            raise
        return name

    def path_of(self, name: str) -> Path:
        """The stored file of `name`; ValueError for a name the store never gives (a path, say)."""
        if not NAME.fullmatch(name):
            raise ValueError(f"not an upload name: {name!r}")
        return self.directory / name

    def remove(self, name: str) -> None:
        self.path_of(name).unlink(missing_ok=True)

    def names(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(p.name for p in self.directory.iterdir() if NAME.fullmatch(p.name))

    def stored_bytes(self) -> int:
        """What the stored uploads take on disk now."""
        total = 0
        for name in self.names():
            try:
                total += self.path_of(name).stat().st_size
            except FileNotFoundError:
                continue
        return total

    def sweep(self, keep: set[str], now: datetime, grace: timedelta) -> list[str]:
        """Remove the stored uploads not in `keep` (those an active job holds) and untouched for
        longer than `grace`; the names removed."""
        removed = []
        for name in self.names():
            try:
                touched = self.path_of(name).stat().st_mtime
            except FileNotFoundError:
                continue
            if name in keep or now.timestamp() - touched < grace.total_seconds():
                continue
            self.remove(name)
            removed.append(name)
        return removed


def read_upload(path: Path, limits: MrpackLimits = MrpackLimits(),
                prism_limits: PrismLimits = PrismLimits()) -> tuple[UploadKind, Union[MrpackContent, CfContent]]:
    """
    What a stored upload is, told apart by content (T5, T6), and what it holds: a Prism instance is
    read from the file (up to 1 GiB, its worlds never opened); a CurseForge zip or an .mrpack in
    memory, up to `limits.max_bytes`. MrpackError naming why if it is none of them or breaks a limit.
    CPU-bound (it hashes every jar): run it in a thread.
    """
    with open(path, "rb") as file:
        if is_prism(file):
            return "prism", read_prism(file, prism_limits)
        data = _capped(file, limits)
        file.seek(0)
        if is_curseforge(file):
            return "curseforge", read_cf_zip(data, limits)
        return "mrpack", read_mrpack(data, limits)


def _capped(file: IO[bytes], limits: MrpackLimits) -> bytes:
    """Up to one byte past the limit: enough for the reader to refuse it naming the limit."""
    data = file.read(limits.max_bytes + 1)
    if len(data) > limits.max_bytes:
        raise MrpackError(f"El archivo pesa más de {limits.max_bytes_label}")
    return data


def remove_quietly(store: Optional[UploadStore], name: Optional[str]) -> None:
    """Remove a stored upload, if there is one by that name; a bad name is ignored."""
    if store is None or not name:
        return
    try:
        store.remove(name)
    except ValueError:
        return
