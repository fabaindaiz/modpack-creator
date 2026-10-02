"""Update a server to a build (i-7985ee-495708), and roll it back on request.

The procedure of research/2026-10-operacion-pterodactyl.md, with the owner's decisions:
- on request only, without warning players: save-all and stop (d-7985ee-c2adc8);
- the API downloads each new jar, checks its sha512 against the build and uploads it to a staging
  folder while the server still runs (d-7985ee-a13b33);
- five minutes stable count as success; without a backup slot nothing starts (d-7985ee-5cc6ee);
- on failure the server is stopped and a person chooses rollback A or B (d-7985ee-5445a0);
- a jar the build does not explain stops the update: import first (d-7985ee-c8a15d).

Every step is a job step with a checkpoint, so a retry continues where it stopped; a step that
creates something looks for it first (the backup, by name). A dry run reads everything, verifies
the downloads and writes nothing: it records what it would do. Writes still go through the
Pterodactyl client's gate (PTERODACTYL_WRITES).
"""
import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse
from src.database.jobs import JobDatabase
from src.database.modpack import ModpackDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.exceptions import *
from src.library.api.client.download import Downloader
from src.library.api.client.pterodactyl import PterodactylAPI
from src.library.mrpack import ALLOWED_DOMAINS
from src.library.scan import compare
from src.model.jobs import Job
from src.model.pterodactyl import PterodactylServer
from src.library.auth.roles import operator_only
from src.model.audit import AuditTarget
from src.model.users import Actor
from src.services.audit import AuditSink
from src.services.jobs import Handler, JobContext, JobFailed, NotificationSink
from src.services.pterodactyl import PterodactylService

import hashlib

@dataclass
class UpdateTiming:
    """Waits of the procedure, in seconds. The panel caches a server's state for up to 20 s, so
    a state is confirmed by polling, never by the answer to a power signal."""
    save_wait: float = 10
    poll: float = 10
    stop_timeout: float = 600
    kill_timeout: float = 120
    start_timeout: float = 600
    verify_seconds: float = 300
    backup_timeout: float = 1800
    restore_timeout: float = 1800
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)
    clock: Callable[[], float] = field(default=time.monotonic)

class CheckFailed(Exception):
    """The server did not start, or did not stay up, with the new jars."""

class Ops:
    """The writes of one run: sent, or, in a dry run, only recorded."""
    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.log: list[str] = []

    async def write(self, description: str, action: Callable[[], Awaitable[Any]]) -> Any:
        self.log.append(description)
        if self.dry_run:
            return None
        return await action()

