"""Un panel de Pterodactyl falso con estado, para probar la actualización de un servidor.

Simula lo que la actualización observa y cambia: los archivos del servidor, su estado de energía
(con la caché del panel: un `stop` no deja el servidor offline al instante), los backups (que
terminan después de crearse), el cupo de backups y un servidor que se cae al arrancar. Registra
cada escritura, para afirmar que el modo simulación no escribe nada.
"""
import hashlib
from src.exceptions import UpstreamError
from datetime import datetime, timezone
from typing import Optional

from src.model.pterodactyl import PterodactylBackup, PterodactylFile, ServerResources, ServerState

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def sha512(data: bytes) -> str:
    return hashlib.sha512(data).hexdigest()


class StatefulPanel:
    def __init__(self, files: dict[str, bytes], backup_limit: int = 2, crash_after_start: Optional[int] = None,
                 ignores_stop: bool = False, backup_fails: bool = False) -> None:
        self.files: dict[str, bytes] = dict(files)
        self.folders: set[str] = {"/", "/mods"}
        self.state = "running"
        self.uptime = 1_000_000
        self.status: Optional[str] = None
        self.backup_limit = backup_limit
        self.backup_list: list[PterodactylBackup] = []
        self.writes: list[str] = []
        self.backed_up: dict[str, dict[str, bytes]] = {}
        # Lo que puede salir mal.
        self.crash_after_start = crash_after_start  # se cae tras N lecturas de `running`
        self.ignores_stop = ignores_stop
        self.backup_fails = backup_fails
        self._running_reads = 0

    # Lecturas.

    async def server_files(self, server_id: str, directory: str) -> list[PterodactylFile]:
        prefix = directory.rstrip("/") + "/"
        # As Wings: listing a directory that does not exist is a 404, which the panel passes on
        # (A1, docs/research/2026-10-09-flujo-actualizacion-y-caches.md). It returned [] before.
        if directory.rstrip("/") not in {f.rstrip("/") for f in self.folders} | {""} \
                and not any(path.startswith(prefix) for path in self.files):
            raise UpstreamError("Pterodactyl", 404, f"{directory}: directory not found")
        names = {path[len(prefix):] for path in self.files if path.startswith(prefix) and "/" not in path[len(prefix):]}
        return [PterodactylFile(name=n, size=len(self.files[prefix + n]), mode_bits="644", is_file=True, is_symlink=False,
                                mimetype="application/jar", created_at=T0, modified_at=T0) for n in sorted(names)]

    async def server_files_download(self, server_id: str, filepath: str) -> bytes:
        return self.files[filepath]

    async def server_state(self, server_id: str) -> ServerState:
        status = self.status
        if status == "restoring_backup":
            self.status = None  # la restauración termina entre una lectura y la siguiente
        return ServerState(status=status, is_suspended=False, is_installing=False, is_transferring=False,
                           backup_limit=self.backup_limit)

    async def resources(self, server_id: str) -> ServerResources:
        if self.state == "stopping":
            self.state = "offline"
            return ServerResources(current_state="stopping", is_suspended=False, uptime_ms=self.uptime)
        if self.state == "starting":
            self.state, self.uptime, self._running_reads = "running", 0, 0
            return ServerResources(current_state="starting", is_suspended=False, uptime_ms=0)
        if self.state == "running":
            self._running_reads += 1
            if self.crash_after_start is not None and self._running_reads > self.crash_after_start:
                self.state = "offline"
            else:
                self.uptime += 15_000
        return ServerResources(current_state=self.state, is_suspended=False, uptime_ms=self.uptime)

    async def backups(self, server_id: str) -> tuple[list[PterodactylBackup], int]:
        return list(self.backup_list), len([b for b in self.backup_list if b.is_successful or b.completed_at is None])

    async def backup_get(self, server_id: str, backup_id: str) -> PterodactylBackup:
        backup = next(b for b in self.backup_list if b.uuid == backup_id)
        if backup.completed_at is None:
            backup.completed_at, backup.is_successful = T0, not self.backup_fails
        return backup

    # Escrituras.

    async def server_command(self, server_id: str, command: str) -> bool:
        self.writes.append(f"command {command}")
        return True

    async def server_power(self, server_id: str, signal: str) -> bool:
        self.writes.append(f"power {signal}")
        if signal == "stop" and not self.ignores_stop and self.state == "running":
            self.state = "stopping"
        elif signal == "kill":
            self.state = "offline"
        elif signal == "start" and self.state == "offline":
            self.state = "starting"
        return True

    async def backup_create(self, server_id: str, name: str, locked: bool) -> PterodactylBackup:
        self.writes.append(f"backup {name}")
        if len([b for b in self.backup_list if b.is_successful]) >= self.backup_limit:
            raise RuntimeError("TooManyBackupsException")
        backup = PterodactylBackup(uuid=f"b-{len(self.backup_list) + 1}", name=name, is_successful=False, is_locked=locked,
                                   bytes=1, created_at=T0, completed_at=None)
        self.backup_list.append(backup)
        self.backed_up[backup.uuid] = dict(self.files)
        return backup

    async def backup_lock_toggle(self, server_id: str, backup_id: str) -> PterodactylBackup:
        self.writes.append(f"lock {backup_id}")
        backup = next(b for b in self.backup_list if b.uuid == backup_id)
        backup.is_locked = not backup.is_locked
        return backup

    async def backup_restore(self, server_id: str, backup_id: str, truncate: bool) -> bool:
        self.writes.append(f"restore {backup_id} truncate={truncate}")
        assert self.state == "offline" and self.status is None
        self.files = dict(self.backed_up[backup_id])
        self.status = "restoring_backup"
        return True

    async def files_create_folder(self, server_id: str, root: str, name: str) -> bool:
        self.writes.append(f"mkdir {root.rstrip('/')}/{name}")
        self.folders.add(f"{root.rstrip('/')}/{name}")
        return True

    async def server_files_upload(self, server_id: str, directory: str, filename: str, fileraw: bytes) -> bool:
        self.writes.append(f"upload {directory}/{filename}")
        self.files[f"{directory.rstrip('/')}/{filename}"] = fileraw
        return True

    async def server_files_upload_many(self, server_id: str, directory: str, files: list[tuple[str, bytes]]) -> bool:
        self.writes.append(f"upload {directory}: {', '.join(name for name, _ in files)}")
        for filename, fileraw in files:
            self.files[f"{directory.rstrip('/')}/{filename}"] = fileraw
        return True

    async def files_rename(self, server_id: str, root: str, pairs: list[tuple[str, str]]) -> bool:
        for source, target in pairs:
            self.writes.append(f"rename {source} -> {target}")
            base = root.rstrip("/")
            self.files[f"{base}/{target}"] = self.files.pop(f"{base}/{source}")
        return True

    def mods(self) -> dict[str, str]:
        return {p.rsplit("/", 1)[-1]: sha512(d)[:8] for p, d in self.files.items() if p.startswith("/mods/")}
