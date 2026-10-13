import asyncio
from collections.abc import Awaitable, Callable
import hashlib
import math
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional
from src.database import ModpackDatabase, ModrinthDatabase, PterodactylDatabase
from src.database.jobs import JobDatabase
from src.model.jobs import Job
from src.library.api.client.modrinth import ModrinthAPI
from src.library.api.client.pterodactyl import PterodactylAPI
from fastapi import HTTPException
from src.library.deduce import Deduction, deduce_target
from src.library.jarmeta import ModMetadata, read_mod_metadata
from src.library.resolution import CHANNEL_RANK, EXCEPTION_CHANNELS
from src.library.utils import MODRINTH_ID
from src.library.scan import ClaimedFile, Comparison, ListedFile, LocalFile, ScanFile, compare, drift_message, plan_scan
from src.model.modpack import ModpackBundle, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.model.pterodactyl import BundleForServer, PanelServers, ImportCandidate, ImportResult, ProjectCandidate, PterodactylFile, PterodactylServer, ServerScan
from src.exceptions import *
from src.library.auth.roles import operator_only
from src.model.audit import AuditTarget
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from src.services.audit import AuditSink
if TYPE_CHECKING:  # src.services.jobs imports this module: only names for annotations here
    from src.services.jobs import Handler, JobContext

logger = logging.getLogger("modpack.servers")

# A jar from a server is untrusted: it is hashed, never loaded, and not downloaded past this
# size (untrusted-package-is-parsed-never-loaded). The largest common mods are a few tens of MiB.
SCAN_MAX_BYTES = 128 * 1024 * 1024
# Downloads at once. The Client API allows 256 requests a minute, and each jar takes two (the
# signed URL and the download): a large first scan is paced by this and by the panel.
SCAN_CONCURRENCY = 4
SCAN_DIRECTORY = "/mods"
READING_JARS = "Leyendo los jars del servidor"
READING_METADATA = "Leyendo metadatos de jars"
SEARCHING = "Buscando en Modrinth"
ADDING = "Agregando al modpack"
# What a long task hears as it goes: what it processes, done and total (a job's progress).
Report = Callable[[str, int, int], Awaitable[None]]
# The panel's server list (U6; owner, 2026-10-09): asked at most this often, and «Actualizar lista»
# at most once a minute (one resource for everyone, not per user).
# What brings files back with old modification times (a partial match of the panel's event names;
# the panel writes the failed restore as `server.backup.restore-failed`, hence `backup.restore`).
RESTORE_EVENTS = ["backup.restore", "file.decompress", "reinstall"]
# The scan's own start and the event's stamp come from two clocks; the panel purges activity after
# 90 days (research 2026-10-12-actividad-pterodactyl).
ACTIVITY_SLACK = timedelta(minutes=2)
ACTIVITY_KEPT = timedelta(days=90)
PANEL_LIST_TTL = timedelta(minutes=5)
PANEL_LIST_REFRESH_EVERY = timedelta(minutes=1)
IMPORT_FAILED = ("El modpack quedó creado y vinculado a este servidor, pero la importación de sus mods falló. "
                 "Vuelve a intentarlo con «Importar».")
# A pack created for a server that another operator linked meanwhile (d-7985ee-b8e927).
RACE_LOST = "No se pudo vincular: otro operador vinculó un modpack a este servidor mientras se creaba este."
RACE_UNDONE = "El modpack «{name}» que se acababa de crear se borró. Vuelve a cargar la página."
RACE_KEPT = "El modpack «{name}» que se acababa de crear no se pudo borrar: bórralo a mano. Vuelve a cargar la página."

def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _servers(kept: list[dict[str, Any]]) -> list[PterodactylServer]:
    return [PterodactylServer.model_validate(item) for item in kept]