class UpdateService():
    def __init__(self,
            pterodactylAPI: PterodactylAPI,
            pterodactylService: PterodactylService,
            pterodactylDB: PterodactylDatabase,
            modpackDB: ModpackDatabase,
            downloader: Downloader,
            jobDB: JobDatabase,
            audit: AuditSink,
            notifier: Optional[NotificationSink] = None,
            timing: Optional[UpdateTiming] = None,
            ) -> None:
        self.api = pterodactylAPI
        self.servers = pterodactylService
        self.serverDB = pterodactylDB
        self.modpackDB = modpackDB
        self.downloader = downloader
        self.jobDB = jobDB
        self.audit = audit
        self.notifier = notifier
        self.timing = timing or UpdateTiming()

    # Queuing. Operators only, a dry run too (it reads the panel on behalf of a write); the
    # request is recorded after it is validated and before the job exists: no job without its
    # event, and a failed record queues nothing.

    @operator_only
    async def enqueue_update(self, server_id: str, actor: Actor, build: Optional[int] = None, dry_run: bool = True) -> Job:
        """Queue an update of a server to a build of its bundle (by default the recommended one, else
        the latest). A dry run unless dry_run is False. 409 if the server has no bundle or build,
        or already has an active job."""
        server = await self.serverDB.server_get(server_id)
        if server.bundle_id is None:
            raise HTTP_409_CONFLICT(f"Server '{server_id}' is not linked to a bundle")
        bundle = await self.modpackDB.bundle_get(server.bundle_id)
        number = build or bundle.recommended_build or bundle.latest_build
        if number is None:
            raise HTTP_409_CONFLICT(f"Bundle '{bundle.id}' has no build")
        await self.modpackDB.build_get(str(bundle.id), number)
        await self._refuse_if_busy(server_id)
        await self.audit.record(actor, "server.update", AuditTarget(kind="server", id=server_id),
                                {"build": number, "dry_run": dry_run})
        return await self.jobDB.enqueue("server_update", key=f"server:{server_id}",
                                        payload={"server_id": server_id, "build": number, "dry_run": dry_run},
                                        requested_by=actor.ref())

    @operator_only
    async def enqueue_rollback(self, server_id: str, update_id: str, mode: str, actor: Actor) -> Job:
        """Queue rollback A (bring the old jars back) or B (restore the pre-update backup) of a
        failed update of this server (d-7985ee-5445a0)."""
        if mode not in ("A", "B"):
            raise HTTPException(status_code=422, detail="mode must be A or B")
        update = await self.jobDB.get(update_id)
        if update.kind != "server_update" or update.payload.get("server_id") != server_id or update.status != "failed":
            raise HTTP_409_CONFLICT(f"Job '{update_id}' is not a failed update of server '{server_id}'")
        needed = "swap" if mode == "A" else "backup"
        if needed not in update.checkpoint:
            raise HTTP_409_CONFLICT(f"The update never reached its {needed} step: nothing to roll back with {mode}")
        await self._refuse_if_busy(server_id)
        await self.audit.record(actor, "server.rollback", AuditTarget(kind="server", id=server_id),
                                {"update_id": update_id, "mode": mode})
        return await self.jobDB.enqueue("server_rollback", key=f"server:{server_id}",
                                        payload={"server_id": server_id, "update_id": update_id, "mode": mode},
                                        requested_by=actor.ref())

    async def _refuse_if_busy(self, server_id: str) -> None:
        """Same 409 as JobDatabase.enqueue, raised before the event is recorded: a refused request
        leaves no event. Only a race between two requests can still reach the unique index."""
        key = f"server:{server_id}"
        if await self.jobDB.active_for(key) is not None:
            raise HTTP_409_CONFLICT(f"There is already an active job for {key}")

    def handlers(self) -> dict[str, Handler]:
        return {"server_update": self._update, "server_rollback": self._rollback}

    # The update.

    async def _update(self, job: Job, context: JobContext) -> None:
        payload = job.payload
        dry_run = bool(payload.get("dry_run", True))
        ops = Ops(dry_run)
        server = await self.serverDB.server_get(payload["server_id"])
        plan = await context.step("preflight", lambda: self._preflight(job, server, dry_run))
        if not plan["install"] and not plan["remove"]:
            return
        ident = server.identifier
        try:
            await context.step("staging", lambda: self._staging(job, ident, plan, ops))
        except JobFailed as failed:
            await self._notify(f"⛔ {server.name} no se actualizó: {failed}", dry_run)
            raise
        await context.step("save", lambda: self._save(ident, ops))
        await context.step("stop", lambda: self._stop(ident, ops))
        backup = await context.step("backup", lambda: self._backup(job, server, ops))
        await context.step("swap", lambda: self._swap(job, ident, plan, ops))
        try:
            await context.step("start", lambda: self._start(ident, ops))
            await context.step("verify", lambda: self._verify(ident, ops))
        except CheckFailed as failed:
            await self._stop(ident, ops)
            message = (f"⚠️ {server.name}: el build {plan['build']} no quedó estable ({failed}). El servidor quedó "
                       f"parado; elige rollback A (devolver los jars) o B (restaurar el backup).")
            await self._notify(message, dry_run)
            raise JobFailed(f"{failed}: server stopped, waiting for rollback A or B") from failed
        await context.step("finish", lambda: self._finish(ident, backup, server, plan, ops))
        if dry_run:
            await context.step("log", lambda: _value({"lines": ops.log}))

    async def _preflight(self, job: Job, server: PterodactylServer, dry_run: bool) -> dict[str, Any]:
        """Only reads: a fresh scan, the build, the server's state and backup slots. Any reason
        not to update fails the job here, before anything is written."""
        def refuse(reason: str) -> JobFailed:
            return JobFailed(reason)
        try:
            if server.bundle_id is None:
                raise refuse("the server is not linked to a bundle")
            bundle = await self.modpackDB.bundle_get(server.bundle_id)
            build = await self.modpackDB.build_get(str(bundle.id), job.payload["build"])
            scan = await self.servers.scan(str(server.id), actor=None)
            comparison = compare(scan.files, build.lock.entries, build.local_files, bundle.claimed_files)
            in_bundle = {p.project_id for p in bundle.projects}
            foreign = [f.path for f in comparison.extra if f.project_id not in in_bundle]
            unexplained = [f.path for f in comparison.unknown] + foreign
            if unexplained:
                raise refuse(f"jars the build does not explain, importar el servidor primero: {', '.join(unexplained)}")
            entries = {e.project_id: e for e in build.lock.entries}
            install = [entries[e.project_id] for _, e in comparison.different_version] + \
                      [entries[e.project_id] for e in comparison.missing]
            remove = [f.path for f, _ in comparison.different_version] + [f.path for f in comparison.extra]
            for entry in install:
                if entry.file is None or urlparse(entry.file.url).hostname not in ALLOWED_DOMAINS:
                    raise refuse(f"{entry.project_id} has no file from an allowed domain")
            staying = {f.path.rsplit("/", 1)[-1] for f in scan.files if f.path not in remove}
            clashes = [e.file.filename for e in install if e.file and e.file.filename in staying]
            if clashes:
                raise refuse(f"new jars would overwrite files that stay: {', '.join(clashes)}")
            plan = {"build": build.number, "bundle": bundle.name, "remove": remove,
                    "install": [{"project_id": e.project_id, "filename": e.file.filename, "url": e.file.url,
                                 "sha512": e.file.sha512, "size": e.file.size} for e in install if e.file]}
            if not install and not remove:
                return plan
            state = await self.api.server_state(server.identifier)
            if state.status is not None or state.is_suspended or state.is_installing or state.is_transferring:
                raise refuse(f"the server is busy (status {state.status})")
            _, count = await self.api.backups(server.identifier)
            if count >= state.backup_limit:
                raise refuse(f"no backup slot left ({count} of {state.backup_limit})")
            return plan
        except JobFailed as failed:
            await self._notify(f"⛔ {server.name} no se actualizó: {failed}", dry_run)
            raise

    async def _staging(self, job: Job, ident: str, plan: dict[str, Any], ops: Ops) -> dict[str, Any]:
        """Download each new jar, check it against the build, and upload it to a staging folder,
        while the server still runs. A jar that does not match stops everything before the stop."""
        staging = f"/mods.staging/{job.id}"
        present = {} if ops.dry_run else {f.name: f.size for f in await self.api.server_files(ident, staging)}
        folder_made = bool(present)
        for item in plan["install"]:
            if present.get(item["filename"]) == item["size"]:
                continue
            data = await self.downloader.download_file(item["url"])
            if len(data) != item["size"] or hashlib.sha512(data).hexdigest() != item["sha512"]:
                raise JobFailed(f"{item['filename']} downloaded from Modrinth does not match the build: no se actualizó")
            if not folder_made:
                # Only now, after the first jar checked out: a bad download writes nothing.
                await ops.write("mkdir /mods.staging", lambda: self.api.files_create_folder(ident, "/", "mods.staging"))
                await ops.write(f"mkdir {staging}", lambda: self.api.files_create_folder(ident, "/mods.staging", str(job.id)))
                folder_made = True
            filename = item["filename"]

            async def upload(filename: str = filename, data: bytes = data) -> bool:
                return await self.api.server_files_upload(ident, staging, filename, data)
            await ops.write(f"upload {staging}/{filename}", upload)
        if not ops.dry_run:
            uploaded = {f.name: f.size for f in await self.api.server_files(ident, staging)}
            wrong = [i["filename"] for i in plan["install"] if uploaded.get(i["filename"]) != i["size"]]
            if wrong:
                raise JobFailed(f"staging does not hold what was uploaded: {', '.join(wrong)}")
        return {"staging": staging, "files": [i["filename"] for i in plan["install"]]}

    async def _save(self, ident: str, ops: Ops) -> dict[str, Any]:
        await ops.write("command save-all", lambda: self.api.server_command(ident, "save-all"))
        if not ops.dry_run:
            await self.timing.sleep(self.timing.save_wait)
        return {}

    async def _wait(self, check: Callable[[], Awaitable[bool]], timeout: float) -> bool:
        start = self.timing.clock()
        while True:
            if await check():
                return True
            if self.timing.clock() - start >= timeout:
                return False
            await self.timing.sleep(self.timing.poll)

    async def _state_is(self, ident: str, state: str) -> bool:
        return (await self.api.resources(ident)).current_state == state

    async def _stop(self, ident: str, ops: Ops) -> dict[str, Any]:
        """Stop, confirmed by polling until offline; kill if it does not stop in time."""
        if await self._state_is(ident, "offline"):
            return {"killed": False}
        await ops.write("power stop", lambda: self.api.server_power(ident, "stop"))
        if ops.dry_run:
            return {"killed": False}
        if await self._wait(lambda: self._state_is(ident, "offline"), self.timing.stop_timeout):
            return {"killed": False}
        await ops.write("power kill", lambda: self.api.server_power(ident, "kill"))
        if await self._wait(lambda: self._state_is(ident, "offline"), self.timing.kill_timeout):
            return {"killed": True}
        raise JobFailed("the server did not stop, not even killed: a person has to look at it")

    async def _backup(self, job: Job, server: PterodactylServer, ops: Ops) -> dict[str, Any]:
        """A locked backup, found by its name first so a retry never makes a second one."""
        name = f"pre-update {job.id}"
        ident = server.identifier
        backups, _ = await self.api.backups(ident)
        existing = next((b for b in backups if b.name == name), None)
        if ops.dry_run:
            ops.log.append(f"backup {name}")
            return {"uuid": None, "name": name}
        backup = existing or await ops.write(f"backup {name}", lambda: self.api.backup_create(ident, name, locked=True))

        async def completed() -> bool:
            return (await self.api.backup_get(ident, backup.uuid)).completed_at is not None
        if not await self._wait(completed, self.timing.backup_timeout):
            await self._restart_after_abort(server, ops, "the backup did not finish in time")
        if not (await self.api.backup_get(ident, backup.uuid)).is_successful:
            await self._restart_after_abort(server, ops, "the backup failed")
        return {"uuid": backup.uuid, "name": name}

    async def _restart_after_abort(self, server: PterodactylServer, ops: Ops, reason: str) -> None:
        """Before the swap nothing changed in mods/: start the server again and fail the job."""
        await ops.write("power start", lambda: self.api.server_power(server.identifier, "start"))
        await self._notify(f"⛔ {server.name} no se actualizó: {reason}; se volvió a arrancar sin cambios.", ops.dry_run)
        raise JobFailed(reason)

    async def _swap(self, job: Job, ident: str, plan: dict[str, Any], ops: Ops) -> dict[str, Any]:
        """Old jars out to mods.old/<job>, then the new ones in from staging. Out first: a crash
        between the two leaves mods/ without either, and a retry finishes it from staging."""
        old = f"mods.old/{job.id}"
        await ops.write("mkdir /mods.old", lambda: self.api.files_create_folder(ident, "/", "mods.old"))
        await ops.write(f"mkdir /{old}", lambda: self.api.files_create_folder(ident, "/mods.old", str(job.id)))
        in_mods = set() if ops.dry_run else {f.name for f in await self.api.server_files(ident, "/mods")}
        staged = set() if ops.dry_run else {f.name for f in await self.api.server_files(ident, f"/mods.staging/{job.id}")}
        names_out = [p.rsplit("/", 1)[-1] for p in plan["remove"]]
        moves_out = [(f"mods/{n}", f"{old}/{n}") for n in names_out if ops.dry_run or n in in_mods]
        moves_in = [(f"mods.staging/{job.id}/{i['filename']}", f"mods/{i['filename']}") for i in plan["install"]
                    if ops.dry_run or i["filename"] in staged]
        if moves_out:
            await ops.write(f"rename {len(moves_out)} old jars to /{old}", lambda: self.api.files_rename(ident, "/", moves_out))
        if moves_in:
            await ops.write(f"rename {len(moves_in)} new jars into /mods", lambda: self.api.files_rename(ident, "/", moves_in))
        return {"old": old, "moved_out": names_out, "moved_in": [i["filename"] for i in plan["install"]]}

    async def _start(self, ident: str, ops: Ops) -> dict[str, Any]:
        await ops.write("power start", lambda: self.api.server_power(ident, "start"))
        if not ops.dry_run and not await self._wait(lambda: self._state_is(ident, "running"), self.timing.start_timeout):
            raise CheckFailed("it did not reach running")
        return {}

    async def _verify(self, ident: str, ops: Ops) -> dict[str, Any]:
        """Running for verify_seconds without going down, its uptime always growing (a restart
        resets it)."""
        if ops.dry_run:
            ops.log.append(f"verify {self.timing.verify_seconds:.0f} s running")
            return {}
        start, last = self.timing.clock(), -1
        while self.timing.clock() - start < self.timing.verify_seconds:
            await self.timing.sleep(self.timing.poll)
            resources = await self.api.resources(ident)
            if resources.current_state != "running":
                raise CheckFailed(f"it went {resources.current_state}")
            if resources.uptime_ms < last:
                raise CheckFailed("it restarted")
            last = resources.uptime_ms
        return {"uptime_ms": last}

    async def _finish(self, ident: str, backup: dict[str, Any], server: PterodactylServer,
                      plan: dict[str, Any], ops: Ops) -> dict[str, Any]:
        """Release the backup's lock, and announce it. mods.old stays until a person removes it."""
        if backup.get("uuid") and not ops.dry_run and (await self.api.backup_get(ident, backup["uuid"])).is_locked:
            await ops.write("unlock backup", lambda: self.api.backup_lock_toggle(ident, backup["uuid"]))
        await self._notify(f"✅ {server.name} actualizado a {plan['bundle']}, build {plan['build']}.", ops.dry_run)
        return {}

    # Rollbacks, only on request (d-7985ee-5445a0).

    async def _rollback(self, job: Job, context: JobContext) -> None:
        payload = job.payload
        ops = Ops(dry_run=False)
        server = await self.serverDB.server_get(payload["server_id"])
        update = await self.jobDB.get(payload["update_id"])
        ident, mode = server.identifier, payload["mode"]
        await context.step("stop", lambda: self._stop(ident, ops))
        if mode == "A":
            await context.step("files", lambda: self._rollback_files(ident, update, ops))
        else:
            await context.step("restore", lambda: self._restore(ident, update, ops))
        try:
            await context.step("start", lambda: self._start(ident, ops))
            await context.step("verify", lambda: self._verify(ident, ops))
        except CheckFailed as failed:
            await self._stop(ident, ops)
            await self._notify(f"⚠️ {server.name}: el rollback {mode} no quedó estable ({failed}); el servidor quedó parado.", False)
            raise JobFailed(f"rollback {mode}: {failed}") from failed
        await self._notify(f"↩️ {server.name}: rollback {mode} hecho; el servidor corre.", False)

    async def _rollback_files(self, ident: str, update: Job, ops: Ops) -> dict[str, Any]:
        """Rollback A: the new jars out to mods.old/<job>/new, the old ones back into mods/."""
        swap = update.checkpoint["swap"]
        old = swap["old"]
        await ops.write(f"mkdir /{old}/new", lambda: self.api.files_create_folder(ident, f"/{old}", "new"))
        in_mods = {f.name for f in await self.api.server_files(ident, "/mods")}
        kept = {f.name for f in await self.api.server_files(ident, f"/{old}")}
        out = [(f"mods/{n}", f"{old}/new/{n}") for n in swap["moved_in"] if n in in_mods]
        back = [(f"{old}/{n}", f"mods/{n}") for n in swap["moved_out"] if n in kept]
        if out:
            await ops.write("rename new jars out", lambda: self.api.files_rename(ident, "/", out))
        if back:
            await ops.write("rename old jars back", lambda: self.api.files_rename(ident, "/", back))
        return {"out": [a for a, _ in out], "back": [b for _, b in back]}

    async def _restore(self, ident: str, update: Job, ops: Ops) -> dict[str, Any]:
        """Rollback B: restore the pre-update backup, truncating, and wait until the panel is idle.
        Not atomic, and loses what was played since the backup: a person chose it."""
        uuid = update.checkpoint["backup"]["uuid"]
        await ops.write("restore backup", lambda: self.api.backup_restore(ident, uuid, truncate=True))

        async def idle() -> bool:
            return (await self.api.server_state(ident)).status is None
        if not await self._wait(idle, self.timing.restore_timeout):
            raise JobFailed("the restore did not finish in time: a person has to look at it")
        return {"restored": uuid}

    async def _notify(self, text: str, dry_run: bool) -> None:
        if self.notifier is not None and not dry_run:
            await self.notifier.notify(text)

async def _value(value: dict[str, Any]) -> dict[str, Any]:
    return value