class PterodactylService():
    def __init__(self,
            pterodactylAPI: PterodactylAPI,
            PterodactylDB: PterodactylDatabase,
            modrinthAPI: ModrinthAPI,
            modrinthDB: ModrinthDatabase,
            modpackDB: ModpackDatabase,
            audit: AuditSink,
            jobDB: Optional[JobDatabase] = None,
            clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
            ) -> None:
        self.clock = clock
        self.jobDB = jobDB or JobDatabase()
        self.pterodactylAPI = pterodactylAPI
        self.pterodactylDB = PterodactylDB
        self.modrinthAPI = modrinthAPI
        self.modrinthDB = modrinthDB
        self.modpackDB = modpackDB
        self.audit = audit

    async def server_external(self) -> list[PterodactylServer]:
        """Every server the panel key can see, straight from the panel."""
        return list(await self.pterodactylAPI.server_get_many())

    async def panel_servers(self, force: bool = False) -> PanelServers:
        """The panel's servers (U6): the kept list while younger than PANEL_LIST_TTL, unless `force`;
        else asked now and kept. If the panel does not answer, the kept list, however old, marked."""
        kept = await self.pterodactylDB.panel_list_get()
        kept_at = _utc(kept.fetched_at) if kept is not None else None
        if kept is not None and kept_at is not None and not force and self.clock() - kept_at < PANEL_LIST_TTL:
            return PanelServers(servers=_servers(kept.servers), fetched_at=kept_at)
        try:
            servers = await self.server_external()
        except Exception:
            logger.warning("The panel's server list failed", exc_info=True)
            return PanelServers(servers=_servers(kept.servers) if kept else [], fetched_at=kept_at, failed=True)
        now = self.clock()
        await self.pterodactylDB.panel_list_save([s.model_dump(mode="json", exclude={"id", "bundle_id"}) for s in servers], now)
        return PanelServers(servers=servers, fetched_at=now)

    async def panel_refresh(self, actor: Actor) -> PanelServers:
        """«Actualizar lista»: ask the panel now, once a minute; sooner, QuotaExceeded that says when."""
        kept = await self.pterodactylDB.panel_list_get()
        if kept is not None:
            left = PANEL_LIST_REFRESH_EVERY - (self.clock() - _utc(kept.fetched_at))
            if left > timedelta(0):
                raise QuotaExceeded(f"Se puede volver a actualizar en {math.ceil(left.total_seconds())} s",
                                    left.total_seconds())
        return await self.panel_servers(force=True)

    async def server_find(self) -> list[PterodactylServer]:
        return await self.pterodactylDB.server_find().to_list()

    async def server_get(self, server_id: str) -> PterodactylServer:
        return await self.pterodactylDB.server_get(server_id)

    async def server_create(self, uuid: str, actor: Actor) -> PterodactylServer:
        """
        Create a new Pterodactyl server.
        
        :param uuid: The UUID of the server to create.
        :return: The created PterodactylServer object.
        """
        server = await self.pterodactylAPI.server_get(uuid)
        created = await self.pterodactylDB.server_insert(server)
        await self.audit.record(actor, "server.create", _target(str(created.id)), {"uuid": uuid})
        return created
    
    async def server_update(self, server_id: str, actor: Actor) -> PterodactylServer:
        """
        Update a Pterodactyl server.
        
        :param server_id: The ID of the server to update.
        :return: The updated PterodactylServer object.
        """
        document = await self.pterodactylDB.server_get(server_id)
        server = await self.pterodactylAPI.server_get(document.uuid)
        # The panel does not know our fields: keep them.
        server.bundle_id = document.bundle_id
        saved = await self.pterodactylDB.server_save(server, document.id)
        await self.audit.record(actor, "server.refresh", _target(server_id))
        return saved

    @operator_only
    async def server_link_bundle(self, server_id: str, bundle_id: Optional[str], actor: Actor) -> PterodactylServer:
        """Link a server to the bundle it runs (404 if the bundle does not exist), or unlink it.
        Operators only: the bundle decides what an update writes to the server."""
        if bundle_id is not None:
            await self.modpackDB.bundle_get(bundle_id)
        server = await self.pterodactylDB.server_set_bundle(server_id, bundle_id)
        await self.audit.record(actor, "server.bundle", _target(server_id), {"bundle_id": bundle_id})
        return server
    
    async def server_files(self, server_id: str, directory: str) -> list[PterodactylFile]:
        """
        Get the files of a Pterodactyl server.
        
        :param server_id: The ID of the server to get files from.
        :return: A list of PterodactylFile objects.
        """
        server = await self.pterodactylDB.server_get(server_id)
        return list(await self.pterodactylAPI.server_files(server.identifier, directory))

    # What a server has (i-7985ee-43df2f, option 1). Only reads: listing and downloading.

    async def scan(self, server_id: str, actor: Optional[Actor], directory: str = SCAN_DIRECTORY,
                   full: bool = False, record_drift: bool = True,
                   seeds: Optional[dict[str, tuple[int, str, str]]] = None,
                   report: Optional[Report] = None) -> ServerScan:
        """
        Scan a directory of a server: which jars it has, and which Modrinth version each one is.

        Lists the directory; downloads and hashes only the jars whose path, size or modification
        time changed since the last scan, or all of them if `full`; identifies them by sha1
        against Modrinth. Writes nothing to the server.

        :param actor: Who asked; None for the scheduler's and the worker's scans, which are not recorded.
        :param record_drift: Store how the scan compares with the server's build (F37). False for a
            real update's preflight: the update changes the server right after, so it would be stale.
        :param seeds: Path → (size, sha1, sha512) of jars the app itself just uploaded and verified
            (U3): one listed with that size takes those hashes instead of being downloaded; it stays
            unconfirmed, so the next scan downloads it once (owner, 2026-10-12).
        :param report: Hears how far it is: «Leyendo los jars del servidor», done and total (T1).
        :return: The stored scan.
        """
        server = await self.pterodactylDB.server_get(server_id)
        # The scan is dated when it starts: the next one asks the activity since then, so a restore
        # during a long scan is not missed (code review, 2026-10-12).
        started = self.clock()
        previous = await self.pterodactylDB.scan_latest(server_id, directory)
        if previous is not None and not full:
            full = await self._restored_since(server.identifier, previous)
        listing = [ListedFile(path=f"{directory.rstrip('/')}/{f.name}", size=f.size, modified_at=f.modified_at,
                              created_at=f.created_at)
                   for f in await self.pterodactylAPI.server_files(server.identifier, directory)
                   if f.is_file and not f.is_symlink and f.name.endswith(".jar")]
        kept, to_hash, too_large = plan_scan(listing, previous.files if previous else [], full, SCAN_MAX_BYTES)
        seeded = [ScanFile(**f.model_dump(), status="unknown", sha1=seeds[f.path][1], sha512=seeds[f.path][2],
                           confirmed=False)
                  for f in to_hash if seeds and f.path in seeds and seeds[f.path][0] == f.size]
        seeded_paths = {f.path for f in seeded}
        hashed = [*seeded, *await self._hash(server.identifier, [f for f in to_hash if f.path not in seeded_paths],
                                             report)]
        # Modrinth may know today a jar it did not know before: unknown ones are asked about again.
        known = [f for f in kept if f.status == "identified"]
        identified = [f if f.status == "unknown" else f.model_copy(update={"metadata": None, "metadata_read": False})
                      for f in await self._identify([*hashed, *(f for f in kept if f.status == "unknown")])]
        files = sorted([*known, *identified, *too_large], key=lambda f: f.path)
        stored = await self.pterodactylDB.scan_insert(ServerScan(
            server_id=server.id, directory=directory, scanned_at=started,  # type: ignore[arg-type]
            full=full, files=files))
        if record_drift and directory == SCAN_DIRECTORY:
            stored = await self._store_drift(server_id, stored)
        if actor is not None:
            await self.audit.record(actor, "server.scan", _target(server_id), {"directory": directory, "full": full})
        return stored

    async def record_drift(self, server_id: str) -> None:
        """Record on the latest scan how it compares with the build, with no request to the panel: for
        a real update that failed before writing, whose preflight scan recorded none (round 1 review)."""
        latest = await self.pterodactylDB.scan_latest(server_id, SCAN_DIRECTORY)
        if latest is not None:
            await self._store_drift(server_id, latest)

    async def _store_drift(self, server_id: str, scan: ServerScan) -> ServerScan:
        """Record on a new scan how it compares with the server's build (F37), whoever scanned:
        the dashboard reads the latest scan's. Nothing for a server without a bundle with a build.
        Comparing is a nicety here: if it fails, the scan stays, without it. The scan job's drift
        step compares again and decides what to announce against the scan before this one."""
        try:
            drift = await self.drift(server_id)
        except Exception:
            # Any failure, not only an HTTP one (code review of round 1): the scan stays, without drift.
            logger.warning("comparing a new scan with its build failed", exc_info=True, extra={"server_id": server_id})
            return scan
        if drift is None:
            return scan
        await self.pterodactylDB.scan_set_drift(server_id, drift)
        return scan.model_copy(update={"drift": drift})

    async def _restored_since(self, identifier: str, previous: ServerScan) -> bool:
        """Whether files may have come back with old modification times since `previous`: a backup
        restore, a decompress or a reinstall in the server's activity (one request), or a scan too
        old for the activity to still hold it. If the activity cannot be read, yes: one full scan
        costs less than a stale cache (fail closed). A 409 (restoring, installing, moving) is raised."""
        since = _utc(previous.scanned_at)
        if self.clock() - since > ACTIVITY_KEPT:
            return True
        try:
            events = await self.pterodactylAPI.server_activity(identifier, RESTORE_EVENTS)
        except HTTPException as error:
            # 409: restoring, installing or moving; 429: a full scan would make the limit worse. Both
            # are raised, and the job tries again with its backoff (code review, 2026-10-12).
            if error.status_code in (409, 429):
                raise
            logger.warning("activity of %s unreadable: the scan is full", identifier, exc_info=True)
            return True
        except Exception:
            logger.warning("activity of %s unreadable: the scan is full", identifier, exc_info=True)
            return True
        return any(_utc(stamp) >= since - ACTIVITY_SLACK for _, stamp in events)

    async def _hash(self, identifier: str, files: list[ListedFile], report: Optional[Report] = None) -> list[ScanFile]:
        """Download each file and hash its bytes, and read its mod metadata as data (S5): the bytes are
        hashed and parsed, never loaded. An identified jar drops the metadata afterwards."""
        limit = asyncio.Semaphore(SCAN_CONCURRENCY)
        done = 0
        if report is not None and files:
            await report(READING_JARS, 0, len(files))

        async def one(file: ListedFile) -> ScanFile:
            nonlocal done
            async with limit:
                data = await self.pterodactylAPI.server_files_download(identifier, file.path)
            done += 1
            if report is not None:
                await report(READING_JARS, done, len(files))
            if len(data) > SCAN_MAX_BYTES:
                return ScanFile(**file.model_dump(), status="too_large")
            return ScanFile(**file.model_dump(), status="unknown",
                            sha1=hashlib.sha1(data).hexdigest(), sha512=hashlib.sha512(data).hexdigest(),
                            metadata=read_mod_metadata(data), metadata_read=True)

        return list(await asyncio.gather(*[one(f) for f in files]))

    async def _identify(self, files: list[ScanFile]) -> list[ScanFile]:
        """Ask Modrinth which version each sha1 belongs to, and store those versions in the catalog.
        A version counts only if one of its files has exactly that sha1."""
        hashes = sorted({f.sha1 for f in files if f.sha1})
        found: dict[str, ModrinthVersion] = await self.modrinthAPI.files_hash_post_many(hashes, "sha1") if hashes else {}
        result = []
        for file in files:
            version = found.get(file.sha1 or "")
            if version and any(f.hashes.sha1 == file.sha1 for f in version.files):
                file = file.model_copy(update={"status": "identified", "project_id": version.project_id,
                                               "version_id": version.version_id})
            result.append(file)
        if found:
            await self.modrinthDB.version_upsert_many(found.values())
        return result

    async def scan_latest(self, server_id: str, directory: str = SCAN_DIRECTORY) -> ServerScan:
        scan = await self.pterodactylDB.scan_latest(server_id, directory)
        if scan is None:
            raise HTTP_404_NOT_FOUND(f"Server '{server_id}' has no scan of {directory}")
        return scan

    async def scan_find(self, server_id: str) -> list[ServerScan]:
        return await self.pterodactylDB.scan_find(server_id)

    async def drift(self, server_id: str) -> Optional[str]:
        """How the latest scan compares with the server's build, as a message; None if the server
        is not linked to a bundle with a build."""
        server = await self.pterodactylDB.server_get(server_id)
        if server.bundle_id is None:
            return None
        bundle = await self.modpackDB.bundle_get(server.bundle_id)
        number = bundle.recommended_build or bundle.latest_build
        if number is None:
            return None
        comparison = await self.compare(server_id, server.bundle_id, number)
        return drift_message(server.name, bundle.name, number, comparison)

    async def compare(self, server_id: str, bundle_id: Optional[str] = None, build: Optional[int] = None,
                      directory: str = SCAN_DIRECTORY) -> Comparison:
        """
        Compare a server's latest scan with a build of a bundle (i-7985ee-4364a3): what matches,
        what has another version, what is missing, extra or unknown.

        :param bundle_id: By default, the bundle the server is linked to; 409 if it has none.
        :param build: The build number; by default the recommended one, else the latest.
        """
        if bundle_id is None:
            bundle_id = (await self.pterodactylDB.server_get(server_id)).bundle_id
            if bundle_id is None:
                raise HTTP_409_CONFLICT(f"Server '{server_id}' is not linked to a bundle: name one, or link it")
        bundle = await self.modpackDB.bundle_get(bundle_id)
        number = build if build is not None else (bundle.recommended_build or bundle.latest_build)
        if number is None:
            raise HTTP_409_CONFLICT(f"Bundle '{bundle_id}' has no build to compare with")
        frozen = await self.modpackDB.build_get(bundle_id, number)
        scan = await self.scan_latest(server_id, directory)
        return compare(scan.files, frozen.lock.entries, frozen.local_files, bundle.claimed_files)

    async def compare_names(self, comparison: Comparison) -> dict[str, str]:
        """
        What a person reads for each version id of a comparison (F20, F30): the catalog's version
        number; else the build's, from its lock; else the name of the server's jar. Project titles
        are not here: they come from ModrinthService.projects_filled. Only reads the catalog.
        """
        labels: dict[str, str] = {}
        for file in [*comparison.matching, *(f for f, _ in comparison.different_version)]:
            if file.version_id:
                labels[file.version_id] = file.path.rsplit("/", 1)[-1]
        for entry in [*(e for _, e in comparison.different_version), *comparison.missing]:
            labels[entry.version_id] = entry.version_number or labels.get(entry.version_id, entry.version_id)
        for version in await self.modrinthDB.version_find_by_version_ids(sorted(labels)):
            labels[version.version_id] = version.version_number
        return labels

    # Importing a server (i-7985ee-17745b, d-7985ee-c8a15d): its jars that the bundle does not
    # explain are brought into the bundle. Only reads the server.

    async def _import_context(self, server_id: str) -> tuple[PterodactylServer, ModpackBundle, ServerScan]:
        server = await self.pterodactylDB.server_get(server_id)
        if server.bundle_id is None:
            raise HTTP_409_CONFLICT(f"Server '{server_id}' is not linked to a bundle: link it before importing")
        bundle = await self.modpackDB.bundle_get(server.bundle_id)
        return server, bundle, await self.scan_latest(server_id)

    @operator_only
    async def import_request(self, server_id: str, actor: Actor) -> Job:
        """
        "Importar" (T1): the server's import job, which waits its turn and shows how far it is; one
        per server: asking again gives the running import. 409 now if the server is not linked to a
        bundle, or if another job of the server (a scan, an update) holds its key.
        """
        server = await self.pterodactylDB.server_get(server_id)
        if server.bundle_id is None:
            raise HTTP_409_CONFLICT(f"Server '{server_id}' is not linked to a bundle: link it before importing")
        key = f"server:{server_id}"
        try:
            job = await self.jobDB.enqueue("server_import", key=key, payload={"server_id": server_id},
                                           requested_by=actor.ref())
        except HTTP_409_CONFLICT:
            # The key is the server's: a scan, an update or a rollback holds it too. Only a running
            # import is answered as this one; anything else is the 409 as the other actions show it.
            active = await self.jobDB.active_for(key)
            if active is None or active.kind != "server_import":
                raise
            return active
        await self.audit.record(actor, "server.import.request", _target(server_id), {"job_id": str(job.id)})
        return job

    def handlers(self) -> "dict[str, Handler]":
        return {"server_import": self._import_run}

    async def _import_run(self, job: Job, context: "JobContext") -> None:
        from src.services.jobs import JobFailed  # src.services.jobs imports this module

        asked = job.requested_by
        if asked is None:
            raise JobFailed("Una importación sin quién la pidió no se hace: no quedaría en la auditoría")
        # Asked by an operator: import_request is operators only, and so is importing.
        actor = Actor(iss=asked.iss, sub=asked.sub, name=asked.name, groups=frozenset(), is_operator=True)

        async def run() -> dict[str, Any]:
            result = await self.import_server(job.payload["server_id"], actor, report=context.progress)
            return result.model_dump(mode="json")

        await context.step("import", run)

    async def import_pending(self, server_id: str) -> Optional[ImportResult]:
        """What the server's last import job left (S3): its result, with only the candidates the admin
        has not decided yet (a jar confirmed as a project or kept as local leaves the list). None if
        it was never imported by a job."""
        last = await self.jobDB.last_of_kind("server_import", f"server:{server_id}")
        if last is None or "import" not in last.checkpoint:
            return None
        result = ImportResult(**last.checkpoint["import"])
        server = await self.pterodactylDB.server_get(server_id)
        if server.bundle_id is None:
            return result
        bundle = await self.modpackDB.bundle_get(server.bundle_id)
        decided = {f.sha512 for f in bundle.local_files} | {c.sha512 for c in bundle.claimed_files}
        return result.model_copy(update={"candidates": [c for c in result.candidates if c.sha512 not in decided]})

    @operator_only
    async def import_server(self, server_id: str, actor: Actor, report: Optional[Report] = None) -> ImportResult:
        """
        Bring into the server's bundle every jar it has that the bundle does not explain (T1). It
        scans first, incrementally (S1): only what changed is downloaded. Identified by hash, the
        project joins as the admin's. Unknown, its metadata comes from the scan (an older scan's jars
        are read once, S5): every mod id is tried as a slug in one Modrinth request, and a text
        search only runs for the jars whose slug found no mod (S2); the candidates wait for the
        admin. Without metadata or candidates, the jar becomes a local file of the bundle. Running it
        again adds nothing new. `report` hears how far it is.
        """
        server = await self.pterodactylDB.server_get(server_id)
        if server.bundle_id is None:
            raise HTTP_409_CONFLICT(f"Server '{server_id}' is not linked to a bundle: link it before importing")
        scan = await self.scan(server_id, actor=None, report=report)
        bundle = await self.modpackDB.bundle_get(server.bundle_id)
        in_bundle = {p.project_id for p in bundle.projects}
        decided = {f.sha512 for f in bundle.local_files} | {c.sha512 for c in bundle.claimed_files}
        unread = [f for f in scan.files if f.status == "unknown" and f.sha512 and f.sha512 not in decided
                  and not f.metadata_read]
        if unread:
            scan = await self._read_metadata(server.identifier, scan, unread, report)
        result = ImportResult(added=[], candidates=[], local=[], too_large=[])
        unknown: list[ScanFile] = []
        for file in scan.files:
            if file.status == "too_large":
                result.too_large.append(file.path)
            elif file.status == "identified" and file.project_id and file.project_id not in in_bundle:
                in_bundle.add(file.project_id)
                result.added.append(file.project_id)
            elif file.status == "unknown" and file.sha512 and file.sha512 not in decided:
                unknown.append(file)
        with_metadata = [f for f in unknown if f.metadata is not None]
        found = await self._candidates_for(with_metadata, report)
        for file in unknown:
            candidates = found.get(file.path, [])
            if candidates and file.sha512 is not None:
                result.candidates.append(ImportCandidate(path=file.path, sha512=file.sha512,
                                                         metadata=file.metadata, candidates=candidates))
            else:
                await self.modpackDB.bundle_local_file_add(str(bundle.id), _local(file))
                result.local.append(file.path)
        if report is not None:
            await report(ADDING, 0, 1)
        if result.added:
            await self._add_projects(str(bundle.id), result.added)
        on_server, exceptions = await self._running(bundle, scan)
        result.on_server, result.channel_exceptions = on_server, exceptions
        await self.audit.record(actor, "server.import", _target(server_id), {
            "bundle_id": str(bundle.id), "added": result.added, "local": result.local,
            "candidates": [c.path for c in result.candidates], "on_server": on_server,
            "channel_exceptions": exceptions})
        return result

    async def _read_metadata(self, identifier: str, scan: ServerScan, files: list[ScanFile],
                             report: Optional[Report]) -> ServerScan:
        """Download once the unknown jars an older scan did not read (S5), read their metadata as
        data, and keep it in the scan, so the next import downloads nothing."""
        read: dict[str, Optional[ModMetadata]] = {}
        for done, file in enumerate(files):
            if report is not None:
                await report(READING_METADATA, done, len(files))
            data = await self.pterodactylAPI.server_files_download(identifier, file.path)
            read[file.path] = read_mod_metadata(data) if len(data) <= SCAN_MAX_BYTES else None
        if report is not None:
            await report(READING_METADATA, len(files), len(files))
        updated = [f.model_copy(update={"metadata": read[f.path], "metadata_read": True}) if f.path in read else f
                   for f in scan.files]
        await self.pterodactylDB.scan_set_files(scan.id, updated)
        return scan.model_copy(update={"files": updated})

    async def _candidates_for(self, files: list[ScanFile], report: Optional[Report]) -> dict[str, list[ProjectCandidate]]:
        """Modrinth projects each jar's metadata may be (S2): every mod id as a slug, in one request;
        a search by name only for a jar whose slug found no mod. Never accepted here: the metadata
        named the wrong project in 1 of 9 real jars."""
        if not files:
            return {}
        slugs = sorted({f.metadata.mod_id for f in files if f.metadata is not None})
        projects = await self.modrinthDB.project_upsert_many(await self.modrinthAPI.project_get_many(slugs))
        by_ref = {key: p for p in projects for key in (p.slug, p.project_id)}
        found: dict[str, list[ProjectCandidate]] = {}
        for done, file in enumerate(files):
            if report is not None:
                await report(SEARCHING, done, len(files))
            assert file.metadata is not None
            project = by_ref.get(file.metadata.mod_id)
            if project is not None and project.project_type.value == "mod":
                found[file.path] = [ProjectCandidate(project_id=project.project_id, slug=project.slug,
                                                     title=project.title, via="slug")]
                continue
            hits = await self.modrinthAPI.search(file.metadata.name or file.metadata.mod_id, [["project_type:mod"]], limit=3)
            found[file.path] = list({hit["project_id"]: ProjectCandidate(project_id=hit["project_id"], slug=hit["slug"],
                                                                         title=hit["title"], via="search")
                                     for hit in hits}.values())
        if report is not None:
            await report(SEARCHING, len(files), len(files))
        return found

    async def _running(self, bundle: ModpackBundle, scan: ServerScan) -> tuple[list[str], dict[str, str]]:
        """Record what the server runs (owner, 2026-10-09): every identified mod goes on the server,
        and one whose installed version is beta or alpha, outside the pack's channels, accepts that
        channel (d-7985ee-b1ce87). Only where the admin decided nothing: a decision or an exception
        already there stays. Returns the decisions and the exceptions written."""
        identified = [f for f in scan.files if f.status == "identified" and f.project_id and MODRINTH_ID.fullmatch(f.project_id)]
        versions = {v.version_id: v for v in await self.modrinthDB.version_find_by_version_ids(
            sorted({f.version_id for f in identified if f.version_id}))}
        exceptions: dict[str, str] = {}
        for file in identified:
            version = versions.get(file.version_id) if file.version_id else None
            channel = version.version_type.value if version is not None else None
            if channel in EXCEPTION_CHANNELS and channel not in bundle.channels and file.project_id is not None:
                known = exceptions.get(file.project_id)
                if known is None or CHANNEL_RANK[channel] > CHANNEL_RANK[known]:
                    exceptions[file.project_id] = channel
        bundle_id = str(bundle.id)
        decided = await self.modpackDB.bundle_keys_set_missing(
            bundle_id, "server_decisions", {f.project_id: True for f in identified if f.project_id})
        excepted = await self.modpackDB.bundle_keys_set_missing(bundle_id, "channel_exceptions", exceptions)
        return sorted(decided), excepted

    async def _add_projects(self, bundle_id: str, project_ids: list[str]) -> None:
        projects = await self.modrinthDB.project_upsert_many(await self.modrinthAPI.project_get_many(project_ids))
        await self.modpackDB.bundle_projects_add(
            bundle_id, [PackProject(project_id=p.project_id, origin=ProjectOrigin.admin) for p in projects])

    # A new bundle for a server without one (panel usability §2): deduced from the latest scan.

    async def deduction(self, server_id: str) -> Optional[Deduction]:
        """The loader and Minecraft version the identified jars of the latest scan point to, read
        from the catalog; None if the server has no scan. A jar whose version the catalog does not
        have does not vote."""
        scan = await self.pterodactylDB.scan_latest(server_id, SCAN_DIRECTORY)
        if scan is None:
            return None
        version_ids = [f.version_id for f in scan.files if f.status == "identified" and f.version_id]
        versions = {v.version_id: v for v in await self.modrinthDB.version_find_by_version_ids(sorted(set(version_ids)))}
        return deduce_target([(versions[i].loaders, versions[i].game_versions) for i in version_ids if i in versions])

    @operator_only
    async def bundle_for_server(self, server_id: str, name: str, version: str, loader: MinecraftLoaders,
                                import_mods: bool, actor: Actor) -> BundleForServer:
        """
        Create a bundle for a server without one, link it and, if `import_mods`, import the server
        into it, each step with its audit event. Operators only, since linking and importing are.
        409 if the server already has a bundle, before anything is created. The link only lands
        while the server still has none: if another operator linked one meanwhile, the new bundle,
        still empty, is deleted and it is a 409 that says so (d-7985ee-b8e927). If importing fails,
        the bundle stays created and linked (an empty linked bundle, never a touched server: this
        only reads the panel) and the answer says so.
        """
        server = await self.pterodactylDB.server_get(server_id)
        if server.bundle_id is not None:
            raise HTTP_409_CONFLICT("Este servidor ya tiene modpack: vuelve a cargar la página")
        bundle = await self.modpackDB.bundle_insert(ModpackBundle(name=name, description="", version=version, loader=loader))
        bundle_target = AuditTarget(kind="bundle", id=str(bundle.id), name=bundle.name)  # it may be deleted below
        await self.audit.record(actor, "bundle.create", bundle_target)
        if not await self.pterodactylDB.server_link_if_unlinked(server_id, str(bundle.id)):
            try:
                deleted = await self.modpackDB.bundle_delete_empty(str(bundle.id))
            except Exception:
                logger.warning("deleting a pack that lost the race to link a server failed", exc_info=True,
                               extra={"server_id": server_id, "bundle_id": str(bundle.id)})
                deleted = False
            if deleted:
                await self.audit.record(actor, "bundle.delete", bundle_target, {"reason": "server linked meanwhile"})
            raise HTTP_409_CONFLICT(f"{RACE_LOST} {(RACE_UNDONE if deleted else RACE_KEPT).format(name=name)}")
        await self.audit.record(actor, "server.bundle", _target(server_id), {"bundle_id": str(bundle.id)})
        import_job, import_error = None, None
        if import_mods:
            try:
                # T1: the server's import job, which shows its progress (never inside this request).
                import_job = await self.import_request(server_id, actor)
            except Exception:
                logger.warning("queueing the import after creating a bundle for a server failed", exc_info=True,
                               extra={"server_id": server_id, "bundle_id": str(bundle.id)})
                import_error = IMPORT_FAILED
        return BundleForServer(bundle=bundle, imported=None, import_error=import_error, import_job=import_job)

    def _scanned(self, scan: ServerScan, path: str) -> ScanFile:
        file = next((f for f in scan.files if f.path == path), None)
        if file is None or file.sha512 is None:
            raise HTTP_404_NOT_FOUND(f"The latest scan has no hashed file {path}")
        return file

    @operator_only
    async def import_confirm(self, server_id: str, path: str, project_id: str, actor: Actor) -> list[PackProject]:
        """The admin confirms that a jar is a Modrinth project: the project joins the bundle as the
        admin's, and the jar is claimed by it, to be replaced by the build's version."""
        _, bundle, scan = await self._import_context(server_id)
        file = self._scanned(scan, path)
        await self._add_projects(str(bundle.id), [project_id])
        await self.modpackDB.bundle_claim_add(str(bundle.id), ClaimedFile(sha512=file.sha512 or "", project_id=project_id))
        await self.audit.record(actor, "server.import.confirm", _target(server_id),
                                {"bundle_id": str(bundle.id), "path": path, "project_id": project_id})
        return (await self.modpackDB.bundle_get(str(bundle.id))).projects

    @operator_only
    async def import_local(self, server_id: str, path: str, actor: Actor) -> list[LocalFile]:
        """The admin keeps a jar as a local file of the bundle, untouched by updates."""
        _, bundle, scan = await self._import_context(server_id)
        files = await self.modpackDB.bundle_local_file_add(str(bundle.id), _local(self._scanned(scan, path)))
        await self.audit.record(actor, "server.import.local", _target(server_id),
                                {"bundle_id": str(bundle.id), "path": path})
        return files

def _target(server_id: str) -> AuditTarget:
    return AuditTarget(kind="server", id=server_id)

def _local(file: ScanFile) -> LocalFile:
    return LocalFile(filename=file.path.rsplit("/", 1)[-1], sha512=file.sha512 or "", sha1=file.sha1 or "", size=file.size)
